"""Testes da camada de scraping.

Zero rede: todo acesso externo entra por injeção (transport do httpx, dublê do
robots.txt, dublê do Tavily, renderizador falso). Isso não é só conveniência de
CI — é o que permite rodar a suíte sem chave de API, sem Docker e sem os
binários do Playwright, que é o estado real desta máquina.

O foco é nos invariantes que tornam a coleta defensável: robots.txt de fato
bloqueia, o freio é por domínio, o cache poupa o servidor alheio, o excerpt é
literal e o fallback de navegador é exceção.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from radar.config import Settings
from radar.models.evidence import SourceKind
from radar.scraping.cache import CachedResponse, ResponseCache
from radar.scraping.extract import (
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
from radar.scraping.fetch import HttpFetcher, needs_rendering
from radar.scraping.politeness import DomainRateLimiter, PolitenessGate, RobotsCache
from radar.scraping.search import (
    DEFAULT_BLOCKED_DOMAINS,
    MissingSearchKeyError,
    SearchCandidate,
    TavilySearch,
    dedupe_by_domain,
    is_blocked,
)

# --------------------------------------------------------------------------- #
# Fixtures de HTML (inline: são pequenos e o teste fica legível junto do dado)
# --------------------------------------------------------------------------- #
BLOG_HTML = """<html><head>
<title>Como servimos nossos modelos</title>
<meta property="article:published_time" content="2025-03-10T12:00:00Z">
</head><body>
<nav><a href="/">Home</a><a href="/carreiras">Carreiras</a></nav>
<article>
<h1>Como servimos nossos modelos</h1>
<p>Migramos toda a inferencia para GPUs proprias usando Triton Inference Server
e TensorRT-LLM, o que reduziu a latencia media das nossas respostas em quarenta
por cento e cortou o custo por milhao de tokens pela metade.</p>
<p>O pipeline de dados roda em Airflow e alimenta o fine-tuning mensal do modelo
proprietario com as interacoes anonimizadas dos clientes que autorizaram o uso
para treinamento, um ciclo que hoje esta completamente automatizado.</p>
</article>
<footer>Todos os direitos reservados</footer>
</body></html>"""

CAREERS_HTML = """<html><body>
<h1>Trabalhe conosco</h1>
<ul class="vagas">
  <li><a href="/vagas/machine-learning-engineer">Machine Learning Engineer (Senior)</a></li>
  <li><a href="https://empresa.gupy.io/jobs/123">Engenheiro de Dados Pleno</a></li>
  <li><a href="/vagas/executivo-de-contas">Executivo de Contas</a></li>
  <li><a href="/sobre">Sobre nos</a></li>
</ul>
</body></html>"""

CLIENTS_HTML = """<html><body>
<section>
  <h2>Nossos clientes</h2>
  <img src="/logos/1.svg" alt="Banco Neon">
  <img src="/logos/2.svg" alt="Logo Magazine Luiza">
  <figcaption>Grupo Fleury</figcaption>
</section>
<section class="parceiros-logos">
  <img src="/logos/3.svg" alt="NVIDIA Inception">
</section>
</body></html>"""

SPA_SHELL_HTML = """<html><head><title>App</title>
<script src="/bundle.js"></script></head>
<body><div id="root"></div><noscript>Ative o JavaScript</noscript></body></html>"""

RENDERED_HTML = """<html><body><main><p>Somos uma startup de visao computacional
para inspecao industrial, com modelos proprios treinados em imagens capturadas
nas linhas de producao dos nossos clientes industriais brasileiros, hoje em
operacao em fabricas de autopecas, alimentos e bens de consumo em todo o
pais.</p></main></body></html>"""

ROBOTS_BODY = """User-agent: *
Disallow: /privado/
Disallow: /admin

User-agent: BadBot
Disallow: /
"""


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Settings isolado do .env real e apontando o cache para o tmp do teste."""
    base: dict[str, Any] = {
        "scraper_cache_dir": tmp_path / "cache",
        "scraper_rate_limit_seconds": 0.0,
        "scraper_respect_robots": False,
        "scraper_timeout_seconds": 5.0,
        "tavily_api_key": "",
    }
    base.update(overrides)
    return Settings(**base)


class FakeClock:
    """Relógio e sleep falsos: o teste de rate limit precisa ser determinístico."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def counting_transport(
    handler: Any, calls: list[httpx.Request]
) -> httpx.MockTransport:
    def _handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.MockTransport(_handle)


# --------------------------------------------------------------------------- #
# politeness — rate limit
# --------------------------------------------------------------------------- #
async def test_rate_limit_espaca_requisicoes_do_mesmo_dominio():
    clock = FakeClock()
    limiter = DomainRateLimiter(2.0, clock=clock, sleeper=clock.sleep)

    await limiter.acquire("https://empresa.com.br/a")
    await limiter.acquire("https://empresa.com.br/b")

    assert clock.slept == [2.0]


async def test_rate_limit_e_por_dominio_e_nao_global():
    """O freio protege o host, não a nossa velocidade: raspar dois sites
    diferentes em paralelo é legítimo e não pode custar espera."""
    clock = FakeClock()
    limiter = DomainRateLimiter(2.0, clock=clock, sleeper=clock.sleep)

    await limiter.acquire("https://empresa-a.com.br/x")
    await limiter.acquire("https://empresa-b.com.br/x")
    await limiter.acquire("https://outra.com.br/x")

    assert clock.slept == []


async def test_rate_limit_trata_www_e_porta_como_mesmo_host():
    clock = FakeClock()
    limiter = DomainRateLimiter(1.5, clock=clock, sleeper=clock.sleep)

    await limiter.acquire("https://www.empresa.com.br/a")
    await limiter.acquire("https://empresa.com.br:443/b")

    assert clock.slept == [1.5]


async def test_rate_limit_concorrente_serializa_o_mesmo_host():
    clock = FakeClock()
    limiter = DomainRateLimiter(1.0, clock=clock, sleeper=clock.sleep)

    await asyncio.gather(*(limiter.acquire("https://empresa.com.br/p") for _ in range(3)))

    assert clock.slept == [1.0, 1.0]


# --------------------------------------------------------------------------- #
# politeness — robots.txt
# --------------------------------------------------------------------------- #
def robots_fetcher(body: str | None, calls: list[str]):
    async def _fetch(url: str) -> str | None:
        calls.append(url)
        return body

    return _fetch


async def test_robots_bloqueia_caminho_proibido():
    cache = RobotsCache(user_agent="Radar/0.1", fetcher=robots_fetcher(ROBOTS_BODY, []))

    assert await cache.can_fetch("https://empresa.com.br/blog/post") is True
    assert await cache.can_fetch("https://empresa.com.br/privado/dados") is False
    assert await cache.can_fetch("https://empresa.com.br/admin") is False


async def test_robots_e_buscado_uma_unica_vez_por_dominio():
    calls: list[str] = []
    cache = RobotsCache(user_agent="Radar", fetcher=robots_fetcher(ROBOTS_BODY, calls))

    await asyncio.gather(
        *(cache.can_fetch(f"https://empresa.com.br/p{i}") for i in range(5)),
        cache.can_fetch("https://outra.com.br/p"),
    )

    assert calls.count("https://empresa.com.br/robots.txt") == 1
    assert calls.count("https://outra.com.br/robots.txt") == 1


async def test_robots_ausente_libera_a_coleta():
    """Sem arquivo, o padrão diz que tudo é permitido — e falha de rede não pode
    virar proibição silenciosa, que apagaria cobertura sem ninguém perceber."""
    cache = RobotsCache(user_agent="Radar", fetcher=robots_fetcher(None, []))

    assert await cache.can_fetch("https://empresa.com.br/privado/dados") is True


async def test_robots_desligado_nao_busca_nada():
    calls: list[str] = []
    fetcher = robots_fetcher(ROBOTS_BODY, calls)
    cache = RobotsCache(user_agent="Radar", fetcher=fetcher, enabled=False)

    assert await cache.can_fetch("https://empresa.com.br/privado/x") is True
    assert calls == []


def test_headers_identificam_o_coletor():
    gate = PolitenessGate(Settings(scraper_user_agent="RadarTeste/9.9 (contato)"))

    assert gate.headers()["User-Agent"] == "RadarTeste/9.9 (contato)"


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
def test_cache_guarda_e_recupera(tmp_path: Path):
    cache = ResponseCache(tmp_path)
    entry = CachedResponse(url="https://empresa.com.br/", status_code=200, body="<html>oi</html>")

    cache.set(entry)
    recovered = cache.get("https://empresa.com.br/")

    assert recovered is not None
    assert recovered.body == "<html>oi</html>"
    assert recovered.status_code == 200


def test_cache_expira_pelo_ttl(tmp_path: Path):
    cache = ResponseCache(tmp_path, ttl_seconds=60)
    cache.set(
        CachedResponse(
            url="https://empresa.com.br/",
            status_code=200,
            body="antigo",
            fetched_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )

    assert cache.get("https://empresa.com.br/") is None


def test_cache_ignora_arquivo_corrompido(tmp_path: Path):
    """Cache é otimização; um JSON truncado nunca pode derrubar a coleta."""
    cache = ResponseCache(tmp_path)
    url = "https://empresa.com.br/"
    path = cache.path_for(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"url": "https://empresa', encoding="utf-8")

    assert cache.get(url) is None
    assert not path.exists()


def test_cache_separa_urls_diferentes(tmp_path: Path):
    cache = ResponseCache(tmp_path)
    cache.set(CachedResponse(url="https://a.com/", status_code=200, body="A"))
    cache.set(CachedResponse(url="https://b.com/", status_code=200, body="B"))

    assert cache.get("https://a.com/").body == "A"  # type: ignore[union-attr]
    assert cache.get("https://b.com/").body == "B"  # type: ignore[union-attr]


def test_cache_persiste_em_json_legivel(tmp_path: Path):
    """Formato inspecionável a olho nu: depurar extração exige abrir o HTML."""
    cache = ResponseCache(tmp_path)
    cache.set(CachedResponse(url="https://a.com/", status_code=200, body="<p>oi</p>"))

    payload = json.loads(cache.path_for("https://a.com/").read_text(encoding="utf-8"))

    assert payload["status_code"] == 200
    assert payload["body"] == "<p>oi</p>"


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
async def test_robots_bloqueando_impede_a_requisicao(tmp_path: Path):
    """O teste que importa não é `can_fetch` devolver False, é a rede não ser tocada."""
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=BLOG_HTML), calls)
    settings = make_settings(tmp_path, scraper_respect_robots=True)
    gate = PolitenessGate(
        settings,
        robots=RobotsCache(user_agent="Radar", fetcher=robots_fetcher(ROBOTS_BODY, [])),
    )

    async with HttpFetcher(settings, gate=gate, transport=transport) as fetcher:
        result = await fetcher.fetch("https://empresa.com.br/privado/segredo")

    assert result is None
    assert calls == []


async def test_cache_evita_a_segunda_busca(tmp_path: Path):
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=BLOG_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport) as fetcher:
        primeiro = await fetcher.fetch("https://empresa.com.br/blog/post")
        segundo = await fetcher.fetch("https://empresa.com.br/blog/post")

    assert len(calls) == 1
    assert primeiro is not None and primeiro.from_cache is False
    assert segundo is not None and segundo.from_cache is True
    assert segundo.html == primeiro.html


async def test_force_refresh_ignora_o_cache(tmp_path: Path):
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=BLOG_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport) as fetcher:
        await fetcher.fetch("https://empresa.com.br/blog/post")
        await fetcher.fetch("https://empresa.com.br/blog/post", force_refresh=True)

    assert len(calls) == 2


async def test_404_nao_e_cacheado_nem_repetido(tmp_path: Path):
    """Repetir 4xx é insistir onde já disseram não; cachear esconde a página real."""
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(404, text="nao encontrado"), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport) as fetcher:
        primeiro = await fetcher.fetch("https://empresa.com.br/sumiu")
        segundo = await fetcher.fetch("https://empresa.com.br/sumiu")

    assert primeiro is not None and primeiro.ok is False
    assert len(calls) == 2  # nenhuma das duas veio do cache
    assert segundo is not None and segundo.from_cache is False


async def test_500_e_tentado_de_novo(tmp_path: Path):
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(503, text="indisponivel"), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(
        settings, transport=transport, max_attempts=3, retry_wait_multiplier=0.0
    ) as fetcher:
        result = await fetcher.fetch("https://empresa.com.br/instavel")

    assert result is None
    assert len(calls) == 3


async def test_429_e_tentado_de_novo_e_pode_ter_sucesso(tmp_path: Path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(calls) < 3:  # `calls` já inclui a requisição corrente
            return httpx.Response(429, text="devagar")
        return httpx.Response(200, text=BLOG_HTML)

    transport = counting_transport(handler, calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(
        settings, transport=transport, max_attempts=3, retry_wait_multiplier=0.0
    ) as fetcher:
        result = await fetcher.fetch("https://empresa.com.br/instavel")

    assert result is not None and result.ok
    assert len(calls) == 3


async def test_403_nao_e_tentado_de_novo(tmp_path: Path):
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(403, text="proibido"), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(
        settings, transport=transport, max_attempts=3, retry_wait_multiplier=0.0
    ) as fetcher:
        await fetcher.fetch("https://empresa.com.br/fechado")

    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# fetch — heurística do fallback Playwright
# --------------------------------------------------------------------------- #
def test_heuristica_de_render_so_dispara_com_html_vazio():
    assert needs_rendering(SPA_SHELL_HTML) is True
    assert needs_rendering("") is True
    assert needs_rendering(BLOG_HTML) is False
    assert needs_rendering(RENDERED_HTML) is False


def test_heuristica_ignora_script_e_style():
    """Bundle JS gigante não é conteúdo: contar bytes brutos mascararia a SPA vazia."""
    html = "<html><body><div id='root'></div><script>" + ("x=1;" * 500) + "</script></body></html>"

    assert needs_rendering(html) is True


async def test_fallback_nao_dispara_em_pagina_com_texto(tmp_path: Path):
    rendered_calls: list[str] = []

    async def fake_renderer(url: str) -> str | None:
        rendered_calls.append(url)
        return RENDERED_HTML

    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=BLOG_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport, renderer=fake_renderer) as fetcher:
        result = await fetcher.fetch("https://empresa.com.br/blog/post")

    assert rendered_calls == []
    assert result is not None and result.rendered is False


async def test_fallback_dispara_e_substitui_o_html_vazio(tmp_path: Path):
    rendered_calls: list[str] = []

    async def fake_renderer(url: str) -> str | None:
        rendered_calls.append(url)
        return RENDERED_HTML

    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=SPA_SHELL_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport, renderer=fake_renderer) as fetcher:
        result = await fetcher.fetch("https://app.empresa.com.br/")

    assert rendered_calls == ["https://app.empresa.com.br/"]
    assert result is not None and result.rendered is True
    assert "visao computacional" in result.html


async def test_fallback_desligado_devolve_o_html_estatico(tmp_path: Path):
    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=SPA_SHELL_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(
        settings, transport=transport, use_playwright_fallback=False
    ) as fetcher:
        result = await fetcher.fetch("https://app.empresa.com.br/")

    assert result is not None and result.rendered is False


async def test_render_falho_nao_derruba_a_coleta(tmp_path: Path):
    """Sem Playwright instalado (o caso desta máquina) o renderer devolve None."""

    async def failing_renderer(url: str) -> str | None:
        return None

    calls: list[httpx.Request] = []
    transport = counting_transport(lambda r: httpx.Response(200, text=SPA_SHELL_HTML), calls)
    settings = make_settings(tmp_path)

    async with HttpFetcher(settings, transport=transport, renderer=failing_renderer) as fetcher:
        result = await fetcher.fetch("https://app.empresa.com.br/")

    assert result is not None
    assert result.rendered is False
    assert result.html == SPA_SHELL_HTML


# --------------------------------------------------------------------------- #
# extract — classificação de fonte
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://empresa.com.br/carreiras", SourceKind.PAGINA_CARREIRAS),
        ("https://empresa.com.br/vagas/ml-engineer", SourceKind.PAGINA_CARREIRAS),
        ("https://empresa.com.br/jobs", SourceKind.PAGINA_CARREIRAS),
        ("https://empresa.gupy.io/", SourceKind.PAGINA_CARREIRAS),
        ("https://empresa.com.br/blog/inferencia", SourceKind.BLOG_TECNICO),
        ("https://blog.empresa.com.br/post", SourceKind.BLOG_TECNICO),
        ("https://docs.empresa.com.br/api", SourceKind.DOCUMENTACAO),
        ("https://empresa.com.br/", SourceKind.SITE_OFICIAL),
        ("https://empresa.com.br/produto", SourceKind.SITE_OFICIAL),
        ("https://braziljournal.com/startup-x-capta", SourceKind.NOTICIA),
        ("https://www.crunchbase.com/organization/empresa", SourceKind.DIRETORIO_STARTUP),
        ("https://www.linkedin.com/company/empresa", SourceKind.PERFIL_PUBLICO),
        # `x.com.br` é site de empresa, não o X: casamento por substring erraria.
        ("https://x.com.br/produto", SourceKind.SITE_OFICIAL),
        ("https://g1.globo.com/tecnologia/startup", SourceKind.NOTICIA),
    ],
)
def test_classificacao_de_source_kind(url: str, esperado: SourceKind):
    assert classify_source_kind(url) == esperado


def test_dominio_de_terceiro_nao_vira_site_oficial():
    kind = classify_source_kind("https://outrosite.com/pagina", company_domain="empresa.com.br")

    assert kind == SourceKind.OUTRO


# --------------------------------------------------------------------------- #
# extract — texto principal e evidências
# --------------------------------------------------------------------------- #
def test_extract_page_pega_texto_data_e_tipo():
    page = extract_page("https://empresa.com.br/blog/inferencia", BLOG_HTML)

    assert page.kind == SourceKind.BLOG_TECNICO
    assert "Triton Inference Server" in page.text
    assert "Todos os direitos reservados" not in page.text  # rodapé fora do conteúdo
    assert page.published_at is not None
    assert page.published_at.year == 2025 and page.published_at.month == 3


def test_evidencia_tem_excerpt_literal_e_source_kind_correto():
    """O invariante do projeto: o trecho precisa existir, palavra por palavra, na fonte."""
    page = extract_page("https://empresa.com.br/blog/inferencia", BLOG_HTML)

    evidencias = evidences_for_keywords(page, ["triton"])

    assert len(evidencias) == 1
    evidencia = evidencias[0]
    assert evidencia.kind == SourceKind.BLOG_TECNICO
    assert "Triton Inference Server" in evidencia.excerpt
    assert " ".join(evidencia.excerpt.split()) in " ".join(page.text.split())
    assert evidencia.published_at == page.published_at
    assert str(evidencia.url) == "https://empresa.com.br/blog/inferencia"


def test_evidencia_parafraseada_e_rejeitada():
    page = extract_page("https://empresa.com.br/blog/inferencia", BLOG_HTML)

    with pytest.raises(ValueError, match="literal"):
        evidence_from_page(page, "A empresa afirma que usa GPUs proprias para inferencia.")


def test_evidencia_curta_demais_e_rejeitada():
    with pytest.raises(ValueError, match="curto"):
        make_evidence("https://empresa.com.br/", "usa IA")


def test_keywords_sem_ocorrencia_nao_inventam_evidencia():
    page = extract_page("https://empresa.com.br/blog/inferencia", BLOG_HTML)

    assert evidences_for_keywords(page, ["blockchain", "metaverso"]) == []


def test_evidencia_de_pagina_de_carreiras_herda_o_tipo_certo():
    page = extract_page("https://empresa.com.br/carreiras", CAREERS_HTML)

    assert page.kind == SourceKind.PAGINA_CARREIRAS


# --------------------------------------------------------------------------- #
# extract — extrações estruturadas
# --------------------------------------------------------------------------- #
def test_extract_career_links_absolutiza_e_cobre_ats_externo():
    links = extract_career_links(CAREERS_HTML, "https://empresa.com.br/trabalhe-conosco")

    assert "https://empresa.com.br/vagas/machine-learning-engineer" in links
    assert "https://empresa.gupy.io/jobs/123" in links
    assert "https://empresa.com.br/sobre" not in links


def test_extract_job_titles_filtra_vaga_nao_tecnica():
    titulos = extract_job_titles(CAREERS_HTML)

    assert "Machine Learning Engineer (Senior)" in titulos
    assert "Engenheiro de Dados Pleno" in titulos
    assert "Executivo de Contas" not in titulos


def test_extract_client_names_le_o_alt_dos_logos():
    """O nome do cliente só existe no `alt` — extrator de texto principal descarta."""
    nomes = extract_client_names(CLIENTS_HTML)

    assert "Banco Neon" in nomes
    assert "Magazine Luiza" in nomes  # prefixo "Logo" removido
    assert "Grupo Fleury" in nomes
    assert "NVIDIA Inception" in nomes


def test_extract_tech_mentions_respeita_fronteira_de_palavra():
    texto = "Usamos Triton e TensorRT-LLM. O fragmento de dados nao conta como rag."

    mencoes = extract_tech_mentions(texto)

    assert "triton" in mencoes
    assert "tensorrt-llm" in mencoes
    assert "rag" in mencoes  # ocorrência real, não dentro de "fragmento"
    assert extract_tech_mentions("um fragmento qualquer") == []


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
class FakeTavily:
    """Dublê do cliente Tavily: registra queries e devolve payload combinado."""

    def __init__(self, payloads: dict[str, list[dict[str, Any]]], falha: str | None = None) -> None:
        self._payloads = payloads
        self._falha = falha
        self.queries: list[str] = []

    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(query)
        if query == self._falha:
            raise RuntimeError("cota estourada")
        return {"results": self._payloads.get(query, [])}


def test_sem_chave_o_erro_e_explicito(tmp_path: Path):
    """Busca vazia por falta de credencial se parece com 'nenhuma startup achada'."""
    with pytest.raises(MissingSearchKeyError):
        TavilySearch(make_settings(tmp_path, tavily_api_key=""))


async def test_dedupe_por_dominio(tmp_path: Path):
    fake = FakeTavily(
        {
            "startups de visao computacional brasil": [
                {"url": "https://alfa.com.br/", "title": "Alfa", "content": "x", "score": 0.9},
                {"url": "https://alfa.com.br/sobre", "title": "Sobre", "score": 0.4},
                {"url": "https://beta.com.br/", "title": "Beta", "content": "z", "score": 0.7},
            ]
        }
    )
    search = TavilySearch(make_settings(tmp_path), client=fake)

    candidatos = await search.search(["startups de visao computacional brasil"])

    assert [c.domain for c in candidatos] == ["alfa.com.br", "beta.com.br"]
    assert candidatos[0].url == "https://alfa.com.br/"  # o de maior score do domínio


async def test_dominios_irrelevantes_sao_filtrados(tmp_path: Path):
    fake = FakeTavily(
        {
            "q": [
                {"url": "https://www.linkedin.com/company/alfa", "score": 0.99},
                {"url": "https://pt.wikipedia.org/wiki/Alfa", "score": 0.95},
                {"url": "https://alfa.com.br/", "score": 0.4},
            ]
        }
    )
    search = TavilySearch(
        make_settings(tmp_path),
        client=fake,
        blocked_domains=DEFAULT_BLOCKED_DOMAINS | {"linkedin.com"},
    )

    candidatos = await search.search(["q"])

    assert [c.domain for c in candidatos] == ["alfa.com.br"]


async def test_query_com_erro_nao_derruba_o_lote(tmp_path: Path):
    fake = FakeTavily(
        {"boa": [{"url": "https://alfa.com.br/", "score": 0.8}]},
        falha="ruim",
    )
    search = TavilySearch(make_settings(tmp_path), client=fake)

    candidatos = await search.search(["boa", "ruim"])

    assert [c.domain for c in candidatos] == ["alfa.com.br"]
    assert sorted(fake.queries) == ["boa", "ruim"]


async def test_urls_repetidas_entre_queries_contam_uma_vez(tmp_path: Path):
    resultado = [{"url": "https://alfa.com.br/", "score": 0.8}]
    fake = FakeTavily({"q1": resultado, "q2": resultado})
    search = TavilySearch(make_settings(tmp_path), client=fake)

    candidatos = await search.search(["q1", "q2"])

    assert len(candidatos) == 1


def test_is_blocked_cobre_subdominio():
    assert is_blocked("br.linkedin.com", {"linkedin.com"}) is True
    assert is_blocked("linkedin.com.br", {"linkedin.com"}) is False


def test_dedupe_por_dominio_pode_manter_mais_de_um():
    candidatos = [
        SearchCandidate(url="https://alfa.com.br/a", score=0.9),
        SearchCandidate(url="https://alfa.com.br/b", score=0.8),
        SearchCandidate(url="https://alfa.com.br/c", score=0.1),
    ]

    mantidos = dedupe_by_domain(candidatos, per_domain=2)

    assert [c.url for c in mantidos] == ["https://alfa.com.br/a", "https://alfa.com.br/b"]


# --------------------------------------- veiculo de midia versus empresa


def test_portal_de_midia_e_reconhecido_pela_densidade_de_artigos():
    """Motivado por um falso positivo real: "Saúde Business".

    O porteiro da descoberta só vê domínio, URL e título, e um portal setorial
    não é denunciado por nenhum dos três. A forma da home denuncia.
    """
    from radar.scraping.extract import parece_veiculo_de_midia

    portal = "".join(
        f'<a href="/noticias/2026/09/materia-{i}">titulo {i}</a>' for i in range(20)
    )
    assert parece_veiculo_de_midia(portal, "https://saudebusiness.com.br/")


def test_site_de_empresa_com_blog_ativo_nao_e_falso_positivo():
    """Limiar calibrado alto: recusar empresa de verdade custa mais que deixar
    passar um portal, porque o extractor ainda tem chance de corrigir."""
    from radar.scraping.extract import parece_veiculo_de_midia

    empresa = (
        '<a href="/produto">Produto</a><a href="/precos">Preços</a>'
        '<a href="/contato">Contato</a><a href="/carreiras">Vagas</a>'
        + "".join(f'<a href="/blog/post-{i}">post {i}</a>' for i in range(5))
    )
    assert not parece_veiculo_de_midia(empresa, "https://startup.com.br/")


def test_links_externos_nao_contam():
    """Empresa que linka notícias sobre si mesma não vira portal por isso."""
    from radar.scraping.extract import conta_links_de_artigo

    html = "".join(
        f'<a href="https://exame.com/noticias/2026/09/sobre-nos-{i}">saiu na imprensa</a>'
        for i in range(20)
    )
    assert conta_links_de_artigo(html, "https://startup.com.br/") == 0
