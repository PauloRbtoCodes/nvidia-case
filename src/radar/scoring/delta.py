"""Gatilho temporal: o que separa radar de foto.

O resto do sistema roda um lote, produz um diagnóstico, e termina. Nada aqui
compara duas execuções — mas o Postgres já guarda a série inteira
(`DefensibilityScoreRepository.add` é append-only, com `history()` pronto desde
o dia 1). Este módulo é a peça que faltava: comparar o score de hoje com o mais
recente anterior e dizer **o que mudou, e por que a conversa é agora**.

Determinístico e sem LLM, pelo mesmo motivo de `priority.py`: o delta alimenta
uma fila que um gerente confia de olho fechado, e "por que essa empresa subiu"
precisa ter resposta auditável em uma linha, não uma explicação que varia entre
execuções sobre os mesmos dados.

O invariante que este módulo existe para não quebrar (ADR 0002, estendido ao
eixo do tempo): **ausência de evidência numa execução posterior nunca é prova de
que o sinal deixou de existir.** Pode ser scraping falho, página fora do ar,
layout mudado — o mundo é hostil, e um site que não respondeu hoje não significa
que a startup regrediu. Por isso o diff nunca deriva `regressao` de contagem de
evidência caindo: só de score caindo com confiança mantida ou subindo. Sumiço de
evidência aparece como queda de confiança, nunca como queda de score.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

from radar.models.evidence import Evidence
from radar.models.scoring import AxisScore, DefensibilityAxis, DefensibilityScore

#: Abaixo disso a variação de score é ruído de calibração do LLM (temperatura >
#: 0 nas tarefas de raciocínio), não mudança real na empresa. Calibrado para ser
#: maior que a variação observada entre duas chamadas idênticas em produção.
SCORE_NOISE_FLOOR = 3.0

#: Idem, para confiança.
CONFIDENCE_NOISE_FLOOR = 0.05


class ChangeKind(StrEnum):
    """O que aconteceu com um eixo entre duas execuções."""

    NOVA_EVIDENCIA = "nova_evidencia"
    """Apareceu evidência que não existia antes — é o que vira a frase do talk
    track ("vi que vocês abriram vaga de MLOps"). O sinal mais valioso do diff."""

    MELHOROU = "melhorou"
    """Score subiu além do ruído, com confiança igual ou maior."""

    PIOROU = "piorou"
    """Score caiu além do ruído, com confiança igual ou maior.

    **Nunca** disparado quando a confiança também caiu — isso seria o diff
    confundindo "paramos de achar evidência" com "a empresa regrediu", exatamente
    a injustiça com aparência de rigor que o ADR 0002 existe para evitar.
    """

    CONFIANCA_CAIU = "confianca_caiu"
    """A evidência que sustentava o score sumiu (ou a nova coleta não a repetiu).
    Sobe como alerta de qualidade de dado, nunca como piora do eixo."""

    ESTAVEL = "estavel"


class AxisDelta(BaseModel):
    """Diferença de um eixo entre duas execuções."""

    axis: DefensibilityAxis
    kind: ChangeKind
    score_before: float
    score_after: float
    confidence_before: float
    confidence_after: float
    new_evidences: list[Evidence] = Field(default_factory=list)

    @computed_field
    @property
    def score_change(self) -> float:
        return round(self.score_after - self.score_before, 1)

    @computed_field
    @property
    def is_actionable(self) -> bool:
        """O que vale aparecer na fila como "mudou". Estável e ruído não valem."""
        return self.kind not in (ChangeKind.ESTAVEL,)


class ScoreDelta(BaseModel):
    """Comparação entre o score mais recente e o anterior de uma empresa."""

    company_name: str
    weights_version_before: str
    weights_version_after: str
    axes: list[AxisDelta]

    @computed_field
    @property
    def has_changes(self) -> bool:
        return any(a.is_actionable for a in self.axes)

    @computed_field
    @property
    def headline_axis(self) -> AxisDelta | None:
        """O eixo que melhor justifica "por que ligar agora".

        Prioridade: nova evidência primeiro — é o que dá ao gerente uma frase
        concreta pra abrir a conversa — depois piora (urgência), depois melhora.
        Estável e confiança-caiu nunca viram manchete.
        """
        ordem = {
            ChangeKind.NOVA_EVIDENCIA: 0,
            ChangeKind.PIOROU: 1,
            ChangeKind.MELHOROU: 2,
            ChangeKind.CONFIANCA_CAIU: 3,
            ChangeKind.ESTAVEL: 4,
        }
        acionaveis = [a for a in self.axes if a.is_actionable]
        if not acionaveis:
            return None
        return min(acionaveis, key=lambda a: ordem[a.kind])


def _novas_evidencias(antes: AxisScore, depois: AxisScore) -> list[Evidence]:
    """Evidências em `depois` cujo hash de conteúdo não existia em `antes`.

    Compara por `content_hash` (url + trecho), não por identidade de objeto: a
    mesma evidência recoletada não é nova, mesmo que `collected_at` tenha mudado.
    """
    vistas = {e.content_hash for e in antes.evidences}
    return [e for e in depois.evidences if e.content_hash not in vistas]


def _comparar_eixo(antes: AxisScore, depois: AxisScore) -> AxisDelta:
    novas = _novas_evidencias(antes, depois)
    delta_score = depois.score - antes.score
    delta_conf = depois.confidence - antes.confidence

    if novas:
        kind = ChangeKind.NOVA_EVIDENCIA
    elif delta_conf < -CONFIDENCE_NOISE_FLOOR and delta_score <= 0:
        # A confiança caiu e o score não melhorou: é sumiço de evidência, não
        # piora real. O invariante do módulo — nunca inferir regressão da
        # ausência de sinal — vive nesta condição.
        kind = ChangeKind.CONFIANCA_CAIU
    elif delta_score > SCORE_NOISE_FLOOR and delta_conf >= -CONFIDENCE_NOISE_FLOOR:
        kind = ChangeKind.MELHOROU
    elif delta_score < -SCORE_NOISE_FLOOR and delta_conf >= -CONFIDENCE_NOISE_FLOOR:
        kind = ChangeKind.PIOROU
    else:
        kind = ChangeKind.ESTAVEL

    return AxisDelta(
        axis=depois.axis,
        kind=kind,
        score_before=antes.score,
        score_after=depois.score,
        confidence_before=antes.confidence,
        confidence_after=depois.confidence,
        new_evidences=novas,
    )


def comparar_scores(antes: DefensibilityScore, depois: DefensibilityScore) -> ScoreDelta:
    """Compara duas execuções da mesma empresa, eixo a eixo.

    Não valida que `depois` é cronologicamente posterior a `antes` — quem chama
    (o nó `compare`, quando existir) é responsável por passar `history()[0]`
    como `antes` e o score recém-calculado como `depois`. Este módulo é
    aritmética pura; ordenar no tempo é responsabilidade de quem tem acesso ao
    repositório, que `scoring/` deliberadamente não tem.
    """
    por_eixo_antes = {a.axis: a for a in antes.axes}
    deltas = [
        _comparar_eixo(por_eixo_antes[eixo.axis], eixo)
        for eixo in depois.axes
        if eixo.axis in por_eixo_antes
    ]
    return ScoreDelta(
        company_name=depois.company_name,
        weights_version_before=antes.weights_version,
        weights_version_after=depois.weights_version,
        axes=deltas,
    )
