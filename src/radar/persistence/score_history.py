"""Adaptador de `ScoreHistoryPort` sobre o Postgres.

Existe para que o nó `compare` do grafo leia o score da execução anterior sem
que `graph/` importe `persistence/`. A dependência entra invertida, por uma
porta declarada em `radar.graph.nodes.deps.ScoreHistoryPort`.

Abre uma sessão curta por consulta, como o `sink`: o nó roda dentro do fan-out
`Send`, e segurar uma sessão aberta por toda a duração do subgrafo de cada
empresa multiplicaria conexões pelo tamanho do lote.
"""

from __future__ import annotations

import structlog

from radar.models.scoring import DefensibilityScore
from radar.persistence import db
from radar.persistence.repositories import CompanyRepository, ScoreRepository

log = structlog.get_logger(__name__)


class DbScoreHistory:
    """`ScoreHistoryPort` real. Degrada para `None` quando o banco está fora do ar.

    A resolução da empresa é por nome normalizado (`CompanyRepository.get_by_name`),
    a mesma chave fraca de dedupe usada no resto do sistema: uma execução
    anterior gravada sob um nome levemente diferente e sem domínio em comum não
    será encontrada, e o diff daquela empresa aparece como primeira execução. É a
    limitação conhecida da identidade por nome, não um caminho de erro.
    """

    def previous_score(self, company_name: str) -> DefensibilityScore | None:
        if not db.healthcheck():
            log.warning("historico_indisponivel", empresa=company_name)
            return None
        with db.session_scope() as session:
            company = CompanyRepository.get_by_name(session, company_name)
            if company is None:
                return None
            return ScoreRepository.latest(session, company.id)

    def seen_before(self, company_name: str) -> bool:
        """Só a linha de score, sem hidratar o modelo nem o mapa de evidências.

        Banco fora do ar devolve `False`: na dúvida, tratar como primeira passada
        significa confiar no cache, que é o comportamento mais barato — o oposto
        (forçar rede em todo lote quando o Postgres pisca) sairia caro sem motivo.
        """
        if not db.healthcheck():
            return False
        with db.session_scope() as session:
            company = CompanyRepository.get_by_name(session, company_name)
            if company is None:
                return False
            return ScoreRepository.latest_row(session, company.id) is not None
