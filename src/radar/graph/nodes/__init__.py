"""Um arquivo por agente do grafo.

Todo nó é exposto como uma fábrica `make_<nome>(deps)` que devolve a corrotina
registrada no `StateGraph`. A indireção existe para que a dependência entre no
nó por closure: `StateGraph.add_node` chama a função apenas com o estado, e sem
a fábrica cada nó teria que descobrir sozinho como construir cliente de LLM,
scraper e retriever — o que tornaria o grafo impossível de testar sem rede.
"""

from radar.graph.nodes.base import falha
from radar.graph.nodes.briefing import make_write_briefing, render_markdown
from radar.graph.nodes.classifier import make_classify_company
from radar.graph.nodes.collector import make_collect_sources
from radar.graph.nodes.deps import NodeDeps, build_deps
from radar.graph.nodes.discovery import make_discover_companies
from radar.graph.nodes.extractor import make_extract_profile, podar_nao_literais
from radar.graph.nodes.planner import make_plan_search
from radar.graph.nodes.recommender import make_recommend_technologies, sanear_recomendacoes
from radar.graph.nodes.retriever import make_retrieve_kb
from radar.graph.nodes.scorer import make_score_defensibility
from radar.graph.nodes.validator import make_validate_evidence, precisa_recoletar

__all__ = [
    "NodeDeps",
    "build_deps",
    "falha",
    "make_classify_company",
    "make_collect_sources",
    "make_discover_companies",
    "make_extract_profile",
    "make_plan_search",
    "make_recommend_technologies",
    "make_retrieve_kb",
    "make_score_defensibility",
    "make_validate_evidence",
    "make_write_briefing",
    "podar_nao_literais",
    "precisa_recoletar",
    "render_markdown",
    "sanear_recomendacoes",
]
