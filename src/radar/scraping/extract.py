"""HTML → texto e evidências.

Duas ferramentas com papéis distintos, de propósito:

* `trafilatura` para o texto principal (post de blog, notícia, página
  institucional). Ele resolve o problema difícil de separar conteúdo de menu,
  rodapé e banner de cookie, e ainda entrega a data de publicação.
* `BeautifulSoup` para o que é estrutura, não prosa: links de carreira, títulos
  de vaga, logos de clientes. Esses sinais moram em atributos (`href`, `alt`) que
  qualquer extrator de texto principal descarta — e vaga de MLE é o sinal mais
  honesto de stack que existe.

Regra inviolável deste módulo: todo `excerpt` é literal. Nada aqui reescreve,
resume ou normaliza semanticamente um trecho — no máximo colapsa espaços em
branco (o próprio `Evidence` já faz isso) ou corta o fim. Um trecho parafraseado
deixa de ser prova e vira opinião com aparência de citação.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import structlog
import trafilatura
from bs4 import BeautifulSoup
from trafilatura.metadata import extract_metadata

from radar.models.evidence import Evidence, SourceKind

log = structlog.get_logger(__name__)

#: Espelha o `min_length` de `Evidence.excerpt`: filtramos antes para não gastar
#: uma exceção de validação em cada fragmento curto encontrado.
MIN_EXCERPT_CHARS = 20
MAX_EXCERPT_CHARS = 2000

CAREER_PATH_HINTS: tuple[str, ...] = (
    "carreira",
    "carreiras",
    "vaga",
    "vagas",
    "job",
    "jobs",
    "career",
    "careers",
    "trabalhe-conosco",
    "trabalhe",
    "oportunidade",
    "join-us",
    "work-with-us",
    "gupy.io",
    "greenhouse.io",
    "lever.co",
    "workable.com",
    "solides.com",
)
BLOG_PATH_HINTS: tuple[str, ...] = (
    "/blog",
    "/engineering",
    "/tech",
    "/artigos",
    "/insights",
    "/posts",
    "/news/eng",
)
DOCS_HINTS: tuple[str, ...] = ("/docs", "/documentation", "/developers", "/api-reference", "/sdk")
NEWS_HOST_HINTS: tuple[str, ...] = (
    "braziljournal",
    "startups.com.br",
    "neofeed",
    "exame",
    "valor",
    "infomoney",
    "techcrunch",
    "canaltech",
    "mobiletime",
    "baguete",
    "globo.com",
    "estadao",
    "folha",
)
DIRECTORY_HOST_HINTS: tuple[str, ...] = (
    "crunchbase",
    "distrito.me",
    "abstartups",
    "startupbase",
    "f6s.com",
    "dealroom",
    "pitchbook",
    "latamlist",
    "sling.com.br",
)
PROFILE_HOST_HINTS: tuple[str, ...] = (
    "linkedin.com",
    "github.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "facebook.com",
    "youtube.com",
    "medium.com",
)

#: Vocabulário de menções técnicas. Curto e explícito de propósito: uma lista que
#: tenta cobrir tudo produz falso positivo ("python" em qualquer página) e o
#: sinal que interessa ao Defensibility Score é o específico (Triton, vLLM, GPU).
TECH_VOCABULARY: tuple[str, ...] = (
    "openai",
    "gpt-4",
    "gpt-4o",
    "anthropic",
    "claude",
    "gemini",
    "llama",
    "mistral",
    "nemotron",
    "hugging face",
    "huggingface",
    "fine-tuning",
    "fine tuning",
    "fine-tunado",
    "rag",
    "embedding",
    "embeddings",
    "vector database",
    "banco vetorial",
    "pinecone",
    "qdrant",
    "weaviate",
    "milvus",
    "pgvector",
    "langchain",
    "llamaindex",
    "langgraph",
    "triton",
    "tensorrt",
    "tensorrt-llm",
    "nvidia nim",
    "cuda",
    "gpu",
    "h100",
    "a100",
    "l40s",
    "vllm",
    "sglang",
    "ollama",
    "pytorch",
    "tensorflow",
    "kubernetes",
    "kubeflow",
    "mlflow",
    "sagemaker",
    "bedrock",
    "vertex ai",
    "databricks",
    "snowflake",
    "airflow",
    "dbt",
    "spark",
    "computer vision",
    "visao computacional",
    "nlp",
    "speech-to-text",
    "asr",
    "ocr",
)

#: Palavras que indicam vaga técnica. O Company Profile só quer papéis de
#: engenharia — vaga de vendas não diz nada sobre domínio de stack.
ROLE_KEYWORDS: tuple[str, ...] = (
    "engenheir",
    "engineer",
    "desenvolvedor",
    "developer",
    "dev ",
    "cientista de dados",
    "data scientist",
    "data engineer",
    "machine learning",
    "ml engineer",
    "mlops",
    "devops",
    "sre",
    "infra",
    "backend",
    "back-end",
    "frontend",
    "front-end",
    "fullstack",
    "full stack",
    "arquiteto",
    "architect",
    "tech lead",
    "cto",
    "analista de dados",
    "qa ",
    "software",
)

CLIENT_SECTION_PATTERNS = re.compile(
    r"(clientes?|parceiros?|cases?|customers?|partners?|trusted\s+by|quem\s+(usa|confia)|"
    r"empresas\s+que)",
    re.IGNORECASE,
)

_JUNK_LOGO_WORDS = re.compile(
    r"^(logo|logotipo|icon|ícone|imagem|image|img|banner|placeholder)[\s\-_:]*", re.IGNORECASE
)


@dataclass(slots=True)
class ExtractedPage:
    """Resultado da extração de uma página: o texto e sua procedência."""

    url: str
    kind: SourceKind
    text: str
    title: str | None = None
    published_at: datetime | None = None
    html: str = field(default="", repr=False)

    @property
    def is_empty(self) -> bool:
        return len(self.text.strip()) < MIN_EXCERPT_CHARS


# --------------------------------------------------------------------------- #
# Classificação de fonte
# --------------------------------------------------------------------------- #
def _host_matches(host: str, hints: tuple[str, ...]) -> bool:
    """Casa host por domínio ou por rótulo inteiro, nunca por substring.

    Substring produz falso positivo silencioso e caro: `x.com.br` (site legítimo
    de startup) conteria `x.com` e a página viraria PERFIL_PUBLICO, com confiança
    0,60 em vez de 0,85. Erro de classificação aqui vira erro de score adiante.
    """
    labels = host.split(".")
    return any(
        host == hint or host.endswith(f".{hint}") or hint in labels for hint in hints
    )


def classify_source_kind(url: str, *, company_domain: str | None = None) -> SourceKind:
    """Deduz o `SourceKind` a partir da URL.

    Heurística deliberadamente simples e auditável: o tipo de fonte entra no
    cálculo de confiança (`SOURCE_TRUST`), e uma regra que ninguém consegue
    explicar em uma frase vira peso arbitrário disfarçado de método.

    `company_domain` desempata o caso genérico: sem ele, assumimos que a página
    é do site da própria empresa, que é o caminho por onde o Scraper chega aqui.
    """
    parts = urlsplit(url.lower())
    host = parts.netloc.split("@")[-1].split(":")[0].removeprefix("www.")
    path = parts.path or "/"
    full = f"{host}{path}"

    if _host_matches(host, PROFILE_HOST_HINTS):
        return SourceKind.PERFIL_PUBLICO
    if _host_matches(host, DIRECTORY_HOST_HINTS):
        return SourceKind.DIRETORIO_STARTUP
    if _host_matches(host, NEWS_HOST_HINTS):
        return SourceKind.NOTICIA

    # Carreira antes de blog: `/blog/vagas-abertas` é conteúdo de recrutamento.
    if any(hint in full for hint in CAREER_PATH_HINTS):
        return SourceKind.PAGINA_CARREIRAS
    if host.startswith("docs.") or any(hint in path for hint in DOCS_HINTS):
        return SourceKind.DOCUMENTACAO
    if any(hint in path for hint in BLOG_PATH_HINTS) or host.startswith("blog."):
        return SourceKind.BLOG_TECNICO

    if company_domain:
        expected = company_domain.lower().removeprefix("www.")
        if host != expected and not host.endswith(f".{expected}"):
            return SourceKind.OUTRO
    return SourceKind.SITE_OFICIAL


# --------------------------------------------------------------------------- #
# Texto principal
# --------------------------------------------------------------------------- #
def visible_text(html: str) -> str:
    """Texto bruto visível, sem tentar isolar o conteúdo principal.

    Serve para a heurística de "a página veio vazia?" no fetch, onde o que
    importa é a quantidade de texto e não sua qualidade.
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _parse_published_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        log.debug("data_publicacao_ilegivel", raw=raw)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def extract_page(url: str, html: str, *, company_domain: str | None = None) -> ExtractedPage:
    """HTML → `ExtractedPage`, com data de publicação quando o site expõe uma."""
    kind = classify_source_kind(url, company_domain=company_domain)
    text = ""
    title: str | None = None
    published_at: datetime | None = None

    if html and html.strip():
        text = (
            trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
            or ""
        )
        try:
            meta = extract_metadata(html, default_url=url)
        except Exception as exc:  # metadata é best-effort; nunca deve derrubar a coleta
            log.debug("metadata_falhou", url=url, error=str(exc))
            meta = None
        if meta is not None:
            title = meta.title or None
            published_at = _parse_published_at(meta.date)

        # Página estruturada (lista de vagas, grade de logos) frequentemente não
        # tem "conteúdo principal" para o trafilatura, mas ainda carrega sinal.
        if len(text.strip()) < MIN_EXCERPT_CHARS:
            text = visible_text(html)

    return ExtractedPage(
        url=url,
        kind=kind,
        text=text.strip(),
        title=title,
        published_at=published_at,
        html=html,
    )


# --------------------------------------------------------------------------- #
# Evidências
# --------------------------------------------------------------------------- #
def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def make_evidence(
    url: str,
    excerpt: str,
    *,
    kind: SourceKind | None = None,
    context: str | None = None,
    published_at: datetime | None = None,
    source_text: str | None = None,
) -> Evidence:
    """Constrói uma `Evidence` garantindo que o trecho é literal.

    Quando `source_text` é informado, verificamos que o trecho ocorre nele. É a
    trava mecânica contra o modo de falha mais caro do projeto: alguém (ou um
    LLM, adiante na pipeline) "melhorar" a citação e o briefing passar a exibir
    uma frase que a startup nunca escreveu.
    """
    cleaned = " ".join(excerpt.split())[:MAX_EXCERPT_CHARS]
    if len(cleaned) < MIN_EXCERPT_CHARS:
        raise ValueError(f"trecho curto demais para virar evidencia: {cleaned!r}")

    if source_text is not None and _normalize(cleaned) not in _normalize(source_text):
        raise ValueError("excerpt nao encontrado na fonte — evidencia precisa ser literal")

    return Evidence(
        url=url,  # type: ignore[arg-type]  # pydantic converte str → HttpUrl
        kind=kind or SourceKind.OUTRO,
        excerpt=cleaned,
        context=context,
        published_at=published_at,
    )


def evidence_from_page(
    page: ExtractedPage, excerpt: str, *, context: str | None = None
) -> Evidence:
    """Evidência amarrada a uma página já extraída — herda tipo, data e validação."""
    return make_evidence(
        page.url,
        excerpt,
        kind=page.kind,
        context=context or page.title,
        published_at=page.published_at,
        source_text=page.text,
    )


def evidences_for_keywords(
    page: ExtractedPage,
    keywords: list[str] | tuple[str, ...],
    *,
    max_evidences: int = 5,
) -> list[Evidence]:
    """Parágrafos da página que mencionam algum termo de interesse.

    Trabalhamos em parágrafo e não em sentença porque o trecho precisa fazer
    sentido sozinho no briefing: "usamos Triton" isolado não prova nada, o
    parágrafo em volta prova.
    """
    if not page.text:
        return []

    needles = [k.casefold() for k in keywords if k.strip()]
    found: list[Evidence] = []
    seen: set[str] = set()

    for block in page.text.split("\n"):
        paragraph = " ".join(block.split())
        if len(paragraph) < MIN_EXCERPT_CHARS:
            continue
        lowered = paragraph.casefold()
        if not any(needle in lowered for needle in needles):
            continue

        evidence = evidence_from_page(page, paragraph[:MAX_EXCERPT_CHARS])
        if evidence.content_hash in seen:
            continue
        seen.add(evidence.content_hash)
        found.append(evidence)
        if len(found) >= max_evidences:
            break

    return found


# --------------------------------------------------------------------------- #
# Extrações estruturadas (BeautifulSoup)
# --------------------------------------------------------------------------- #
def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def extract_career_links(html: str, base_url: str) -> list[str]:
    """Links que levam a páginas de vaga, absolutos e deduplicados.

    Cobre também ATSs externos (Gupy, Greenhouse, Lever): startup brasileira
    raramente hospeda o próprio quadro de vagas.
    """
    links: list[str] = []
    seen: set[str] = set()

    for anchor in _soup(html).find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        label = " ".join(anchor.get_text(" ", strip=True).split()).casefold()
        haystack = f"{absolute.casefold()} {label}"
        if not any(hint in haystack for hint in CAREER_PATH_HINTS):
            continue
        normalized = absolute.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(absolute)

    return links


def extract_signal_links(html: str, base_url: str, *, same_domain_only: bool = False) -> list[str]:
    """Links internos que costumam carregar sinal técnico: blog, preços, clientes.

    A home institucional é a página que menos diz sobre stack — ela é escrita
    para investidor e comprador. O sinal mora um clique adiante: o post de
    engenharia (a fonte de maior confiança em `SOURCE_TRUST`), a página de preços
    (que revela modelo de cobrança e, com ele, o volume de inferência) e a de
    clientes (que sustenta o eixo de distribuição).

    Carreira sai por `extract_career_links`, que é caso à parte: precisa seguir
    para ATS de terceiros (Gupy, Lever), e aqui filtramos justamente o que sai do
    domínio quando `same_domain_only` está ligado.
    """
    interesse: tuple[str, ...] = (
        *BLOG_PATH_HINTS,
        "/precos",
        "/preco",
        "/pricing",
        "/planos",
        "/plans",
        "/clientes",
        "/customers",
        "/cases",
        "/casos",
        *DOCS_HINTS,
    )
    origem = urlsplit(base_url).netloc.casefold().removeprefix("www.")

    links: list[str] = []
    seen: set[str] = set()
    for anchor in _soup(html).find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        partes = urlsplit(absolute.casefold())
        if partes.scheme not in ("http", "https"):
            continue
        host = partes.netloc.removeprefix("www.")
        if same_domain_only and host != origem:
            continue
        # Casamos no caminho, não na URL inteira: "blog" no domínio de um
        # agregador (`medium.com/@empresa`) não é o blog de engenharia dela.
        if not any(hint in partes.path for hint in interesse) and not host.startswith("blog."):
            continue
        normalized = absolute.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        links.append(absolute)

    return links


def extract_job_titles(html: str) -> list[str]:
    """Títulos de vagas técnicas visíveis na página.

    Varre títulos, links e itens de lista porque não há padrão de marcação: cada
    ATS renderiza a lista de vagas de um jeito, e depender de um seletor
    específico quebra na primeira mudança de layout.
    """
    titles: list[str] = []
    seen: set[str] = set()

    for element in _soup(html).find_all(["h1", "h2", "h3", "h4", "a", "li", "span"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        if not (3 < len(text) <= 90):
            continue
        lowered = text.casefold()
        if not any(keyword in lowered for keyword in ROLE_KEYWORDS):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        titles.append(text)

    return titles


def extract_client_names(html: str) -> list[str]:
    """Nomes de clientes/parceiros a partir de seções de logos.

    O nome quase sempre está no `alt` da imagem do logo — é o único lugar onde o
    site escreve "Banco X" em texto. Por isso esta extração é do BeautifulSoup e
    não do trafilatura, que descarta atributos.
    """
    soup = _soup(html)
    names: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        cleaned = _JUNK_LOGO_WORDS.sub("", " ".join(raw.split())).strip(" -–—:|")
        if not (1 < len(cleaned) <= 60):
            return
        key = cleaned.casefold()
        if key in seen or CLIENT_SECTION_PATTERNS.fullmatch(cleaned):
            return
        seen.add(key)
        names.append(cleaned)

    containers = []

    # 1) Seções cujo título anuncia clientes/parceiros.
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        if CLIENT_SECTION_PATTERNS.search(heading.get_text(" ", strip=True)):
            parent = heading.find_parent(["section", "div", "article", "aside"]) or heading.parent
            if parent is not None:
                containers.append(parent)

    # 2) Seções marcadas por classe/id, comuns em landing pages.
    for element in soup.find_all(["section", "div", "ul"], attrs={"class": True}):
        marker = " ".join(element.get("class", [])) + " " + (element.get("id") or "")
        if CLIENT_SECTION_PATTERNS.search(marker) or "logo" in marker.casefold():
            containers.append(element)

    for container in containers:
        for img in container.find_all("img"):
            _add(img.get("alt") or img.get("title") or "")
        for tag in container.find_all(["figcaption", "li", "span", "p"]):
            text = tag.get_text(" ", strip=True)
            if text and len(text.split()) <= 5:
                _add(text)

    return names


def extract_tech_mentions(text: str, vocabulary: tuple[str, ...] = TECH_VOCABULARY) -> list[str]:
    """Tecnologias citadas no texto, na forma canônica do vocabulário."""
    lowered = (text or "").casefold()
    found: list[str] = []
    for term in vocabulary:
        # Fronteira de palavra evita casar "rag" dentro de "fragmento".
        if re.search(rf"(?<![\w-]){re.escape(term)}(?![\w-])", lowered):
            found.append(term)
    return found

# --------------------------------------------------------------------------- #
# Veículo de mídia versus empresa
# --------------------------------------------------------------------------- #
#: Marcas de caminho de artigo. Um veículo publica dezenas por semana e linka
#: todas na home; uma empresa linka produto, preço e contato.
_MARCAS_DE_ARTIGO: tuple[str, ...] = (
    "/noticia",
    "/artigo",
    "/materia",
    "/reportagem",
    "/blog/",
    "/post/",
    "/coluna",
    "/entrevista",
    "/edicao",
    "/categoria",
    "/tag/",
    "/author/",
    "/autor/",
)

_DATA_NO_CAMINHO = re.compile(r"/(?:19|20)\d{2}/(?:0[1-9]|1[0-2])/")

#: Links de artigo na home a partir dos quais a página deixa de ser plausível
#: como site de empresa. Calibrado alto de propósito: uma startup com blog ativo
#: pode ter alguns, e recusar empresa de verdade custa mais que deixar passar um
#: portal — o extractor ainda tem chance de corrigir.
LIMIAR_LINKS_DE_ARTIGO = 12


def conta_links_de_artigo(html: str, base_url: str) -> int:
    """Links internos com forma de artigo na página."""
    if not html:
        return 0
    base_host = urlsplit(base_url).netloc.lower().removeprefix("www.")
    vistos: set[str] = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        alvo = urljoin(base_url, href)
        partes = urlsplit(alvo)
        host = partes.netloc.lower().removeprefix("www.")
        if host and host != base_host:
            continue
        caminho = partes.path.lower()
        if any(m in caminho for m in _MARCAS_DE_ARTIGO) or _DATA_NO_CAMINHO.search(caminho):
            vistos.add(caminho)
    return len(vistos)


def parece_veiculo_de_midia(html: str, base_url: str) -> bool:
    """Distingue portal de conteúdo de site de empresa pela forma da home.

    Motivada por um falso positivo real: "Saúde Business" — um portal de mídia do
    setor — passou pelo porteiro de descoberta, que só olha domínio, URL e
    título, e nenhum dos três a denuncia. O sinal que a denuncia está no HTML:
    **um veículo linka dezenas de artigos na própria home; uma empresa linka
    produto, preço e contato.**

    Determinístico e sem custo de rede ou de cota: a home já foi baixada, e a
    contagem roda sobre o HTML que já está em memória.
    """
    return conta_links_de_artigo(html, base_url) >= LIMIAR_LINKS_DE_ARTIGO
