"""Catalogo de providers e modelos, e sincronizacao com customModels do Droid.

Fontes:
- models.dev: lista de providers, base URL e metadados dos modelos (contexto,
  saida, imagem). Cache local de 24h.
- GET <base_url>/models do provider: quais modelos a key realmente acessa.
- `droid exec --help`: modelos nativos da Factory.
"""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import core

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_DIR = os.path.join(core.app_dir("XDG_CACHE_HOME", (".cache",)), "cache")
MODELS_DEV_CACHE = os.path.join(CACHE_DIR, "models.dev.json")
CACHE_TTL = 24 * 3600

DROID_KINDS = ("generic-chat-completion-api", "openai", "anthropic")
EFFORTS = ("off", "none", "minimal", "low", "medium", "high", "xhigh", "max")

# Providers do models.dev sem `api` (usam o endpoint oficial do SDK).
BASE_URL_OVERRIDES = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}


def _session_header(model):
    # Mantem a mesma sessao no OpenCode, o que aumenta o cache hit.
    return {"x-opencode-session": f"droid-tier-{model}"}


EXTRA_HEADERS = {"opencode": _session_header, "opencode-go": _session_header}


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    kind: str = "generic-chat-completion-api"
    doc: str = ""


@dataclass
class Model:
    id: str
    name: str = ""
    context: int | None = None
    output: int | None = None
    image: bool | None = None
    live: bool | None = None  # True: veio do /models do provider
    reasoning: bool | None = None

    @property
    def label(self):
        return self.name or self.id


@dataclass
class NativeModel:
    id: str
    name: str
    deprecated: bool = False
    efforts: list = field(default_factory=list)  # vazio = desconhecido
    pool: str = field(init=False)

    def __post_init__(self):
        self.pool = "core" if self.id.startswith(core.CORE_PREFIXES) else "standard"


# ---------------------------------------------------------------- models.dev

def load_models_dev(refresh=False, timeout=30):
    fresh = os.path.exists(MODELS_DEV_CACHE) and time.time() - os.path.getmtime(MODELS_DEV_CACHE) < CACHE_TTL
    if not refresh and fresh:
        with open(MODELS_DEV_CACHE, encoding="utf-8") as f:
            return json.load(f)
    try:
        req = urllib.request.Request(MODELS_DEV_URL, headers={"User-Agent": "droid-tier"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        if os.path.exists(MODELS_DEV_CACHE):  # velho, mas melhor que nada
            with open(MODELS_DEV_CACHE, encoding="utf-8") as f:
                return json.load(f)
        raise
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(MODELS_DEV_CACHE, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def droid_kind(npm):
    if npm == "@ai-sdk/anthropic":
        return "anthropic"
    if npm == "@ai-sdk/openai":
        return "openai"
    return "generic-chat-completion-api"


def providers(md):
    out = []
    for pid, p in md.items():
        base = p.get("api") or BASE_URL_OVERRIDES.get(pid)
        if not base:
            continue
        out.append(Provider(pid, p.get("name") or pid, base.rstrip("/"), droid_kind(p.get("npm")), p.get("doc") or ""))
    return sorted(out, key=lambda p: p.name.lower())


def md_models(md, provider_id):
    out = {}
    for mid, m in ((md.get(provider_id) or {}).get("models") or {}).items():
        limit = m.get("limit") or {}
        inputs = (m.get("modalities") or {}).get("input") or []
        out[mid] = Model(mid, m.get("name") or mid, limit.get("context"), limit.get("output"),
                         "image" in inputs if inputs else m.get("attachment"), reasoning=m.get("reasoning"))
    return out


# ---------------------------------------------------------------- provider ao vivo

def fetch_live_models(base_url, api_key, kind, timeout=20):
    """IDs de GET <base>/models, ou None se o provider nao responder."""
    url = base_url.rstrip("/") + ("/v1/models" if kind == "anthropic" and not base_url.rstrip("/").endswith("/v1")
                                  else "/models")
    headers = {"Accept": "application/json", "User-Agent": "droid-tier"}
    if kind == "anthropic":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [m["id"] for m in items if isinstance(m, dict) and m.get("id")]


def list_models(provider, api_key, md):
    """Modelos do provider: lista ao vivo quando da, metadados do models.dev.

    Devolve (modelos, veio_ao_vivo)."""
    meta = md_models(md, provider.id)
    live = fetch_live_models(provider.base_url, api_key, provider.kind) if api_key else None
    if live is None:
        return sorted(meta.values(), key=lambda m: m.id), False
    out = []
    for mid in live:
        m = meta.get(mid) or Model(mid)
        m.live = True
        out.append(m)
    return sorted(out, key=lambda m: m.id), True


# ---------------------------------------------------------------- modelos nativos

def native_models(droid_bin=None):
    """Modelos nativos da Factory, lidos do `droid exec --help`."""
    droid_bin = droid_bin or shutil.which("droid")
    if not droid_bin:
        return []
    try:
        out = subprocess.run([droid_bin, "exec", "--help"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_native_models(out)


def parse_native_models(help_text):
    models, section = [], None
    efforts = {}  # nome de exibicao -> esforcos, do bloco "Model details"
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped in ("Available Models:", "Model details:", "Custom Models:"):
            section = stripped
            continue
        if section == "Available Models:":
            m = re.match(r"^\s{2,}([a-z0-9][a-z0-9.\-]*)\s{2,}(.+?)\s*$", line)
            if not m:
                if models:
                    section = None
                continue
            name = m.group(2)
            deprecated = "[Deprecated]" in name
            name = name.replace("[Deprecated]", "").replace("(default)", "").strip()
            models.append(NativeModel(m.group(1), name, deprecated))
        elif section == "Model details:":
            m = re.match(r"^\s*-\s*(.+?):\s*supports reasoning:.*?supported:\s*\[([^\]]*)\]", line)
            if m:
                efforts[m.group(1).strip()] = [e.strip() for e in m.group(2).split(",") if e.strip()]
    for model in models:
        model.efforts = efforts.get(model.name, [])
    return models


# Sem modelo nativo equivalente, o Droid aceita estes esforcos num customModel
# que tenha reasoningEffort; sem reasoningEffort, o raciocinio fica desligado.
CUSTOM_EFFORTS = ["off", "low", "medium", "high"]


def efforts_for(model_id, natives, entry=None):
    """Esforcos que o Droid aceita para um modelo; None = desconhecido (oferecer todos)."""
    native = next((n for n in natives if n.id == model_id), None)
    if entry is not None:
        base = entry.get("baseModelId") or entry.get("model")
        native = next((n for n in natives if n.id == base), None)
        if native is None:
            configured = entry.get("reasoningEffort")
            if not configured or configured == "none":
                return ["none"]
            return CUSTOM_EFFORTS + ([configured] if configured not in CUSTOM_EFFORTS else [])
    if native is not None and native.efforts:
        return native.efforts
    return None


# ---------------------------------------------------------------- customModels

def normalize_display(name):
    # Igual ao Droid: trim + espacos viram "-".
    return re.sub(r"\s+", "-", name.strip())


def custom_entry(provider, model, api_key, prefix):
    entry = {
        "model": model.id,
        "baseUrl": provider.base_url,
        "apiKey": api_key,
        "provider": provider.kind,
        "displayName": f"{prefix} {model.label}".strip(),
    }
    if model.context:
        entry["maxContextLimit"] = int(model.context)
    if model.output:
        entry["maxOutputTokens"] = int(model.output)
    if model.image is not None:
        entry["noImageSupport"] = not model.image
    if model.reasoning:
        # Sem isso, um modelo que nao tem o mesmo nome de um nativo da Factory
        # (ex.: z-ai/glm-5.3 no OpenRouter) roda com o raciocinio desligado.
        entry["reasoningEffort"] = "high"
    headers = EXTRA_HEADERS.get(provider.id)
    if headers:
        entry["extraHeaders"] = headers(model.id)
    return entry


def provider_entries(settings, base_url):
    return [m for m in settings.get("customModels") or [] if core.norm_url(m.get("baseUrl")) == core.norm_url(base_url)]


def provider_api_key(settings, base_url):
    for m in provider_entries(settings, base_url):
        if m.get("apiKey"):
            return m["apiKey"]
    return ""


def sync_custom_models(settings, provider, selected, api_key, prefix, managed):
    """Deixa em customModels exatamente os `selected` deste provider.

    Remove so entradas que o droid-tier cadastrou antes (`managed`); as que a
    pessoa pos a mao ficam. Entradas existentes mantem o id, para nao quebrar
    referencias no settings. Devolve (adicionados, removidos)."""
    models = settings.setdefault("customModels", [])
    wanted = {m.id: m for m in selected}
    base = core.norm_url(provider.base_url)
    added, removed, kept = [], [], []
    for entry in models:
        same = core.norm_url(entry.get("baseUrl")) == base
        if same and entry.get("model") in wanted:
            entry["apiKey"] = api_key
            if wanted[entry["model"]].reasoning and "reasoningEffort" not in entry:
                entry["reasoningEffort"] = "high"
            kept.append(entry)
            wanted.pop(entry["model"])
        elif same and entry.get("model") in managed:
            removed.append(entry["model"])
        else:
            kept.append(entry)
    for mid in sorted(wanted):
        entry = custom_entry(provider, wanted[mid], api_key, prefix)
        norm = normalize_display(entry["displayName"])
        n = sum(1 for e in kept if normalize_display(e.get("displayName") or "") == norm)
        entry["id"] = f"custom:{norm}-{n}"
        kept.append(entry)
        added.append(mid)
    for i, entry in enumerate(kept):
        entry["index"] = i
    settings["customModels"] = kept
    return added, removed


def in_use(settings, home, ids):
    """Quais ids de customModels estao em uso pelos padroes ou pelo settings atual."""
    used = {model for model, _ in core.snapshot(settings).values()}
    if home:
        used |= {model for model, _ in home.values()}
    return sorted(set(ids) & used)
