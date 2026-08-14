"""Injeção de dependências dos routers.

Tudo que os endpoints usam do mundo externo passa por aqui, para que o teste
substitua com `app.dependency_overrides` em vez de monkeypatch: sessão de banco,
registro de execuções e as dependências do grafo.

`NodeDeps` é construído **uma vez por processo** e não por requisição. O
`NIMClient` reaproveita backends por (modelo, temperatura), o `HttpFetcher`
mantém pool de conexões e cache em disco, e o `HybridRetriever` carrega o índice
BM25 do disco na construção — recriar tudo isso a cada `POST /searches` jogaria
fora o trabalho e reabriria as conexões.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from api.runs import RunRegistry, registry
from radar.graph.nodes import NodeDeps, build_deps
from radar.persistence.db import get_session_factory

_deps: NodeDeps | None = None


def get_session() -> Iterator[Session]:
    """Sessão por requisição, sempre fechada.

    Sem commit automático: os endpoints de leitura não escrevem, e os que
    escrevem usam os repositórios dentro de uma transação explícita.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_registry() -> RunRegistry:
    return registry


def get_node_deps() -> NodeDeps:
    """Dependências do grafo, memoizadas no processo."""
    global _deps
    if _deps is None:
        _deps = build_deps()
    return _deps


def set_node_deps(deps: NodeDeps | None) -> None:
    """Usado pelo lifespan e pelos testes. `None` força reconstrução."""
    global _deps
    _deps = deps


#: Aliases anotados, no estilo atual do FastAPI. Além de eliminarem a chamada em
#: default de argumento — que o bugbear acusa com razão fora do FastAPI —, deixam
#: a assinatura do endpoint legível: o tipo diz o que é, e não como é resolvido.
SessionDep = Annotated[Session, Depends(get_session)]
RegistryDep = Annotated[RunRegistry, Depends(get_registry)]
NodeDepsDep = Annotated[NodeDeps, Depends(get_node_deps)]
