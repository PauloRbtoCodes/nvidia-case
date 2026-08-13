"""Rubrica do Scorer, montada a partir de `scoring/weights.yaml`.

Os sinais por eixo entram no prompt em vez de estarem escritos dentro dele: o
YAML é versionado e o score guardado no Postgres grava a versão que usou. Se a
rubrica vivesse no texto do prompt, recalibrar exigiria editar prompt já usado
em avaliação — e a comparação entre versões perderia o sentido.

Leitura estritamente read-only; `scoring/` continua dono do arquivo. Quando
`scoring/` expuser um loader público, trocar `load_weights` por ele.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from radar.config import PROJECT_ROOT, get_settings


@lru_cache
def load_weights(path: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    resolved = path or settings.weights_path
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}


def weights_version(weights: dict[str, Any] | None = None) -> str:
    return str((weights or load_weights()).get("version", "desconhecida"))


def signals_rubric(weights: dict[str, Any] | None = None) -> str:
    """Renderiza a rubrica de sinais em markdown, para injetar no prompt do Scorer.

    Formato de bullets porque o modelo precisa citar *qual* sinal observou nos
    campos `positive_signals`/`negative_signals` do `AxisScore`; uma lista
    fechada dá vocabulário comum entre execuções e torna o score auditável.
    """
    weights = weights or load_weights()
    axis_weights: dict[str, float] = weights.get("axis_weights", {})
    signals: dict[str, dict[str, list[str]]] = weights.get("signals", {})

    blocks: list[str] = []
    for axis, groups in signals.items():
        peso = axis_weights.get(axis)
        cabecalho = f"### {axis}" + (f" (peso {peso:.0%})" if peso else "")
        positivos = "\n".join(f"  - {s}" for s in groups.get("positive", []))
        negativos = "\n".join(f"  - {s}" for s in groups.get("negative", []))
        blocks.append(
            f"{cabecalho}\n"
            f"- Sinais POSITIVOS (elevam o score):\n{positivos}\n"
            f"- Sinais NEGATIVOS (rebaixam o score):\n{negativos}"
        )

    limiar = weights.get("actionable_confidence_threshold", 0.35)
    gap = weights.get("gap_score_threshold", 60.0)
    rodape = (
        f"\nLimiar de confiança acionável: {limiar} "
        f"(abaixo disso o eixo é reportado como 'evidência insuficiente', nunca como nota baixa).\n"
        f"Limiar de gap: score < {gap} caracteriza gap.\n"
        f"Versão dos pesos: {weights_version(weights)}"
    )
    return "\n\n".join(blocks) + "\n" + rodape
