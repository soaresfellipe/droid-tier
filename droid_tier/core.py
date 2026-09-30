"""droid-tier core: config, Factory limits and writes to Droid's settings.

Standard library only, so the scheduled run stays light.
"""
import datetime as dt
import json
import os
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")


def app_dir(xdg_var, unix_default):
    """droid-tier folder: XDG when set, otherwise inside the user profile.

    On Windows it also stays out of AppData on purpose: the Microsoft Store Python
    redirects writes under AppData to a private package folder, so the config
    would be invisible to Explorer and to other programs."""
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
# Prefixes of the native models Factory bills to the Droid Core pool.
CORE_PREFIXES = ("glm-", "deepseek-", "kimi-", "minimax-", "qwen", "nemotron-")

# role in config.toml -> (settings.json section, model field, effort field)
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
# droid-tier: fallbacks for when your Factory limits run out.
#
# Your Droid defaults (whatever is in settings.json) don't go here.
# When leaving them, droid-tier saves a copy and restores it once the limit frees up.

# Switch when any window (5h, weekly, monthly) goes over this %.
threshold = 95

# Pool your defaults use: "standard", "core" or "none".
# Without this line it's inferred from the models (Claude/GPT/Gemini = standard,
# GLM/DeepSeek/Kimi/MiniMax/Qwen/Nemotron = core, custom: = none).
# home_pool = "standard"

# Fallback providers. Every model used with `provider = "..."` must exist in
# customModels in ~/.factory/settings.json with this baseUrl.
[providers.opencode-go]
base_url = "https://opencode.ai/zen/go/v1"

# Fallbacks in order. The first one with room wins. A fallback with `provider`
# is skipped when that provider's quota is exhausted (OpenCode Go, OpenRouter
# and DeepSeek are tracked; other providers count as always available).
# Roles: session, spec, subagent_light, subagent_medium, subagent_heavy,
# orchestrator, worker, validator. Roles left out are not changed.
# Format: "model" or "model@effort".

[[fallback]]
name = "droid"
pool = "core"       # Factory's open-weight models (Droid Core)
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
provider = "opencode-go"   # the same models, through customModels
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"
subagent_light = "glm-5.3-flash@low"
subagent_medium = "glm-5.3-flash@high"
subagent_heavy = "glm-5.3-flash@max"
orchestrator = "glm-5.3@low"
worker = "glm-5.3-flash@high"
validator = "deepseek-v4.1-flash@high"

# Notifications when the tier changes or Factory's API stops responding.
# [notify]
# desktop = true                          # toast on Windows, notify-send on Linux
# ntfy = "https://ntfy.sh/your-topic"     # phone notification through the ntfy app.
#                                         # Anyone who knows the topic can read it: make it long and random.
# webhook = "https://example/webhook"     # JSON POST (n8n, Slack through a proxy...)
#                                         # URLs must be https, except localhost (local n8n).
'''


class ConfigError(Exception):
    pass


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def check_url(url, what):
    """Reject plain http for anything that carries API keys or messages.

    https is required; http is only accepted for loopback (local n8n, Ollama,
    test servers), where the traffic never leaves the machine."""
    u = urllib.parse.urlparse(url or "")
    if u.scheme == "https" or (u.scheme == "http" and (u.hostname or "").lower() in LOOPBACK_HOSTS):
        return url
    raise ConfigError(f"{what} must be an https:// URL (plain http only for localhost), got {url!r}")


ECHO = True  # the TUI turns this off: printing in the middle of Textual garbles the screen


def ensure_state_dir():
    """The state folder holds the log and the saved defaults; keep it private.

    mode=0700 on creation, plus a chmod that also tightens folders that already
    existed with a looser umask (a no-op on Windows, where ACLs rule)."""
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(STATE_DIR, 0o700)
    except OSError:
        pass


def log(msg):
    line = f"{dt.datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} {msg}"
    if ECHO:
        print(line)
    ensure_state_dir()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- config

def load_config(path=None):
    path = path or CONFIG_FILE
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} does not exist; run `droid-tier init`")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")

    fallbacks = cfg.get("fallback") or []
    if not fallbacks:
        raise ConfigError("no [[fallback]] defined")
    providers = cfg.get("providers") or {}
    home_pool = cfg.get("home_pool")
    if home_pool not in (None, "none", *POOLS):
        raise ConfigError("home_pool must be standard, core or none")
    seen = {HOME_TIER}
    for i, t in enumerate(fallbacks, 1):
        name = t.get("name")
        if not name:
            raise ConfigError(f"[[fallback]] #{i} has no name")
        if name in seen:
            raise ConfigError(f"fallback {name!r} is duplicated or reserved")
        seen.add(name)
        if t.get("pool") not in (None, *POOLS):
            raise ConfigError(f"fallback {name!r}: pool must be {' or '.join(POOLS)}")
        if t.get("pool") and t.get("provider"):
            raise ConfigError(f"fallback {name!r}: use pool (Factory) or provider, not both")
        if t.get("provider") and t["provider"] not in providers:
            raise ConfigError(f"fallback {name!r}: provider {t['provider']!r} is not in [providers]")
        unknown = set(t) - {"name", "pool", "provider", *ROLES}
        if unknown:
            raise ConfigError(f"fallback {name!r}: unknown fields {sorted(unknown)}")
        if not set(t) & set(ROLES):
            raise ConfigError(f"fallback {name!r} defines no role")
    for pname, p in providers.items():
        if not p.get("base_url"):
            raise ConfigError(f"provider {pname!r} has no base_url")
        check_url(p["base_url"], f"provider {pname!r} base_url")  # the API key goes there
    return {
        "threshold": float(cfg.get("threshold", 95)),
        "settings": os.path.expanduser(cfg.get("settings", DEFAULT_SETTINGS)),
        "api": check_url(cfg.get("factory_api", DEFAULT_API), "factory_api").rstrip("/"),
        "home_pool": home_pool,
        "providers": providers,
        "fallbacks": fallbacks,
        "notify": _validate_notify(cfg.get("notify")),
    }


def _validate_notify(section):
    from . import notify  # notify imports core
    return notify.validate(section)


def norm_url(u):
    return (u or "").rstrip("/").lower()


def resolve_fallback(fb, cfg, settings):
    """Config fallback -> {role: (Droid model id, effort or None)}."""
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
        raise ConfigError(f"fallback {name!r}: no customModel with model={model!r} "
                          f"and baseUrl={base_url!r} in Droid's settings.json")
    return found[0]["id"]


def resolve_all(cfg, settings):
    return {fb["name"]: resolve_fallback(fb, cfg, settings) for fb in cfg["fallbacks"]}


def infer_pool(roles):
    """Pool a set of roles uses; None if it only uses customModels."""
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


# ---------------------------------------------------------------- limits

def api_key():
    key = os.environ.get("FACTORY_API_KEY")
    if key:
        return key
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("FACTORY_API_KEY="):
                    _warn_key_file_perms()
                    return line.split("=", 1)[1].strip().strip("'\"")
    except FileNotFoundError:
        pass
    raise ConfigError(f"no FACTORY_API_KEY (neither in the environment nor in {KEY_FILE})")


def _warn_key_file_perms():
    """The file holds the Factory key: readable by group/others is worth a log line.

    Logged once per state via _flag, so the 5-min schedule doesn't spam it."""
    if os.name != "posix":
        return
    try:
        import stat
        loose = bool(stat.S_IMODE(os.stat(KEY_FILE).st_mode) & 0o077)
    except OSError:
        return
    if _flag("keyfile-perms", loose):
        log(f"warning: {KEY_FILE} is readable by group/others; run: chmod 600 {KEY_FILE}")


def fetch_limits(cfg):
    # Undocumented endpoint, the same one Droid's CLI calls.
    req = urllib.request.Request(cfg["api"] + "/api/billing/limits", headers={
        "Authorization": f"Bearer {api_key()}",
        "Accept": "application/json",
        "User-Agent": "droid-tier",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return validate_limits(json.load(r))


def validate_limits(data):
    """Reject responses that aren't in the expected shape.

    Otherwise a change in the (undocumented) endpoint would read as "no limit
    exceeded" and droid-tier would restore defaults that are out of quota. Droid
    itself treats a missing limits.standard as a failure."""
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(limits, dict):
        raise ValueError("response has no `limits`; did Factory's API change?")
    for pool in POOLS:
        p = limits.get(pool)
        if p is None and pool == "core":
            continue  # account without Droid Core
        if not isinstance(p, dict) or not any(
                isinstance((p.get(w) or {}).get("usedPercent"), (int, float)) for w in WINDOWS):
            raise ValueError(f"response has no `limits.{pool}` with usedPercent; did Factory's API change?")
    return limits


def pool_hits(pool, threshold, now=None, names=WINDOWS):
    """Active windows of the pool that went over the threshold, like 'weekly 97%'."""
    if not pool:
        return []
    now = now or dt.datetime.now(dt.timezone.utc)
    hits = []
    for name in names:
        b = pool.get(name) or {}
        pct = b.get("usedPercent")
        if pct is None or pct < threshold:
            continue
        end = b.get("windowEnd")
        if end and dt.datetime.fromisoformat(end.replace("Z", "+00:00")) <= now:
            continue  # the window already reset; the number is stale
        hits.append(f"{name} {pct:.0f}%")
    return hits


def pick_tier(cfg, limits, hpool, now=None, provider_hits=None):
    """'home' if the defaults' pool has room; otherwise the first fallback with
    room, or the last one if all are exhausted. Returns (name, reasons).

    provider_hits: {provider_id: [reasons]} for external providers whose quota
    is known; a provider missing from it counts as available."""
    hits = {p: pool_hits(limits.get(p), cfg["threshold"], now) for p in POOLS}
    provider_hits = provider_hits or {}
    if not hpool or not hits[hpool]:
        return HOME_TIER, hits
    for fb in cfg["fallbacks"]:
        if fb.get("pool"):
            if not hits[fb["pool"]]:
                return fb["name"], hits
        elif fb.get("provider") and provider_hits.get(fb["provider"]):
            hits[fb["provider"]] = provider_hits[fb["provider"]]
        else:
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
                end = f" (resets {b['windowEnd']})" if b.get("windowEnd") else ""
                parts.append(f"{name} {b['usedPercent']:.0f}%{end}")
        out.append(f"  {pool}: " + (", ".join(parts) if parts else "no data"))
    return "\n".join(out)


# ---------------------------------------------------------------- settings

def load_settings(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_settings(path, s):
    # Atomic write in the same folder, keeping 0600 (the file holds API keys).
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings.droid-tier.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, 0o600)
    # On Windows the replace fails if Droid happens to be reading the file.
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
    """Write the roles. exact=True (restoring defaults) also removes what didn't exist."""
    for role, (model, effort) in profile.items():
        section, mk, ek = ROLES[role]
        d = s.setdefault(section, {}) if section else s
        for key, value in ((mk, model), (ek, effort)):
            if value is not None:
                d[key] = value
            elif exact:
                d.pop(key, None)
        if exact and section and not d:
            s.pop(section)  # section created by a fallback


# ---------------------------------------------------------------- state

def read_pin():
    try:
        with open(PIN_FILE, encoding="utf-8") as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def read_home():
    """Defaults saved when leaving them; None while Droid is on its defaults."""
    try:
        with open(HOME_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {role: tuple(v) for role, v in data["roles"].items()}
    except FileNotFoundError:
        return None


def save_home(roles):
    ensure_state_dir()
    with open(HOME_FILE, "w", encoding="utf-8") as f:
        json.dump({"savedAt": dt.datetime.now().astimezone().isoformat(),
                   "roles": {r: list(v) for r, v in roles.items()}}, f, indent=2)


class Droid:
    """Droid settings + resolved fallbacks + saved defaults."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.s = load_settings(cfg["settings"])
        self.fallbacks = resolve_all(cfg, self.s)
        self.saved_home = read_home()
        self.home = self.saved_home or snapshot(self.s)

    def current(self):
        if self.saved_home is None:
            name = self.on_fallback()
            return f"home (matches fallback {name})" if name else HOME_TIER
        return self.on_fallback() or "mixed"

    def on_fallback(self):
        for name, profile in self.fallbacks.items():
            if matches(self.s, profile):
                return name
        return None

    def go(self, tier):
        """Bring the settings to the tier. Returns True if it wrote."""
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
                # No saved defaults and the settings are already on a fallback:
                # saving now would record the fallback as the user's defaults.
                raise ConfigError("the settings are already on a fallback and no defaults are saved; "
                                  "set your defaults in Droid or write home.json")
            save_home(self.home)
            self.saved_home = self.home
        if matches(self.s, profile):
            return False
        set_roles(self.s, profile)
        write_settings(self.cfg["settings"], self.s)
        return True


# ---------------------------------------------------------------- actions (CLI and TUI)

class LimitsError(Exception):
    """Factory's API didn't respond; nothing should change."""


def tier_names(cfg):
    return [HOME_TIER] + [fb["name"] for fb in cfg["fallbacks"]]


def write_pin(name):
    ensure_state_dir()
    with open(PIN_FILE, "w", encoding="utf-8") as f:
        f.write(name + "\n")


def clear_pin():
    if os.path.exists(PIN_FILE):
        os.remove(PIN_FILE)


def pin(cfg, name):
    if name not in tier_names(cfg):
        raise ConfigError(f"tier {name!r} does not exist; options: {', '.join(tier_names(cfg))}")
    d = Droid(cfg)
    before = d.current()
    changed = d.go(name)
    write_pin(name)
    msg = f"pin {name} (was: {before})" + ("" if changed else ", nothing to change")
    log(msg)
    return msg


def unpin():
    clear_pin()
    msg = "unpin, the schedule decides again"
    log(msg)
    return msg


def restore(cfg):
    d = Droid(cfg)
    before = d.current()
    changed = d.go(HOME_TIER)
    clear_pin()
    msg = f"restore: {before} -> home" if changed else "restore: already on defaults"
    log(msg)
    return msg


def status(cfg):
    """Current state + decision from the limits. Raises LimitsError without network."""
    from . import quotas
    d = Droid(cfg)
    try:
        limits = fetch_limits(cfg)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LimitsError(str(e)) from e
    hp = home_pool(cfg, d.home)
    provider_quotas = quotas.check(cfg, d.s)
    provider_hits = {pid: quotas.hits(w, cfg["threshold"]) for pid, w in provider_quotas.items()}
    tier, hits = pick_tier(cfg, limits, hp, provider_hits=provider_hits)
    return {"droid": d, "current": d.current(), "pin": read_pin(), "limits": limits,
            "home_pool": hp, "tier": tier, "hits": hits, "quotas": provider_quotas}


def run(cfg):
    """Apply the tier the limits call for. Returns the log message, or None if nothing changed."""
    d = Droid(cfg)
    before = d.current()
    from . import notify
    try:
        st = status(cfg)
    except LimitsError as e:
        # No reliable answer, so don't touch anything: better to stay put than to guess.
        log(f"failed to fetch limits: {e}; staying on {before}")
        if not os.path.exists(api_error_file()):  # notify when it starts, not every run
            ensure_state_dir()
            open(api_error_file(), "w").close()
            notify.send(cfg, "droid-tier: can't read limits",
                        f"Factory's API failed ({e}). Droid stays on {before} until it's back.",
                        {"event": "api_error", "tier": before})
        raise
    if os.path.exists(api_error_file()):
        os.remove(api_error_file())
        notify.send(cfg, "droid-tier: limits are back", "Factory's API is responding again.",
                    {"event": "api_ok", "tier": before})
    for pid, windows in (st["quotas"] or {}).items():
        failed = bool(windows and "error" in windows)
        if _flag(f"quota-error-{pid}", failed) and failed:
            log(f"couldn't read the {pid} quota ({windows['error']}); treating it as available")
    if st["pin"]:
        return None
    tier, hits = st["tier"], st["hits"]
    if not d.go(tier):
        return None
    why = "; ".join(f"{p} {', '.join(h)}" for p, h in hits.items() if h)
    msg = f"{before} -> {tier} ({'limits freed up' if tier == HOME_TIER else why})"
    log(msg)
    if tier == HOME_TIER:
        text = "Limits freed up. Droid is back on your defaults."
    else:
        text = f"Factory limit: {why}. New Droid sessions use the {tier} fallback."
    notify.send(cfg, f"droid-tier: {before} → {tier}", text,
                {"event": "tier_changed", "from": before, "to": tier, "reason": why})
    return msg


def api_error_file():
    # Computed on use: STATE_DIR can change (XDG, tests).
    return os.path.join(STATE_DIR, "api-error")


def _flag(name, on):
    """Keep a marker file in sync with `on`. Returns True when it changed."""
    path = os.path.join(STATE_DIR, name)
    if on == os.path.exists(path):
        return False
    if on:
        ensure_state_dir()
        open(path, "w").close()
    else:
        os.remove(path)
    return True


def describe_quotas(provider_quotas):
    out = []
    for pid, windows in (provider_quotas or {}).items():
        if windows is None:
            out.append(f"  {pid}: quota not tracked (no adapter), treated as available")
        elif "error" in windows:
            out.append(f"  {pid}: couldn't read the quota ({windows['error']}), treated as available")
        elif not windows:
            out.append(f"  {pid}: no limit on this key")
        else:
            parts = []
            for name, w in windows.items():
                end = f" (resets {w['windowEnd']})" if w.get("windowEnd") else ""
                parts.append(f"{name} {w['usedPercent']:.0f}%{end}")
            out.append(f"  {pid}: " + ", ".join(parts))
    return "\n".join(out)
