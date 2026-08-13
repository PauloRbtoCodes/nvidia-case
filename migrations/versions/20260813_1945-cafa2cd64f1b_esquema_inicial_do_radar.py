"""esquema inicial do radar

Revision ID: cafa2cd64f1b
Revises:
Create Date: 2026-08-13 19:45:39.389922

Snapshot inicial das tabelas derivadas de `radar.models`. Gerado por
autogenerate e depois revisado à mão em dois pontos:

- as colunas JSON usam `with_variant(JSONB, "postgresql")`, para que a mesma
  migration suba tanto no Postgres de produção quanto no SQLite dos testes —
  sem Docker na máquina de desenvolvimento, um esquema exclusivo do Postgres é
  um esquema que ninguém consegue verificar;
- os `batch_alter_table` que o autogenerate produz sob SQLite viraram
  `op.create_index` direto: em criação de tabela eles não têm função e só
  escondem o DDL real.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "cafa2cd64f1b"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    """JSONB no Postgres, JSON em qualquer outro dialeto. Espelha `tables.JSONVariant`."""
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("normalized_website", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("founded_year", sa.Integer(), nullable=True),
        sa.Column("hq_city", sa.String(length=120), nullable=True),
        sa.Column("hq_state", sa.String(length=2), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("headcount_estimate", sa.Integer(), nullable=True),
        sa.Column("sector", _json(), nullable=True),
        sa.Column("target_market", _json(), nullable=True),
        sa.Column("ai_use_description", _json(), nullable=True),
        sa.Column("inference_provider", _json(), nullable=True),
        sa.Column("proprietary_data_claim", _json(), nullable=True),
        sa.Column("named_integrations", _json(), nullable=True),
        sa.Column("enterprise_customers", _json(), nullable=True),
        sa.Column("tech_signals", _json(), nullable=False),
        sa.Column("founders", _json(), nullable=False),
        sa.Column("funding_rounds", _json(), nullable=False),
        sa.Column("open_engineering_roles", _json(), nullable=False),
        sa.Column("source_urls", _json(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("normalized_name", name=op.f("uq_companies_normalized_name")),
        sa.UniqueConstraint("normalized_website", name=op.f("uq_companies_normalized_website")),
    )
    op.create_index("ix_companies_name", "companies", ["name"], unique=False)
    op.create_index("ix_companies_website", "companies", ["website"], unique=False)

    op.create_table(
        "evidences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_evidences_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidences")),
        # O UNIQUE aqui é o mecanismo de dedupe do sistema inteiro: sem ele, a
        # mesma citação recuperada por dois caminhos contaria como duas provas.
        sa.UniqueConstraint("content_hash", name=op.f("uq_evidences_content_hash")),
    )
    op.create_index("ix_evidences_company_id", "evidences", ["company_id"], unique=False)

    op.create_table(
        "classifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("maturity", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=60), nullable=True),
        sa.Column("evidence_hashes", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_classifications_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classifications")),
    )
    op.create_index("ix_classifications_company_id", "classifications", ["company_id"])
    op.create_index(
        "ix_classifications_company_created", "classifications", ["company_id", "created_at"]
    )

    op.create_table(
        "defensibility_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("weights_version", sa.String(length=60), nullable=False),
        sa.Column("total", sa.Float(), nullable=False),
        sa.Column("commoditization_risk", sa.Float(), nullable=False),
        sa.Column("global_confidence", sa.Float(), nullable=False),
        sa.Column("weakest_axis", sa.String(length=32), nullable=False),
        sa.Column("proprietary_data_score", sa.Float(), nullable=False),
        sa.Column("proprietary_data_confidence", sa.Float(), nullable=False),
        sa.Column("workflow_depth_score", sa.Float(), nullable=False),
        sa.Column("workflow_depth_confidence", sa.Float(), nullable=False),
        sa.Column("stack_ownership_score", sa.Float(), nullable=False),
        sa.Column("stack_ownership_confidence", sa.Float(), nullable=False),
        sa.Column("distribution_score", sa.Float(), nullable=False),
        sa.Column("distribution_confidence", sa.Float(), nullable=False),
        sa.Column("axes_detail", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_defensibility_scores_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_defensibility_scores")),
    )
    op.create_index("ix_defensibility_scores_company_id", "defensibility_scores", ["company_id"])
    # Índice composto para o "score mais recente por empresa" — a query da fila
    # de prioridade, que roda em toda abertura do dashboard.
    op.create_index(
        "ix_defensibility_scores_company_created",
        "defensibility_scores",
        ["company_id", "created_at"],
    )

    op.create_table(
        "tco_estimates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("score_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("scenario", sa.String(length=20), nullable=False),
        sa.Column("monthly_tokens_estimate", sa.Integer(), nullable=False),
        sa.Column("current_provider", sa.String(length=120), nullable=True),
        sa.Column("current_monthly_usd", sa.Float(), nullable=False),
        sa.Column("nvidia_stack_monthly_usd", sa.Float(), nullable=False),
        sa.Column("gpu_assumption", sa.String(length=255), nullable=False),
        sa.Column("assumptions", _json(), nullable=False),
        sa.Column("weights_version", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_tco_estimates_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["score_id"],
            ["defensibility_scores.id"],
            name=op.f("fk_tco_estimates_score_id_defensibility_scores"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tco_estimates")),
        sa.UniqueConstraint("score_id", "scenario", name="uq_tco_estimates_score_scenario"),
    )
    op.create_index("ix_tco_estimates_company_id", "tco_estimates", ["company_id"])

    op.create_table(
        "priority_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("score_id", sa.Uuid(), nullable=True),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.Float(), nullable=False),
        sa.Column("capacity_to_act", sa.Float(), nullable=False),
        sa.Column("capacity_rationale", sa.Text(), nullable=False),
        sa.Column("recommended_next_step", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_priority_assessments_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["score_id"],
            ["defensibility_scores.id"],
            name=op.f("fk_priority_assessments_score_id_defensibility_scores"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_priority_assessments")),
        sa.UniqueConstraint("score_id", name=op.f("uq_priority_assessments_score_id")),
    )
    op.create_index("ix_priority_assessments_company_id", "priority_assessments", ["company_id"])
    op.create_index("ix_priority_assessments_urgency", "priority_assessments", ["urgency"])

    op.create_table(
        "briefings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("score_id", sa.Uuid(), nullable=True),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("maturity", sa.String(length=32), nullable=False),
        sa.Column("executive_summary", sa.Text(), nullable=False),
        sa.Column("moat_plan", sa.Text(), nullable=False),
        sa.Column("inception_fit", sa.Text(), nullable=True),
        sa.Column("conversation_starters", _json(), nullable=False),
        sa.Column("caveats", _json(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_briefings_company_id_companies"),
            ondelete="CASCADE",
        ),
        # SET NULL e não CASCADE: o briefing entregue ao gerente sobrevive ao
        # descarte do score que o originou.
        sa.ForeignKeyConstraint(
            ["score_id"],
            ["defensibility_scores.id"],
            name=op.f("fk_briefings_score_id_defensibility_scores"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_briefings")),
    )
    op.create_index("ix_briefings_company_id", "briefings", ["company_id"])
    op.create_index("ix_briefings_company_created", "briefings", ["company_id", "created_at"])

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("briefing_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("technology", sa.String(length=120), nullable=False),
        sa.Column("addresses_axis", sa.String(length=32), nullable=False),
        sa.Column("technical_rationale", sa.Text(), nullable=False),
        sa.Column("business_rationale", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False),
        sa.Column("complexity", sa.String(length=16), nullable=False),
        sa.Column("next_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["briefing_id"],
            ["briefings.id"],
            name=op.f("fk_recommendations_briefing_id_briefings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_recommendations_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
    )
    op.create_index("ix_recommendations_company_id", "recommendations", ["company_id"])
    op.create_index("ix_recommendations_briefing_id", "recommendations", ["briefing_id"])

    op.create_table(
        "recommendation_evidences",
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidences.id"],
            name=op.f("fk_recommendation_evidences_evidence_id_evidences"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_recommendation_evidences_recommendation_id_recommendations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "recommendation_id", "evidence_id", name=op.f("pk_recommendation_evidences")
        ),
    )

    op.create_table(
        "recommendation_kb_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("source_title", sa.String(length=500), nullable=True),
        sa.Column("technology", sa.String(length=120), nullable=True),
        sa.Column("dense_score", sa.Float(), nullable=True),
        sa.Column("bm25_score", sa.Float(), nullable=True),
        sa.Column("rrf_score", sa.Float(), nullable=True),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["recommendation_id"],
            ["recommendations.id"],
            name=op.f("fk_recommendation_kb_citations_recommendation_id_recommendations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_kb_citations")),
    )
    op.create_index(
        "ix_recommendation_kb_citations_rec_id",
        "recommendation_kb_citations",
        ["recommendation_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("recommendation_kb_citations")
    op.drop_table("recommendation_evidences")
    op.drop_table("recommendations")
    op.drop_table("briefings")
    op.drop_table("priority_assessments")
    op.drop_table("tco_estimates")
    op.drop_table("defensibility_scores")
    op.drop_table("classifications")
    op.drop_table("evidences")
    op.drop_table("companies")
