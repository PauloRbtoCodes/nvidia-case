"""Descoberta: plano de busca → lista de empresas candidatas.

Segundo nó do grafo externo. A saída aqui é o que o `Send` distribui para os
subgrafos, então o custo de um falso positivo é uma empresa inteira processada
à toa — daí o filtro por domínio e a deduplicação antes do fan-out.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

import structlog

from radar.graph.nodes.base import node_guard
from radar.graph.nodes.deps import NodeDeps
from radar.graph.state import RadarState
from radar.scraping.search import SearchCandidate

log = structlog.get_logger(__name__)

#: Resultados por query pedidos à Search API. Acima disso a cauda vira ruído
#: (agregadores, listas de "top 10 startups") e o dedupe por domínio descarta
#: quase tudo mesmo.
RESULTS_PER_QUERY = 6

#: Domínios que hospedam muitas empresas diferentes. Não são candidatos a
#: "empresa", são fontes *sobre* empresas: um perfil no Distrito é evidência,
#: não é a startup. Deixá-los virar candidato produz um subgrafo que tenta
#: diagnosticar o diretório inteiro como se fosse uma companhia.
AGGREGATOR_HINTS: tuple[str, ...] = (
    # Diretórios e mapas de ecossistema
    "distrito.me",
    "startupbase.com.br",
    "abstartups.com.br",
    "crunchbase.com",
    "cubo.network",
    "startups.com.br",
    "100openstartups.com",
    "startse.com",
    # Imprensa de negócios
    "braziljournal.com",
    "baguete.com.br",
    "neofeed.com.br",
    "exame.com",
    "infomoney.com.br",
    "valor.globo.com",
    "g1.globo.com",
    "estadao.com.br",
    "folha.uol.com.br",
    "uol.com.br",
    "terra.com.br",
    "agenciasebrae.com.br",
    "sebrae.com.br",
    "forbes.com.br",
    "canaltech.com.br",
    "tecmundo.com.br",
    "olhardigital.com.br",
    # Plataformas de conteúdo e código
    "medium.com",
    "github.com",
    "substack.com",
    "youtube.com",
    "wikipedia.org",
    # Boards de vaga e redes profissionais
    "linkedin.com",
    "gupy.io",
    "programathor.com.br",
    "trampos.co",
    "glassdoor.com.br",
    "indeed.com",
    "vagas.com.br",
    "catho.com.br",
    "solides.jobs",
    "revelo.com.br",
)

#: Segmentos de caminho que denunciam artigo, vaga ou post — nunca a home de uma
#: empresa. Complementa a lista de domínios, que sozinha nunca fica completa:
#: portais regionais e blogs de nicho aparecem toda semana, e a forma da URL
#: sobrevive melhor que o inventário de domínios.
PATH_HINTS_CONTEUDO: tuple[str, ...] = (
    "/jobs/",
    "/job/",
    "/vaga",
    "/vagas",
    "/carreiras/",
    "/noticia",
    "/noticias",
    "/artigo",
    "/artigos",
    "/blog/",
    "/post/",
    "/posts/",
    "/materia",
    "/reportagem",
    "/entrevista",
    "/coluna",
    "/press-release",
    "/comunicado",
    "/release",
    "/vc/",
    "/vagas-de-emprego",
)

#: Profundidade máxima de caminho para uma URL ser tratada como sede da empresa.
#: A home de uma startup é `empresa.com.br/` ou `empresa.com.br/produto`; um
#: artigo é `portal.com.br/2026/09/09/titulo-longo-da-materia`.
MAX_PATH_SEGMENTS = 2

#: Palavras que denunciam manchete no título do resultado. Nome de empresa é
#: sintagma nominal curto; manchete é oração com verbo.
TITULO_DE_MANCHETE: tuple[str, ...] = (
    " usa ",
    " lança ",
    " lancou ",
    " capta ",
    " recebe ",
    " anuncia ",
    " apostam ",
    " aposta ",
    " cresce ",
    " compra ",
    " adquire ",
    " levanta ",
    " fecha parceria",
    " quer ",
    " vai ",
    "como ",
    "por que ",
    "o que é",
    "os 5 ",
    "os 4 ",
    "os 10 ",
    "melhores ",
    "conheça ",
    "veja ",
    "saiba ",
    "ranking",
    "guia ",
    "lista de",
)


def _e_agregador(domain: str) -> bool:
    return any(domain == hint or domain.endswith(f".{hint}") for hint in AGGREGATOR_HINTS)


def _caminho_de_conteudo(url: str) -> bool:
    """URL de artigo, vaga ou post — evidência sobre uma empresa, não a empresa."""
    caminho = urlsplit(url).path.lower()
    if any(hint in caminho for hint in PATH_HINTS_CONTEUDO):
        return True
    segmentos = [s for s in caminho.split("/") if s]
    if len(segmentos) > MAX_PATH_SEGMENTS:
        return True
    # `/2026/09/materia` — data no caminho é assinatura de portal de notícia.
    return any(re.fullmatch(r"(19|20)\d{2}", s) for s in segmentos)


def _titulo_de_manchete(titulo: str) -> bool:
    baixo = f" {titulo.lower().strip()} "
    return any(marca in baixo for marca in TITULO_DE_MANCHETE)


def _parece_empresa(candidate: SearchCandidate) -> tuple[bool, str]:
    """Porteiro determinístico entre "isto é uma empresa" e "isto fala de empresas".

    Existe porque o planner emite três famílias de query — descoberta, sinais de
    stack e tração — e **só a primeira produz candidatos**. As outras duas
    procuram evidência *sobre* empresas: vaga de emprego e matéria de portal. O
    nó tratava todas igual, e o resultado observado na primeira execução real foi
    uma fila com "Finep Mais Inovação" (uma vaga do LinkedIn) e "Os 4 setores
    mais promissores para uso de IA no Brasil" (uma manchete) no lugar de
    startups.

    Determinístico e não via LLM de propósito: roda sobre dezenas de resultados
    por lote, o erro é barato de auditar lendo uma URL, e gastar cota para
    decidir se `linkedin.com/jobs/view/...` é uma empresa seria absurdo.
    """
    if _e_agregador(candidate.domain):
        return False, "agregador"
    if _caminho_de_conteudo(candidate.url):
        return False, "caminho_de_conteudo"
    if _titulo_de_manchete(candidate.title):
        return False, "titulo_de_manchete"
    return True, ""


def _nome_provavel(candidate: SearchCandidate) -> str:
    """Nome de trabalho da empresa, refinado depois pelo Extractor.

    O título de resultado de busca costuma ser "Acme | Plataforma de X"; o
    primeiro segmento antes do separador é quase sempre o nome. Quando não há
    título, o domínio sem TLD serve — e o Extractor corrige a partir do site.
    """
    titulo = candidate.title.strip()
    for separador in ("|", "–", "—", "-", ":", "·"):
        if separador in titulo:
            titulo = titulo.split(separador)[0].strip()
            break
    if len(titulo) >= 2:
        return titulo[:80]
    return candidate.domain.split(".")[0].capitalize()


def make_discover_companies(
    deps: NodeDeps,
) -> Callable[[RadarState], Coroutine[Any, Any, dict[str, Any]]]:
    @node_guard("discovery", company_key=None)
    async def discover_companies(state: RadarState) -> dict[str, Any]:
        plano = state.get("search_plan") or {}
        queries: list[str] = list(plano.get("queries") or [])
        limite = plano.get("max_companies") or state.get("max_companies") or 12

        if deps.search is None:
            raise RuntimeError(
                "Search API não configurada (TAVILY_API_KEY ausente). Rode o grafo "
                "com `seed_urls` explícitas ou configure a chave."
            )
        if not queries:
            raise ValueError("Plano de busca sem queries.")

        candidatos = await deps.search.search(
            queries, max_results=RESULTS_PER_QUERY, per_domain=1
        )

        descobertos: list[dict[str, Any]] = []
        recusados: dict[str, int] = {}
        for candidato in candidatos:
            ok, motivo = _parece_empresa(candidato)
            if not ok:
                recusados[motivo] = recusados.get(motivo, 0) + 1
                log.debug("candidato_recusado", url=candidato.url, motivo=motivo)
                continue
            descobertos.append(
                {
                    "url": candidato.url,
                    "domain": candidato.domain,
                    "title": candidato.title,
                    "snippet": candidato.snippet,
                    "query": candidato.query,
                    "company_name": _nome_provavel(candidato),
                }
            )
            if len(descobertos) >= limite:
                break

        log.info(
            "descoberta",
            queries=len(queries),
            candidatos=len(candidatos),
            selecionados=len(descobertos),
            recusados=recusados or None,
        )
        if candidatos and not descobertos:
            # Recusar tudo é resultado legítimo (o lote pode ter caído inteiro em
            # notícia), mas silenciar isso faria o operador ler "nenhuma startup
            # encontrada" quando o correto é "as queries trouxeram a coisa errada".
            log.warning(
                "descoberta_sem_candidatos",
                motivos=recusados,
                acao="revisar as queries de descoberta do plano",
            )
        return {"discovered": descobertos}

    return discover_companies
