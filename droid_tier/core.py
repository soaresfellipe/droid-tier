"""Nucleo do droid-tier: config, limites da Factory e escrita no settings do Droid.

Sem dependencias fora da biblioteca padrao, para o timer rodar leve.
"""
import datetime as dt
import json
import os
import tempfile
import time
import tomllib
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")


def app_dir(xdg_var, unix_default):
    """Pasta do droid-tier: XDG se definido, senao dentro do perfil do usuario.

    No Windows tambem fica fora de AppData de proposito: o Python da Microsoft Store
    redireciona gravacoes em AppData para uma pasta privada do pacote, e o config
    sumiria para o Explorer e para os outros programas."""
    return os.path.join(os.environ.get(xdg_var) or os.path.join(HOME, *unix_default), "droid-tier")


CONFIG_DIR = app_dir("XDG_CONFIG_HOME", (".config",))
CONFIG_FILE = os.environ.get("DROID_TIER_CONFIG") or os.path.join(CONFIG_DIR, "config.toml")
KEY_FILE = os.path.join(CONFIG_DIR, "factory-api-key.env")
STATE_DIR = app_dir("XDG_STATE_HOME", (".local", "state"))
PIN_FILE = os.path.join(STATE_DIR, "pin")
HOME_FILE = os.path.join(STATE_DIR, "home.json")
LOG_FILE = os.path.join(STATE_DIR, "log")
DEFAULT_SETTINGS = os.path.join(HOME, ".factory", "settings.json")
DEFAULT_API = "https://app.factory.ai"

HOME_TIER = "home"
POOLS = ("standard", "core")
WINDOWS = ("fiveHour", "weekly", "monthly")
# Prefixos dos modelos nativos que a Factory cobra no pool Droid Core.
CORE_PREFIXES = ("glm-", "deepseek-", "kimi-", "minimax-", "qwen", "nemotron-")

# papel no config.toml -> (secao do settings.json, campo do modelo, campo do esforco)
ROLES = {
    "session": ("sessionDefaultSettings", "model", "reasoningEffort"),
    "spec": ("sessionDefaultSettings", "specModeModel", "specModeReasoningEffort"),
    "subagent_light": ("subagentModelSettings", "lightModel", "lightReasoningEffort"),
    "subagent_medium": ("subagentModelSettings", "mediumModel", "mediumReasoningEffort"),
    "subagent_heavy": ("subagentModelSettings", "heavyModel", "heavyReasoningEffort"),
    "orchestrator": (None, "missionOrchestratorModel", "missionOrchestratorReasoningEffort"),
    "worker": ("missionModelSettings", "workerModel", "workerReasoningEffort"),
    "validator": ("missionModelSettings", "validationWorkerModel", "validationWorkerReasoningEffort"),
}

EXAMPLE_CONFIG = '''\
# droid-tier: fallbacks para quando os limites da Factory acabam.
#
# Os seus padroes do Droid (o que estiver no settings.json) nao entram aqui.
# Ao sair deles, o droid-tier guarda uma copia e restaura quando o limite libera.

# Troca quando qualquer janela (5h, semanal, mensal) passa deste %.
threshold = 95

# Pool que os seus padroes consomem: "standard", "core" ou "none".
# Sem esta linha, e deduzido pelos modelos (Claude/GPT/Gemini = standard,
# GLM/DeepSeek/Kimi/MiniMax/Qwen/Nemotron = core, custom: = none).
# home_pool = "standard"

# Providers de fallback. Cada modelo usado com `provider = "..."` precisa existir
# em customModels no ~/.factory/settings.json com este baseUrl.
[providers.opencode-go]
base_url = "https://opencode.ai/zen/go/v1"

# Fallbacks em ordem. Vale o primeiro com folga; um fallback sem `pool` esta
# sempre disponivel, entao deixe-o por ultimo.
# Papeis: session, spec, subagent_light, subagent_medium, subagent_heavy,
# orchestrator, worker, validator. Papel omitido fica como esta.
# Formato: "modelo" ou "modelo@esforco".

[[fallback]]
name = "droid"
pool = "core"       # modelos open source da Factory (Droid Core)
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"
subagent_light = "glm-5.3-flash@low"
subagent_medium = "glm-5.3-flash@high"
subagent_heavy = "glm-5.3-flash@max"
orchestrator = "glm-5.3@low"
worker = "glm-5.3-flash@high"
validator = "deepseek-v4.1-flash@high"

[[fallback]]
name = "oc"
provider = "opencode-go"   # os mesmos modelos, via customModels
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"
subagent_light = "glm-5.3-flash@low"
subagent_medium = "glm-5.3-flash@high"
subagent_heavy = "glm-5.3-flash@max"
orchestrator = "glm-5.3@low"
worker = "glm-5.3-flash@high"
validator = "deepseek-v4.1-flash@high"

# Avisos quando o degrau muda ou a API da Factory para de responder.
# [notify]
# desktop = true                          # toast no Windows, notify-send no Linux
# ntfy = "https://ntfy.sh/seu-topico"     # notificacao no celular pelo app ntfy
# webhook = "https://exemplo/webhook"     # POST JSON (n8n, Slack via proxy...)
'''


class ConfigError(Exception):
    pass


ECHO = True  # a TUI desliga: print no meio do Textual bagunca a tela


def log(msg):
    line = f"{dt.datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} {msg}"
    if ECHO:
        print(line)
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- config

def load_config(path=None):
    path = path or CONFIG_FILE
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} nao existe; rode `droid-tier init`")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")

    fallbacks = cfg.get("fallback") or []
    if not fallbacks:
        raise ConfigError("nenhum [[fallback]] definido")
    providers = cfg.get("providers") or {}
    home_pool = cfg.get("home_pool")
    if home_pool not in (None, "none", *POOLS):
        raise ConfigError("home_pool deve ser standard, core ou none")
    seen = {HOME_TIER}
    for i, t in enumerate(fallbacks, 1):
        name = t.get("name")
        if not name:
            raise ConfigError(f"[[fallback]] #{i} sem name")
        if name in seen:
            raise ConfigError(f"fallback {name!r} repetido ou reservado")
        seen.add(name)
        if t.get("pool") not in (None, *POOLS):
            raise ConfigError(f"fallback {name!r}: pool deve ser {' ou '.join(POOLS)}")
        if t.get("pool") and t.get("provider"):
            raise ConfigError(f"fallback {name!r}: use pool (Factory) ou provider, nao os dois")
        if t.get("provider") and t["provider"] not in providers:
            raise ConfigError(f"fallback {name!r}: provider {t['provider']!r} nao esta em [providers]")
        unknown = set(t) - {"name", "pool", "provider", *ROLES}
        if unknown:
            raise ConfigError(f"fallback {name!r}: campos desconhecidos {sorted(unknown)}")
        if not set(t) & set(ROLES):
            raise ConfigError(f"fallback {name!r} nao define nenhum papel")
    for pname, p in providers.items():
        if not p.get("base_url"):
            raise ConfigError(f"provider {pname!r} sem base_url")
    return {
        "threshold": float(cfg.get("threshold", 95)),
        "settings": os.path.expanduser(cfg.get("settings", DEFAULT_SETTINGS)),
        "api": cfg.get("factory_api", DEFAULT_API).rstrip("/"),
        "home_pool": home_pool,
        "providers": providers,
        "fallbacks": fallbacks,
        "notify": _validate_notify(cfg.get("notify")),
    }


def _validate_notify(section):
    from . import notify  # notify importa core
    return notify.validate(section)


def norm_url(u):
    return (u or "").rstrip("/").lower()


def resolve_fallback(fb, cfg, settings):
    """Fallback do config -> {papel: (id do modelo no Droid, esforco ou None)}."""
    provider = cfg["providers"].get(fb.get("provider")) if fb.get("provider") else None
    out = {}
    for role in ROLES:
        spec = fb.get(role)
        if spec is None:
            continue
        model, _, effort = spec.partition("@")
        if provider and not model.startswith("custom:"):
            model = custom_model_id(settings, provider["base_url"], model, fb["name"])
        out[role] = (model, effort or None)
    return out


def custom_model_id(settings, base_url, model, name):
    found = [m for m in settings.get("customModels") or []
             if norm_url(m.get("baseUrl")) == norm_url(base_url) and m.get("model") == model]
    if not found:
        raise ConfigError(f"fallback {name!r}: nenhum customModel com model={model!r} "
                          f"e baseUrl={base_url!r} no settings.json do Droid")
    return found[0]["id"]


def resolve_all(cfg, settings):
    return {fb["name"]: resolve_fallback(fb, cfg, settings) for fb in cfg["fallbacks"]}


def infer_pool(roles):
    """Pool que um conjunto de papeis consome; None se so usa customModels."""
    native = [m for m, _ in roles.values() if m and not m.startswith("custom:")]
    if not native:
        return None
    if all(m.startswith(CORE_PREFIXES) for m in native):
        return "core"
    return "standard"


def home_pool(cfg, home):
    if cfg["home_pool"]:
        return None if cfg["home_pool"] == "none" else cfg["home_pool"]
    return infer_pool(home)


# ---------------------------------------------------------------- limites

def api_key():
    key = os.environ.get("FACTORY_API_KEY")
    if key:
        return key
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("FACTORY_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except FileNotFoundError:
        pass
    raise ConfigError(f"sem FACTORY_API_KEY (nem no ambiente, nem em {KEY_FILE})")


def fetch_limits(cfg):
    # Endpoint nao documentado, o mesmo que o CLI do Droid consulta.
    req = urllib.request.Request(cfg["api"] + "/api/billing/limits", headers={
        "Authorization": f"Bearer {api_key()}",
        "Accept": "application/json",
        "User-Agent": "droid-tier",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return validate_limits(json.load(r))


def validate_limits(data):
    """Recusa resposta fora do formato esperado.

    Sem isso, uma mudanca no endpoint (nao documentado) viraria "nenhum limite
    estourado" e o droid-tier restauraria padroes que estao sem limite. O proprio
    Droid trata limits.standard ausente como falha."""
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        raise ValueError("resposta sem `limits`; o formato da API da Factory mudou?")
    for pool in POOLS:
        p = limits.get(pool)
        if p is None and pool == "core":
            continue  # conta sem Droid Core
        if not isinstance(p, dict) or not any(
                isinstance((p.get(w) or {}).get("usedPercent"), (int, float)) for w in WINDOWS):
            raise ValueError(f"resposta sem `limits.{pool}` com usedPercent; o formato da API da Factory mudou?")
    return limits


def pool_hits(pool, threshold, now=None):
    """Janelas ativas do pool que passaram do limiar, como 'weekly 97%'."""
    if not pool:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    hits = []
    for name in WINDOWS:
        b = pool.get(name) or {}
        pct = b.get("usedPercent")
        if pct is None or pct < threshold:
            continue
        end = b.get("windowEnd")
        if end and dt.datetime.fromisoformat(end.replace("Z", "+00:00")) <= now:
            continue  # janela ja virou, o numero e velho
        hits.append(f"{name} {pct:.0f}%")
    return hits


def pick_tier(cfg, limits, hpool, now=None):
    """'home' se o pool dos padroes tem folga; senao o primeiro fallback com folga,
    ou o ultimo se todos estourados. Devolve (nome, motivos)."""
    hits = {p: pool_hits(limits.get(p), cfg["threshold"], now) for p in POOLS}
    if not hpool or not hits[hpool]:
        return HOME_TIER, hits
    for fb in cfg["fallbacks"]:
        if not fb.get("pool") or not hits[fb["pool"]]:
            return fb["name"], hits
    return cfg["fallbacks"][-1]["name"], hits


def describe(limits):
    out = []
    for pool in POOLS:
        p = limits.get(pool) or {}
        parts = []
        for name in WINDOWS:
            b = p.get(name) or {}
            if "usedPercent" in b:
                end = f" (vira {b['windowEnd']})" if b.get("windowEnd") else ""
                parts.append(f"{name} {b['usedPercent']:.0f}%{end}")
        out.append(f"  {pool}: " + (", ".join(parts) if parts else "sem dados"))
    return "\n".join(out)


# ---------------------------------------------------------------- settings

def load_settings(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_settings(path, s):
    # Escrita atomica no mesmo diretorio, mantendo 0600 (o arquivo tem chaves de API).
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings.droid-tier.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    # No Windows a troca falha se o Droid estiver lendo o arquivo naquele instante.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                os.remove(tmp)
                raise
            time.sleep(0.2 * (attempt + 1))


def get_role(s, role):
    section, mk, ek = ROLES[role]
    d = s.get(section, {}) if section else s
    return d.get(mk), d.get(ek)


def snapshot(s):
    return {role: get_role(s, role) for role in ROLES}


def matches(s, profile):
    return all(get_role(s, role)[0] == model and (effort is None or get_role(s, role)[1] == effort)
               for role, (model, effort) in profile.items())


def set_roles(s, profile, exact=False):
    """Grava os papeis. exact=True (restaurar padroes) tambem apaga o que nao existia."""
    for role, (model, effort) in profile.items():
        section, mk, ek = ROLES[role]
        d = s.setdefault(section, {}) if section else s
        for key, value in ((mk, model), (ek, effort)):
            if value is not None:
                d[key] = value
            elif exact:
                d.pop(key, None)
        if exact and section and not d:
            s.pop(section)  # secao criada por um fallback


# ---------------------------------------------------------------- estado

def read_pin():
    try:
        with open(PIN_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def read_home():
    """Padroes guardados ao sair deles; None quando o Droid esta nos padroes."""
    try:
        with open(HOME_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {role: tuple(v) for role, v in data["roles"].items()}
    except FileNotFoundError:
        return None


def save_home(roles):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HOME_FILE, "w", encoding="utf-8") as f:
        json.dump({"savedAt": dt.datetime.now().astimezone().isoformat(),
                   "roles": {r: list(v) for r, v in roles.items()}}, f, indent=2)


class Droid:
    """Settings do Droid + fallbacks resolvidos + padroes guardados."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.s = load_settings(cfg["settings"])
        self.fallbacks = resolve_all(cfg, self.s)
        self.saved_home = read_home()
        self.home = self.saved_home or snapshot(self.s)

    def current(self):
        if self.saved_home is None:
            name = self.on_fallback()
            return f"home (coincide com o fallback {name})" if name else HOME_TIER
        return self.on_fallback() or "misto"

    def on_fallback(self):
        for name, profile in self.fallbacks.items():
            if matches(self.s, profile):
                return name
        return None

    def go(self, tier):
        """Leva o settings ao degrau. Devolve True se escreveu."""
        if tier == HOME_TIER:
            if self.saved_home is None:
                return False
            set_roles(self.s, self.saved_home, exact=True)
            write_settings(self.cfg["settings"], self.s)
            os.remove(HOME_FILE)
            self.saved_home = None
            return True
        profile = self.fallbacks[tier]
        if self.saved_home is None:
            if self.on_fallback():
                # Sem copia dos padroes e o settings ja esta num fallback: guardar
                # agora gravaria o fallback como se fosse o padrao da pessoa.
                raise ConfigError("o settings ja esta num fallback e nao ha padroes guardados; "
                                  "configure seus padroes no Droid ou grave home.json")
            save_home(self.home)
            self.saved_home = self.home
        if matches(self.s, profile):
            return False
        set_roles(self.s, profile)
        write_settings(self.cfg["settings"], self.s)
        return True


# ---------------------------------------------------------------- acoes (CLI e TUI)

class LimitsError(Exception):
    """A API da Factory nao respondeu; nada deve ser alterado."""


def tier_names(cfg):
    return [HOME_TIER] + [fb["name"] for fb in cfg["fallbacks"]]


def write_pin(name):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PIN_FILE, "w", encoding="utf-8") as f:
        f.write(name + "\n")


def clear_pin():
    if os.path.exists(PIN_FILE):
        os.remove(PIN_FILE)


def pin(cfg, name):
    if name not in tier_names(cfg):
        raise ConfigError(f"degrau {name!r} nao existe; opcoes: {', '.join(tier_names(cfg))}")
    d = Droid(cfg)
    before = d.current()
    changed = d.go(name)
    write_pin(name)
    msg = f"pin {name} (antes: {before})" + ("" if changed else ", nada a mudar")
    log(msg)
    return msg


def unpin():
    clear_pin()
    msg = "unpin, o timer volta a decidir"
    log(msg)
    return msg


def restore(cfg):
    d = Droid(cfg)
    before = d.current()
    changed = d.go(HOME_TIER)
    clear_pin()
    msg = f"restore: {before} -> home" if changed else "restore: ja nos padroes"
    log(msg)
    return msg


def status(cfg):
    """Estado atual + decisao pelos limites. Levanta LimitsError sem rede."""
    d = Droid(cfg)
    try:
        limits = fetch_limits(cfg)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LimitsError(str(e)) from e
    hp = home_pool(cfg, d.home)
    tier, hits = pick_tier(cfg, limits, hp)
    return {"droid": d, "current": d.current(), "pin": read_pin(), "limits": limits,
            "home_pool": hp, "tier": tier, "hits": hits}


def run(cfg):
    """Aplica o degrau que os limites indicam. Devolve a mensagem do log, ou None se nada mudou."""
    d = Droid(cfg)
    before = d.current()
    from . import notify
    try:
        st = status(cfg)
    except LimitsError as e:
        # Sem resposta confiavel, nao mexe: melhor ficar no degrau atual do que chutar.
        log(f"falha ao consultar limites: {e}; mantendo {before}")
        if not os.path.exists(api_error_file()):  # avisa so quando comeca, nao a cada rodada
            os.makedirs(STATE_DIR, exist_ok=True)
            open(api_error_file(), "w").close()
            notify.send(cfg, "droid-tier: sem acesso aos limites",
                        f"A API da Factory falhou ({e}). O Droid fica em {before} até ela voltar.",
                        {"event": "api_error", "tier": before})
        raise
    if os.path.exists(api_error_file()):
        os.remove(api_error_file())
        notify.send(cfg, "droid-tier: limites de volta", "A API da Factory voltou a responder.",
                    {"event": "api_ok", "tier": before})
    if st["pin"]:
        return None
    tier, hits = st["tier"], st["hits"]
    if not d.go(tier):
        return None
    why = "; ".join(f"{p} {', '.join(h)}" for p, h in hits.items() if h)
    msg = f"{before} -> {tier} ({'limites liberados' if tier == HOME_TIER else why})"
    log(msg)
    if tier == HOME_TIER:
        text = "Limites liberados. O Droid voltou para os seus padrões."
    else:
        text = f"Limite da Factory: {why}. Sessões novas do Droid usam o fallback {tier}."
    notify.send(cfg, f"droid-tier: {before} → {tier}", text,
                {"event": "tier_changed", "from": before, "to": tier, "reason": why})
    return msg


def api_error_file():
    # Calculado na hora: STATE_DIR pode ser trocado (XDG, testes).
    return os.path.join(STATE_DIR, "api-error")
