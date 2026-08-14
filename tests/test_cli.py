"""Testes da CLI — o contrato entre o Makefile e o código.

O que importa aqui não é o texto impresso, é o **código de saída** e o
comportamento de degradação. `make check` faz parte do onboarding do projeto: se
ele passar com `NVIDIA_API_KEY` ausente, alguém vai rodar `make run` e descobrir
o problema depois de gastar uma busca; se ele falhar porque o Qdrant não subiu,
alguém vai achar que o projeto não roda sem Docker — e roda, com aviso.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from radar import cli
from radar.models.company import AIMaturity
from radar.models.recommendation import Briefing
from radar.models.scoring import (
    AxisScore,
    DefensibilityAxis,
    DefensibilityScore,
    PriorityAssessment,
    PriorityBucket,
)


@pytest.fixture(autouse=True)
def _sem_infra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste desta suíte abre conexão — nem para levar recusa.

    Os dois pontos que tocariam a rede são o `healthcheck` do Postgres e a
    fábrica do Qdrant. Ambos entram simulando infra ausente, que é justamente o
    cenário que estes testes verificam.
    """
    monkeypatch.setattr(
        "radar.persistence.db.healthcheck", lambda *a, **k: False, raising=False
    )

    def _sem_qdrant(*_: Any, **__: Any):
        raise ConnectionError("[Errno 111] Connection refused")

    monkeypatch.setattr("radar.rag.store.build_knowledge_base", _sem_qdrant, raising=False)


def _briefing(nome: str, urgencia: float) -> Briefing:
    score = DefensibilityScore(
        company_name=nome,
        weights_version="teste",
        axes=[
            AxisScore(axis=eixo, score=40.0, confidence=0.7, rationale="teste")
            for eixo in DefensibilityAxis
        ],
    )
    return Briefing(
        company_name=nome,
        executive_summary="resumo",
        maturity=AIMaturity.AI_NATIVE,
        defensibility=score,
        priority=PriorityAssessment(
            bucket=PriorityBucket.ABORDAR_AGORA,
            urgency=urgencia,
            capacity_to_act=0.7,
            capacity_rationale="teste",
            recommended_next_step="teste",
        ),
        moat_plan="plano",
        markdown=f"# {nome}\n",
    )


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
def test_check_falha_sem_chave_do_nim(monkeypatch: pytest.MonkeyPatch, capsys: Any):
    """Sem NIM nenhum agente roda — é bloqueante, não aviso."""
    monkeypatch.setattr(
        cli, "get_settings", lambda: _settings(nvidia="", cohere="c", tavily="t")
    )
    assert cli.main(["check"]) == 1
    assert "NVIDIA_API_KEY" in capsys.readouterr().out


def test_check_passa_sem_docker_porque_infra_e_degradavel(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
):
    """Qdrant e Postgres fora do ar são aviso: o projeto roda sem eles, com menos."""
    monkeypatch.setattr(
        cli, "get_settings", lambda: _settings(nvidia="n", cohere="c", tavily="t")
    )
    assert cli.main(["check"]) == 0

    saida = capsys.readouterr().out
    assert "qdrant" in saida
    assert "Ambiente utilizável" in saida


def _settings(*, nvidia: str, cohere: str, tavily: str):
    from radar.config import Settings

    return Settings(nvidia_api_key=nvidia, cohere_api_key=cohere, tavily_api_key=tavily)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def test_run_exige_consulta(capsys: Any):
    assert cli.main(["run", "--query", "   "]) == 2


def test_run_grava_markdown_na_ordem_da_fila(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
):
    estado = {
        "briefings": [_briefing("Baixa Urgencia", 10.0), _briefing("Alta Urgencia", 90.0)],
        "queue": ["Alta Urgencia", "Baixa Urgencia"],
        "company_results": [],
        "skipped": [],
        "failures": [],
    }

    async def _fake(query: str, max_companies: int) -> dict[str, Any]:
        return estado

    monkeypatch.setattr(cli, "_executar_grafo", _fake)

    codigo = cli.main(
        ["run", "-q", "startups de saude", "--output", str(tmp_path), "--no-persist"]
    )
    assert codigo == 0

    arquivos = sorted(p.name for p in tmp_path.glob("*.md"))
    assert len(arquivos) == 2
    assert any("alta-urgencia" in nome for nome in arquivos)

    # A fila impressa respeita a ordenação do grafo, não a ordem de chegada.
    linhas = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith((" 1.", " 2."))]
    assert "Alta Urgencia" in linhas[0]
    assert "Baixa Urgencia" in linhas[1]


def test_run_sem_briefing_sai_com_codigo_de_erro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
):
    """Lote que não produziu nada precisa ser detectável em script e em CI."""

    async def _fake(query: str, max_companies: int) -> dict[str, Any]:
        return {
            "briefings": [],
            "queue": [],
            "company_results": [],
            "skipped": [{"company": "Acme", "reason": "classificada como non_ai"}],
            "failures": [],
        }

    monkeypatch.setattr(cli, "_executar_grafo", _fake)
    assert cli.main(["run", "-q", "x", "--output", str(tmp_path), "--no-persist"]) == 1
    assert "descartada: Acme" in capsys.readouterr().out


def test_run_grava_json_completo_quando_pedido(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
):
    async def _fake(query: str, max_companies: int) -> dict[str, Any]:
        return {
            "briefings": [_briefing("Acme", 50.0)],
            "queue": ["Acme"],
            "company_results": [],
            "skipped": [],
            "failures": [],
        }

    monkeypatch.setattr(cli, "_executar_grafo", _fake)
    destino = tmp_path / "estado.json"
    cli.main(
        [
            "run",
            "-q",
            "x",
            "--output",
            str(tmp_path),
            "--json",
            str(destino),
            "--no-persist",
        ]
    )

    import json

    dados = json.loads(destino.read_text(encoding="utf-8"))
    assert dados["queue"] == ["Acme"]
    assert dados["briefings"][0]["company_name"] == "Acme"


def test_persistencia_indisponivel_nao_invalida_os_markdowns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
):
    async def _fake(query: str, max_companies: int) -> dict[str, Any]:
        return {
            "briefings": [_briefing("Acme", 50.0)],
            "queue": ["Acme"],
            "company_results": [{"profile": None}],
            "skipped": [],
            "failures": [],
        }

    monkeypatch.setattr(cli, "_executar_grafo", _fake)
    assert cli.main(["run", "-q", "x", "--output", str(tmp_path)]) == 0

    saida = capsys.readouterr().out
    assert "banco inacessível" in saida
    assert "seguem válidos" in saida
    assert list(tmp_path.glob("*.md"))


def test_slug_de_arquivo_remove_acento_e_pontuacao():
    assert cli._slug("Ação & Saúde Ltda.") == "acao-saude-ltda"
