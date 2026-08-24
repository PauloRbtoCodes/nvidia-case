"""Evidência: a unidade atômica de rastreabilidade do sistema.

Regra estrutural do projeto: nenhuma afirmação sobre uma startup existe sem uma
Evidence apontando para a URL e o trecho literal que a sustenta. Isso é o que
separa um briefing auditável de um texto plausível gerado por LLM.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, field_validator


class SourceKind(StrEnum):
    """De onde a evidência veio. Pesa na confiança atribuída."""

    SITE_OFICIAL = "site_oficial"
    BLOG_TECNICO = "blog_tecnico"
    PAGINA_CARREIRAS = "pagina_carreiras"
    DOCUMENTACAO = "documentacao"
    NOTICIA = "noticia"
    DIRETORIO_STARTUP = "diretorio_startup"
    PERFIL_PUBLICO = "perfil_publico"
    OUTRO = "outro"


#: Confiança de base por tipo de fonte. Blog técnico da própria empresa descrevendo
#: sua stack vale mais que matéria de portal, que frequentemente repete o release.
SOURCE_TRUST: dict[SourceKind, float] = {
    SourceKind.BLOG_TECNICO: 0.95,
    SourceKind.DOCUMENTACAO: 0.95,
    SourceKind.PAGINA_CARREIRAS: 0.90,
    SourceKind.SITE_OFICIAL: 0.85,
    SourceKind.DIRETORIO_STARTUP: 0.70,
    SourceKind.NOTICIA: 0.65,
    SourceKind.PERFIL_PUBLICO: 0.60,
    SourceKind.OUTRO: 0.50,
}


class Evidence(BaseModel):
    """Um trecho literal extraído de uma fonte pública, com procedência completa."""

    url: HttpUrl
    kind: SourceKind = SourceKind.OUTRO

    excerpt: str = Field(
        min_length=20,
        max_length=2000,
        description="Trecho LITERAL da fonte. Nunca parafraseado — se o LLM reescreveu, "
        "a evidência perde a função de prova.",
    )
    context: str | None = Field(
        default=None,
        description="Título da seção ou da página onde o trecho aparece.",
    )

    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = Field(
        default=None,
        description="Data da publicação, quando extraível. Evidência de 2021 sobre stack "
        "de IA está provavelmente obsoleta.",
    )

    @property
    def content_hash(self) -> str:
        """Deduplica o mesmo trecho recuperado por caminhos diferentes."""
        return hashlib.sha256(f"{self.url}|{self.excerpt}".encode()).hexdigest()[:16]

    @property
    def trust(self) -> float:
        """Confiança da fonte, decaída pela idade da publicação.

        Sinal técnico envelhece rápido: uma startup que em 2023 dizia usar a API da
        OpenAI pode ter migrado para modelo próprio. Sem data, assumimos o piso do
        tipo de fonte sem decaimento — não temos base para penalizar.
        """
        base = SOURCE_TRUST[self.kind]
        if self.published_at is None:
            return base

        age_days = (datetime.now(UTC) - self.published_at).days
        if age_days <= 180:
            return base
        if age_days <= 365:
            return base * 0.85
        if age_days <= 730:
            return base * 0.65
        return base * 0.40

    @field_validator("excerpt", "context")
    @classmethod
    def _limpa_texto(cls, v: str | None) -> str | None:
        """Normaliza espaços e remove caracteres de controle.

        O NUL (0x00) é o que motivou isto, e o custo foi um lote inteiro: HTML
        raspado de site real trouxe 0x00 no texto, o Postgres recusa NUL em campo
        text, e a gravação da empresa falhou **depois** de todo o diagnóstico ter
        sido pago em cota. Sanitizar no contrato, e não no repositório, garante
        que nenhuma outra porta de escrita repita o erro.

        Remove a faixa de controle C0 exceto o que vira espaço no `split()`.
        """
        if v is None:
            return None
        sem_controle = "".join(c for c in v if c == "\n" or c == "\t" or ord(c) >= 32)
        return " ".join(sem_controle.split())


class EvidenceBackedField[T](BaseModel):
    """Um valor que só existe acompanhado das evidências que o sustentam.

    Usado para todo campo inferido (setor, uso de IA, stack). Um campo sem
    evidência é tratado como desconhecido, nunca como falso — a distinção entre
    "não encontramos" e "não existe" é central para não penalizar startups
    discretas no Defensibility Score.
    """

    value: T
    evidences: list[Evidence] = Field(default_factory=list)
    reasoning: str | None = Field(
        default=None, description="Por que o extrator concluiu isso a partir dos trechos."
    )

    @property
    def confidence(self) -> float:
        """Confiança agregada.

        Evidências independentes reforçam umas às outras, mas com retorno
        decrescente: usamos complemento do produto dos erros, de modo que três
        fontes fracas não passem uma fonte forte.
        """
        if not self.evidences:
            return 0.0

        error = 1.0
        for ev in sorted(self.evidences, key=lambda e: e.trust, reverse=True)[:5]:
            error *= 1.0 - ev.trust
        return round(1.0 - error, 4)

    @property
    def is_grounded(self) -> bool:
        return bool(self.evidences)
