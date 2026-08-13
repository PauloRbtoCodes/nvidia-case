"""Camada de coleta: descoberta, busca de páginas e extração de evidências.

O caminho é sempre o mesmo — `search` acha candidatas, `fetch` traz o HTML
(passando por `politeness` e `cache`), `extract` converte em `Evidence`. Nenhum
nó do grafo deve falar HTTP por conta própria: fora daqui não há robots.txt,
rate limit nem cache.
"""

from __future__ import annotations

from radar.scraping.cache import CachedResponse, ResponseCache
from radar.scraping.extract import (
    ExtractedPage,
    classify_source_kind,
    evidence_from_page,
    evidences_for_keywords,
    extract_career_links,
    extract_client_names,
    extract_job_titles,
    extract_page,
    extract_tech_mentions,
    make_evidence,
)
from radar.scraping.fetch import FetchResult, HttpFetcher, fetch_urls, needs_rendering
from radar.scraping.politeness import PolitenessGate, can_fetch
from radar.scraping.search import (
    MissingSearchKeyError,
    SearchCandidate,
    SearchError,
    TavilySearch,
)

__all__ = [
    "CachedResponse",
    "ExtractedPage",
    "FetchResult",
    "HttpFetcher",
    "MissingSearchKeyError",
    "PolitenessGate",
    "ResponseCache",
    "SearchCandidate",
    "SearchError",
    "TavilySearch",
    "can_fetch",
    "classify_source_kind",
    "evidence_from_page",
    "evidences_for_keywords",
    "extract_career_links",
    "extract_client_names",
    "extract_job_titles",
    "extract_page",
    "extract_tech_mentions",
    "fetch_urls",
    "make_evidence",
    "needs_rendering",
]
