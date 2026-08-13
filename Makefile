.PHONY: help setup up down logs check ingest run api web test lint eval clean

help:
	@echo "NVIDIA Startup AI Radar"
	@echo ""
	@echo "  make setup    Instala dependencias Python (uv)"
	@echo "  make up       Sobe Postgres, Qdrant e Langfuse"
	@echo "  make down     Derruba os servicos"
	@echo "  make check    Valida .env e conectividade com a infra"
	@echo "  make ingest   Ingere a base de conhecimento NVIDIA no Qdrant"
	@echo "  make run      Executa o grafo via CLI"
	@echo "  make api      Sobe a API FastAPI em :8000"
	@echo "  make web      Sobe o frontend Next.js em :3000"
	@echo "  make test     Roda os testes"
	@echo "  make lint     Ruff + mypy"
	@echo "  make eval     Suite de avaliacao (RAGAS + classificador)"

setup:
	uv sync --extra dev --extra eval
	uv run playwright install chromium

up:
	docker compose up -d
	@echo "Postgres :5432 | Qdrant :6333 | Langfuse :3001"

down:
	docker compose down

logs:
	docker compose logs -f

check:
	uv run python -m radar.cli check

ingest:
	uv run python -m radar.cli ingest --sources data/nvidia_sources.yaml

run:
	uv run python -m radar.cli run --query "$(Q)"

api:
	uv run uvicorn api.main:app --reload --port 8000

web:
	cd web && npm run dev

test:
	uv run pytest -q

lint:
	uv run ruff check src api tests
	uv run ruff format --check src api tests
	uv run mypy src

eval:
	uv run python -m radar.eval.run_all

clean:
	rm -rf data/cache data/bm25_index .pytest_cache .ruff_cache .mypy_cache
