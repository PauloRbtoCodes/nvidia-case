"""Registry de prompts versionados em disco.

Por que arquivo e não string no código: `Classification.prompt_version` grava
qual prompt produziu a saída. Sem versão explícita e imutável, não dá para
comparar a precisão do classificador entre duas redações do prompt — e a
avaliação da semana 4 (labels manuais de ~50 startups) depende exatamente disso.

Prompt é conteúdo, não código: mora em `prompts/<nome>_<versao>.md`, com front
matter YAML declarando nome, versão e variáveis. Editar um prompt já usado em
avaliação é proibido por convenção — cria-se `_v2`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from radar.llm.errors import PromptNotFoundError, PromptRenderError

log = structlog.get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

#: Placeholders usam `{{var}}` em vez de `str.format`. Motivo prático: quase todo
#: prompt daqui carrega exemplo de JSON, e `{` de format colidiria com as chaves
#: do exemplo, exigindo escapar tudo em dobro.
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_SECTION = re.compile(r"^=== (SYSTEM|USER) ===\s*$", re.MULTILINE)
_VERSION_SUFFIX = re.compile(r"_v(\d+)\Z")


@dataclass(frozen=True)
class RenderedPrompt:
    """Prompt pronto para virar mensagens, carregando a versão que o gerou.

    A versão viaja junto até o modelo Pydantic de saída para que a linha no
    Postgres saiba com qual redação foi produzida.
    """

    system: str
    user: str
    prompt_version: str


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user: str
    description: str = ""
    variables: tuple[str, ...] = ()
    path: Path | None = None

    @property
    def prompt_version(self) -> str:
        """Identificador gravado nos modelos: `extractor_v1`."""
        return f"{self.name}_{self.version}"

    def render(self, **variables: Any) -> RenderedPrompt:
        """Substitui `{{var}}` e recusa placeholder não resolvido.

        Deixar um `{{gap_axis}}` vazar literalmente para o modelo não gera erro:
        gera uma resposta plausível e errada, que é o modo de falha mais caro.
        """
        missing = [v for v in self.variables if v not in variables]
        if missing:
            raise PromptRenderError(
                f"Prompt '{self.prompt_version}' exige {missing}, não fornecido(s). "
                f"Variáveis declaradas: {list(self.variables)}"
            )

        rendered = {
            part: _PLACEHOLDER.sub(
                lambda m: _stringify(variables[m.group(1)])
                if m.group(1) in variables
                else m.group(0),
                text,
            )
            for part, text in (("system", self.system), ("user", self.user))
        }

        for part, text in rendered.items():
            leftover = _PLACEHOLDER.findall(text)
            if leftover:
                raise PromptRenderError(
                    f"Prompt '{self.prompt_version}' ({part}) tem placeholder não resolvido: "
                    f"{sorted(set(leftover))}"
                )

        return RenderedPrompt(
            system=rendered["system"].strip(),
            user=rendered["user"].strip(),
            prompt_version=self.prompt_version,
        )


def _stringify(value: Any) -> str:
    """Lista vira bullet list — é como o modelo lê melhor, e evita `['a', 'b']` cru."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


class PromptRegistry:
    """Carrega prompts por nome+versão a partir de um diretório."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or PROMPTS_DIR
        self._cache: dict[tuple[str, str], PromptTemplate] = {}

    def path_for(self, name: str, version: str) -> Path:
        return self.directory / f"{name}_{version}.md"

    def available_versions(self, name: str) -> list[str]:
        """Ordenado do mais antigo ao mais novo — `latest` sai do fim desta lista."""
        versions = []
        for path in self.directory.glob(f"{name}_v*.md"):
            match = _VERSION_SUFFIX.search(path.stem)
            if match:
                versions.append((int(match.group(1)), f"v{match.group(1)}"))
        return [v for _, v in sorted(versions)]

    def get(self, name: str, version: str = "latest") -> PromptTemplate:
        """Falha explicitamente quando a versão não existe.

        Cair para outra versão silenciosamente inutilizaria `prompt_version`
        como registro do que de fato rodou.
        """
        if version == "latest":
            available = self.available_versions(name)
            if not available:
                raise PromptNotFoundError(
                    f"Nenhum prompt '{name}' em {self.directory}. "
                    f"Disponíveis: {sorted(self.list_prompts())}"
                )
            version = available[-1]

        key = (name, version)
        if key in self._cache:
            return self._cache[key]

        path = self.path_for(name, version)
        if not path.is_file():
            raise PromptNotFoundError(
                f"Prompt '{name}_{version}' não encontrado em {path}. "
                f"Versões disponíveis de '{name}': {self.available_versions(name) or 'nenhuma'}"
            )

        template = _parse_prompt_file(path)
        if template.name != name or template.version != version:
            raise PromptNotFoundError(
                f"Front matter de {path} declara '{template.prompt_version}', "
                f"mas o arquivo se chama '{name}_{version}'. Nome e versão precisam bater, "
                "senão o registro de prompt_version mente."
            )

        self._cache[key] = template
        log.debug("prompt_carregado", prompt_version=template.prompt_version, path=str(path))
        return template

    def list_prompts(self) -> set[str]:
        names = set()
        for path in self.directory.glob("*_v*.md"):
            names.add(_VERSION_SUFFIX.sub("", path.stem))
        return names

    def render(self, name: str, version: str = "latest", **variables: Any) -> RenderedPrompt:
        return self.get(name, version).render(**variables)


def _parse_prompt_file(path: Path) -> PromptTemplate:
    text = path.read_text(encoding="utf-8")

    match = _FRONT_MATTER.match(text)
    if not match:
        raise PromptNotFoundError(f"{path} não tem front matter YAML delimitado por '---'.")

    meta = yaml.safe_load(match.group(1)) or {}
    body = text[match.end() :]

    parts = _SECTION.split(body)
    # split devolve [pré, marcador, conteúdo, marcador, conteúdo, ...]
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].lower()] = parts[i + 1]

    if "system" not in sections or "user" not in sections:
        raise PromptNotFoundError(
            f"{path} precisa das seções '=== SYSTEM ===' e '=== USER ==='. "
            f"Encontradas: {sorted(sections)}"
        )

    return PromptTemplate(
        name=str(meta.get("name", "")),
        version=str(meta.get("version", "")),
        system=sections["system"].strip(),
        user=sections["user"].strip(),
        description=str(meta.get("description", "")),
        variables=tuple(meta.get("variables") or ()),
        path=path,
    )


@lru_cache
def get_prompt_registry() -> PromptRegistry:
    """Instância única — os prompts não mudam em runtime."""
    return PromptRegistry()
