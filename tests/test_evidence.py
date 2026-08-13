"""Testes da camada de rastreabilidade.

A regra estrutural do projeto — nenhuma afirmação sem evidência — só vale se o
código a impuser. Estes testes verificam que ela é mecânica, não convenção.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from radar.models.company import InferenceProvider
from radar.models.evidence import Evidence, EvidenceBackedField, SourceKind
from radar.models.recommendation import Complexity, Priority, Recommendation, RetrievedChunk
from radar.models.scoring import DefensibilityAxis

TRECHO = "Nossa plataforma usa modelos proprietarios treinados com dados de clientes."


def _ev(kind: SourceKind = SourceKind.SITE_OFICIAL, age_days: int | None = None) -> Evidence:
    published = datetime.now(UTC) - timedelta(days=age_days) if age_days is not None else None
    return Evidence(
        url="https://exemplo.com.br/sobre",
        kind=kind,
        excerpt=TRECHO,
        published_at=published,
    )


def test_trecho_curto_demais_e_rejeitado():
    """Fragmento sem contexto não prova nada e polui o briefing."""
    with pytest.raises(ValidationError):
        Evidence(url="https://exemplo.com.br", kind=SourceKind.NOTICIA, excerpt="usa IA")


def test_blog_tecnico_vale_mais_que_noticia():
    """Portal repete release; blog de engenharia descreve a stack de verdade."""
    assert _ev(SourceKind.BLOG_TECNICO).trust > _ev(SourceKind.NOTICIA).trust


def test_evidencia_antiga_perde_peso():
    """Sinal técnico envelhece: quem usava API em 2023 pode ter migrado."""
    recente = _ev(SourceKind.SITE_OFICIAL, age_days=30)
    antiga = _ev(SourceKind.SITE_OFICIAL, age_days=900)
    assert antiga.trust < recente.trust * 0.6


def test_evidencia_sem_data_nao_e_penalizada():
    """Sem data não temos base para penalizar — assumir obsolescência seria inventar."""
    assert _ev(SourceKind.SITE_OFICIAL).trust == pytest.approx(0.85)


def test_campo_sem_evidencia_tem_confianca_zero():
    campo = EvidenceBackedField[InferenceProvider](value=InferenceProvider.API_EXTERNA)
    assert campo.confidence == 0.0
    assert not campo.is_grounded


def test_fontes_independentes_reforcam_com_retorno_decrescente():
    """Duas fontes valem mais que uma, mas três fracas não superam uma forte."""
    uma = EvidenceBackedField[str](value="saude", evidences=[_ev(SourceKind.SITE_OFICIAL)])
    duas = EvidenceBackedField[str](
        value="saude", evidences=[_ev(SourceKind.SITE_OFICIAL), _ev(SourceKind.NOTICIA)]
    )
    assert duas.confidence > uma.confidence
    assert duas.confidence < 1.0

    tres_fracas = EvidenceBackedField[str](
        value="saude", evidences=[_ev(SourceKind.OUTRO) for _ in range(3)]
    )
    uma_forte = EvidenceBackedField[str](value="saude", evidences=[_ev(SourceKind.BLOG_TECNICO)])
    assert tres_fracas.confidence < uma_forte.confidence


def test_hash_deduplica_mesmo_trecho():
    assert _ev().content_hash == _ev(age_days=10).content_hash


def test_recomendacao_sem_citacao_e_bloqueada():
    """Guardrail central do Entregável 4.

    Alucinação sobre o que o Triton faz, diante de um founder técnico, custa a
    credibilidade do programa inteiro. Melhor recomendar menos.
    """
    with pytest.raises(ValidationError, match="sem citação"):
        Recommendation(
            technology="Triton Inference Server",
            addresses_axis=DefensibilityAxis.STACK_OWNERSHIP,
            technical_rationale="serve modelos",
            business_rationale="reduz custo",
            priority=Priority.ALTA,
            complexity=Complexity.MEDIA,
            next_action="agendar deep dive tecnico",
        )


def test_recomendacao_com_citacao_e_aceita():
    rec = Recommendation(
        technology="Triton Inference Server",
        addresses_axis=DefensibilityAxis.STACK_OWNERSHIP,
        technical_rationale="serve multiplos modelos com batching dinamico",
        business_rationale="reduz custo por requisicao em carga concorrente",
        priority=Priority.ALTA,
        complexity=Complexity.MEDIA,
        next_action="benchmark de latencia p95 contra o endpoint atual",
        kb_citations=[
            RetrievedChunk(
                text="Triton suporta batching dinamico e execucao concorrente de modelos.",
                source_url="https://developer.nvidia.com/triton-inference-server",
                technology="Triton Inference Server",
                rerank_score=0.91,
            )
        ],
    )
    assert rec.kb_citations[0].rerank_score == 0.91
