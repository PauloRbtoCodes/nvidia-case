"""Carregamento e validação de scoring/weights.yaml.

As premissas do Defensibility Radar vivem em YAML, não em código, por uma razão
prática: a calibração acontece na semana 4 contra as startups rotuladas à mão, e
recalibrar não pode exigir alterar código nem re-scraping. O score guardado no
Postgres grava a `version` usada, então o histórico continua interpretável mesmo
depois de os pesos mudarem.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from radar.config import PROJECT_ROOT, get_settings


class AxisSignals(BaseModel):
    """Rubrica de sinais observáveis de um eixo, injetada no prompt do Scorer."""

    positive: list[str] = Field(default_factory=list)
    negative: list[str] = Field(default_factory=list)


class CapacityWeights(BaseModel):
    recent_funding: float
    technical_founder: float
    engineering_hiring: float
    stage_maturity: float

    @model_validator(mode="after")
    def _soma_um(self) -> CapacityWeights:
        total = (
            self.recent_funding
            + self.technical_founder
            + self.engineering_hiring
            + self.stage_maturity
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Pesos de capacity_to_act devem somar 1.0, somam {total}")
        return self


class CapacityConfig(BaseModel):
    funding_recency_months: int
    weights: CapacityWeights
    stage_scores: dict[str, float]


class PriorityConfig(BaseModel):
    """Cortes que traduzem score e capacidade no balde da semana."""

    min_global_confidence: float = Field(ge=0.0, le=1.0)
    defensible_threshold: float = Field(ge=0.0, le=100.0)
    capacity_threshold: float = Field(ge=0.0, le=1.0)


class TCOConfig(BaseModel):
    precos_atualizados_em: str
    api_pricing_usd_per_1m_tokens: dict[str, float]
    gpu_hourly_usd: dict[str, float]
    throughput_tokens_per_second: dict[str, int]
    utilizacao_media: float = Field(gt=0.0, le=1.0)
    cenarios_volume_multiplicador: dict[str, float]
    volume_base_tokens_mes: dict[str, int]
    break_even_minimo_tokens_mes: int

    @model_validator(mode="after")
    def _gpus_consistentes(self) -> TCOConfig:
        """Toda GPU precifiada precisa ter throughput, senão o cálculo é impossível."""
        sem_throughput = set(self.gpu_hourly_usd) - set(self.throughput_tokens_per_second)
        if sem_throughput:
            raise ValueError(f"GPUs sem throughput definido: {sorted(sem_throughput)}")
        return self


class ScoringWeights(BaseModel):
    """Conteúdo completo de weights.yaml, validado no carregamento."""

    version: str
    axis_weights: dict[str, float]
    actionable_confidence_threshold: float = Field(ge=0.0, le=1.0)
    gap_score_threshold: float = Field(ge=0.0, le=100.0)
    priority: PriorityConfig
    signals: dict[str, AxisSignals]
    capacity_to_act: CapacityConfig
    tco: TCOConfig

    @model_validator(mode="after")
    def _pesos_de_eixo_somam_um(self) -> ScoringWeights:
        """Se não somarem 1.0, `total` deixa de ser uma escala 0-100 e o número mente."""
        total = sum(self.axis_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"axis_weights devem somar 1.0, somam {total}")
        return self

    def rubric_for(self, axis: str) -> AxisSignals:
        return self.signals.get(axis, AxisSignals())


def load_weights(path: Path | None = None) -> ScoringWeights:
    settings = get_settings()
    target = path or settings.weights_path
    if not target.is_absolute():
        target = PROJECT_ROOT / target

    raw: dict[str, Any] = yaml.safe_load(target.read_text(encoding="utf-8"))
    return ScoringWeights.model_validate(raw)


@lru_cache
def get_weights() -> ScoringWeights:
    return load_weights()
