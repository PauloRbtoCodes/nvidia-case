"""Ponto de entrada de linha de comando. É o que o Makefile chama.

Três subcomandos, na ordem em que se usa o projeto:

- `check` — diz o que falta antes de qualquer coisa custar dinheiro ou tempo.
- `ingest` — popula a base de conhecimento NVIDIA (Qdrant + índice BM25).
- `run` — executa o grafo sobre uma consulta e grava os briefings.

Regra de desenho deste módulo: **degradar com aviso, nunca em silêncio**. Sem
Postgres no ar, `run` grava o markdown e avisa que não persistiu; sem base
ingerida, avisa que as recomendações sairão bloqueadas. Um comando que falha
inteiro porque uma dependência opcional não subiu impede até o diagnóstico
parcial, que é o que o grafo foi desenhado para entregar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from radar.config import PROJECT_ROOT, get_settings

#: Onde `run` grava os briefings. Fora de `src/` porque é saída, não código.
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "briefings"

OK = "  ok  "
AVISO = " aviso"
FALHA = " falha"


def _linha(status: str, item: str, detalhe: str = "") -> None:
    print(f"[{status}] {item}" + (f" — {detalhe}" if detalhe else ""))


def _slug(nome: str) -> str:
    sem_acento = "".join(
        ch
        for ch in unicodedata.normalize("NFD", nome.casefold())
        if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", "-", sem_acento).strip("-") or "empresa"


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def cmd_check(args: argparse.Namespace) -> int:
    """Inventário do ambiente. Sai com código 1 só quando algo é bloqueante.

    A distinção entre bloqueante e aviso é o ponto do comando: falta de
    `COHERE_API_KEY` degrada o rerank e merece aviso; `weights.yaml` inválido
    invalida todo score já gravado e precisa parar a execução.
    """
    settings = get_settings()
    bloqueantes = 0

    faltando = settings.missing_required_keys()
    for chave in ("NVIDIA_API_KEY", "COHERE_API_KEY", "TAVILY_API_KEY"):
        if chave not in faltando:
            _linha(OK, chave)
        elif chave == "NVIDIA_API_KEY":
            # Sem NIM não há extração, classificação, score nem briefing.
            _linha(FALHA, chave, "ausente — nenhum agente consegue rodar")
            bloqueantes += 1
        else:
            degradacao = {
                "COHERE_API_KEY": "rerank cai para a ordem do RRF",
                "TAVILY_API_KEY": "descoberta indisponível; só roda com URLs semente",
            }[chave]
            _linha(AVISO, chave, f"ausente — {degradacao}")

    # Pesos: um YAML inválido aqui corrompe todo score futuro.
    try:
        from radar.scoring.weights import load_weights

        pesos = load_weights()
        _linha(OK, "scoring/weights.yaml", f"versão {pesos.version}")
    except Exception as exc:  # noqa: BLE001 - queremos a mensagem, não o traceback
        _linha(FALHA, "scoring/weights.yaml", str(exc)[:200])
        bloqueantes += 1

    # Prompts: um prompt ausente só apareceria no meio da execução do grafo.
    try:
        from radar.llm.model_registry import LLMTask
        from radar.llm.registry import get_prompt_registry

        registro = get_prompt_registry()
        ausentes = [t.value for t in LLMTask if t.value not in registro.list_prompts()]
        if ausentes:
            _linha(FALHA, "prompts", f"sem prompt para: {', '.join(ausentes)}")
            bloqueantes += 1
        else:
            _linha(OK, "prompts", f"{len(registro.list_prompts())} agentes")
    except Exception as exc:  # noqa: BLE001
        _linha(FALHA, "prompts", str(exc)[:200])
        bloqueantes += 1

    # Postgres.
    try:
        from radar.persistence.db import healthcheck

        if healthcheck():
            _linha(OK, "postgres", settings.database_url.split("@")[-1])
        else:
            _linha(AVISO, "postgres", "fora do ar — `run` grava markdown sem persistir")
    except Exception as exc:  # noqa: BLE001
        _linha(AVISO, "postgres", str(exc)[:120])

    # Qdrant e índice lexical: sem eles o RAG não responde e o guardrail de
    # citação bloqueia toda recomendação — o que é correto, mas inútil.
    try:
        from radar.rag.store import build_knowledge_base

        kb = build_knowledge_base()
        if kb.client.collection_exists(kb.collection):
            _linha(OK, "qdrant", f"coleção '{kb.collection}'")
        else:
            _linha(AVISO, "qdrant", f"coleção '{kb.collection}' não existe — rode `make ingest`")
    except Exception as exc:  # noqa: BLE001
        _linha(AVISO, "qdrant", f"{str(exc)[:120]} — rode `make up`")

    try:
        from radar.rag.bm25 import BM25Index

        indice = BM25Index.load()
        if len(indice):
            _linha(OK, "índice BM25", f"{len(indice)} chunks")
        else:
            _linha(AVISO, "índice BM25", "vazio — rode `make ingest`")
    except Exception as exc:  # noqa: BLE001
        _linha(AVISO, "índice BM25", str(exc)[:120])

    # Cards manuais: sem eles o RAG responde "o que é o Triton" e falha em
    # "qual tecnologia para esta startup". É a armadilha documentada no CLAUDE.md.
    #
    # Contamos por `load_cards` e não por `glob`: o que interessa é quantos serão
    # de fato ingeridos — o guia de escrita do diretório não conta.
    try:
        from radar.models.scoring import AXIS_TO_NVIDIA_FAMILY
        from radar.rag.ingest import load_cards

        cards = load_cards()
        cobertas = {c.source.technology for c in cards}
        descobertas = sorted(
            {t for familia in AXIS_TO_NVIDIA_FAMILY.values() for t in familia} - cobertas
        )
        if not cards:
            _linha(
                AVISO,
                "cards de recomendação",
                "data/nvidia_cards/ vazio — o RAG explica o produto, não indica quando usá-lo",
            )
        elif descobertas:
            # Gate libera a tecnologia, busca não tem o que devolver, guardrail
            # bloqueia — e nada na execução explica a causa.
            _linha(
                AVISO,
                "cards de recomendação",
                f"{len(cards)} cards, mas sem card para: {', '.join(descobertas)}",
            )
        else:
            _linha(OK, "cards de recomendação", f"{len(cards)} cards, todo o gate coberto")
    except Exception as exc:  # noqa: BLE001
        _linha(AVISO, "cards de recomendação", str(exc)[:160])

    print()
    if bloqueantes:
        print(f"{bloqueantes} problema(s) bloqueante(s). Corrija antes de rodar o grafo.")
        return 1
    print("Ambiente utilizável.")
    return 0


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #
def cmd_ingest(args: argparse.Namespace) -> int:
    from radar.rag.bm25 import BM25Index
    from radar.rag.embed import build_embedding_client
    from radar.rag.ingest import DEFAULT_CARDS_DIR, ingest_knowledge_base
    from radar.rag.store import build_knowledge_base

    embedder = build_embedding_client()
    relatorio = ingest_knowledge_base(
        knowledge_base=build_knowledge_base(vector_size=embedder.dimension),
        embedder=embedder,
        bm25_index=BM25Index.load(),
        sources_path=args.sources,
        cards_dir=args.cards or DEFAULT_CARDS_DIR,
        skip_existing=not args.force,
    )

    print(
        f"documentos: {relatorio.documents_ok} ok, {relatorio.documents_failed} falharam · "
        f"cards: {relatorio.cards_ingested}"
    )
    print(
        f"chunks: {relatorio.chunks_total} no total, {relatorio.chunks_new} novos, "
        f"{relatorio.chunks_skipped} já indexados"
    )
    for url in relatorio.failed_urls:
        _linha(AVISO, "não extraído", url)

    if not relatorio.cards_ingested:
        _linha(
            AVISO,
            "sem cards manuais",
            "o Entregável 4 depende deles: a documentação diz o que a tecnologia faz, "
            "nunca quando recomendá-la",
        )
    return 0 if not relatorio.is_empty else 1


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _persistir(resultados: Sequence[dict[str, Any]]) -> tuple[int, str | None]:
    """Grava o lote no Postgres. Devolve quantas empresas entraram e o erro, se houver.

    Cada empresa entra na sua própria transação: um perfil que viole uma
    constraint não pode levar junto as outras onze que estavam corretas.
    """
    from radar.persistence.db import healthcheck, session_scope
    from radar.persistence.repositories import (
        BriefingRepository,
        ClassificationRepository,
        CompanyRepository,
        EvidenceRepository,
        ScoreRepository,
        collect_evidences,
    )

    if not healthcheck():
        return 0, "banco inacessível"

    gravadas = 0
    for estado in resultados:
        profile = estado.get("profile")
        if profile is None:
            continue
        try:
            with session_scope() as sessao:
                empresa = CompanyRepository.upsert(sessao, profile)
                EvidenceRepository.bulk_upsert(sessao, empresa.id, collect_evidences(profile))

                classificacao = estado.get("classification")
                if classificacao is not None:
                    ClassificationRepository.add(sessao, empresa.id, classificacao)

                briefing = estado.get("briefing")
                score = estado.get("defensibility")
                if briefing is not None:
                    BriefingRepository.save(sessao, empresa.id, briefing)
                elif score is not None:
                    ScoreRepository.add(sessao, empresa.id, score, estado.get("priority"))
            gravadas += 1
        except Exception as exc:  # noqa: BLE001 - uma empresa não derruba o lote
            _linha(AVISO, f"não persistida: {profile.name}", str(exc)[:160])

    return gravadas, None


def _gravar_markdown(briefings: Sequence[Any], destino: Path) -> list[Path]:
    destino.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(UTC).strftime("%Y%m%d")
    escritos: list[Path] = []
    for briefing in briefings:
        caminho = destino / f"{carimbo}-{_slug(briefing.company_name)}.md"
        caminho.write_text(briefing.markdown or "", encoding="utf-8")
        escritos.append(caminho)
    return escritos


async def _executar_grafo(query: str, max_companies: int) -> dict[str, Any]:
    from radar.graph.build import build_radar_graph
    from radar.graph.nodes import build_deps

    deps = build_deps()
    if deps.search is None:
        _linha(AVISO, "descoberta", "sem TAVILY_API_KEY — a busca não vai retornar nada")
    if deps.retriever is None:
        _linha(
            AVISO,
            "base NVIDIA",
            "indisponível — as recomendações serão bloqueadas pelo guardrail de citação",
        )

    grafo = build_radar_graph(deps)
    try:
        return await grafo.ainvoke({"query": query, "max_companies": max_companies})
    finally:
        if deps.fetcher is not None:
            await deps.fetcher.aclose()


def cmd_run(args: argparse.Namespace) -> int:
    query = (args.query or "").strip()
    if not query:
        print("Informe a consulta: make run Q='startups de IA em saude'", file=sys.stderr)
        return 2

    estado = asyncio.run(_executar_grafo(query, args.max_companies))

    briefings = estado.get("briefings") or []
    fila = estado.get("queue") or []
    ordem = {nome: i for i, nome in enumerate(fila)}
    briefings = sorted(briefings, key=lambda b: ordem.get(b.company_name, len(fila)))

    print()
    print(f"Fila de prioridade ({len(briefings)} empresas)")
    print("-" * 72)
    for posicao, briefing in enumerate(briefings, start=1):
        score = briefing.defensibility
        print(
            f"{posicao:2}. {briefing.company_name:<32} "
            f"urgência {briefing.priority.urgency:5.1f} · "
            f"risco {score.commoditization_risk:5.1f} · "
            f"confiança {score.global_confidence:.2f} · "
            f"{briefing.priority.bucket.value}"
        )

    for descartada in estado.get("skipped") or []:
        _linha(AVISO, f"descartada: {descartada['company']}", descartada.get("reason", ""))
    for falha in estado.get("failures") or []:
        marca = AVISO if falha.get("recoverable") else FALHA
        _linha(marca, f"{falha['node']} ({falha.get('company') or 'lote'})", falha["error"][:160])

    if briefings:
        escritos = _gravar_markdown(briefings, Path(args.output))
        print(f"\n{len(escritos)} briefing(s) em {Path(args.output)}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {
                    "query": query,
                    "queue": fila,
                    "briefings": [b.model_dump(mode="json") for b in briefings],
                    "skipped": estado.get("skipped") or [],
                    "failures": estado.get("failures") or [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"estado completo em {args.json}")

    if not args.no_persist:
        gravadas, erro = _persistir(estado.get("company_results") or [])
        if erro:
            _linha(AVISO, "persistência", f"{erro} — os markdowns acima seguem válidos")
        else:
            _linha(OK, "persistência", f"{gravadas} empresa(s) no Postgres")

    return 0 if briefings else 1


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="radar",
        description="NVIDIA Startup AI Radar — descoberta, diagnóstico e recomendação.",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    checar = sub.add_parser("check", help="Valida .env, pesos, prompts e infraestrutura.")
    checar.set_defaults(func=cmd_check)

    ingerir = sub.add_parser("ingest", help="Ingere a base de conhecimento NVIDIA.")
    ingerir.add_argument(
        "--sources",
        default=str(PROJECT_ROOT / "data" / "nvidia_sources.yaml"),
        help="YAML com as URLs da KB.",
    )
    ingerir.add_argument("--cards", default=None, help="Diretório dos cards manuais.")
    ingerir.add_argument(
        "--force",
        action="store_true",
        help="Reembeda o que já está indexado (gasta cota do NIM).",
    )
    ingerir.set_defaults(func=cmd_ingest)

    executar = sub.add_parser("run", help="Executa o grafo sobre uma consulta.")
    executar.add_argument("--query", "-q", required=True, help="Consulta em linguagem natural.")
    executar.add_argument("--max-companies", type=int, default=12)
    executar.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    executar.add_argument("--json", default=None, help="Grava o estado completo neste arquivo.")
    executar.add_argument(
        "--no-persist", action="store_true", help="Não grava no Postgres."
    )
    executar.set_defaults(func=cmd_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
