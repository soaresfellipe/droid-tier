"""Quota of external fallback providers.

Each adapter returns windows in the same shape as Factory's limits,
{name: {"usedPercent": float, "windowEnd": iso or None, "status": str}},
so the same threshold logic applies. Standard library only (runs in the schedule).

Providers without an adapter are treated as always available.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from . import core


class QuotaError(Exception):
    """The provider's quota couldn't be read."""


def _get(url, api_key, timeout=10):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "droid-tier",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise QuotaError(str(e)) from e


def opencode_go(base_url, api_key):
    # Undocumented, the same data the OpenCode console shows.
    data = _get(base_url.rstrip("/") + "/usage", api_key).get("usage")
    if not isinstance(data, dict) or not data:
        raise QuotaError("response has no `usage`; did the OpenCode Go API change?")
    windows = {}
    for name, w in data.items():
        if not isinstance(w, dict) or not isinstance(w.get("percent"), (int, float)):
            continue
        windows[name] = {"usedPercent": float(w["percent"]), "windowEnd": w.get("resetsAt"),
                         "status": w.get("status", "ok")}
    if not windows:
        raise QuotaError("`usage` has no window with a percent; did the OpenCode Go API change?")
    return windows


def openrouter(base_url, api_key):
    data = _get("https://openrouter.ai/api/v1/key", api_key).get("data") or {}
    limit, remaining = data.get("limit"), data.get("limit_remaining")
    if not limit or remaining is None:
        return {}  # key without a credit limit: nothing to watch
    used = max(0.0, min(100.0, (limit - remaining) / limit * 100))
    return {"key limit": {"usedPercent": used, "windowEnd": None, "status": "ok"}}


def deepseek(base_url, api_key):
    data = _get("https://api.deepseek.com/user/balance", api_key)
    if "is_available" not in data:
        raise QuotaError("response has no `is_available`; did the DeepSeek API change?")
    return {"balance": {"usedPercent": 0.0 if data["is_available"] else 100.0, "windowEnd": None,
                        "status": "ok" if data["is_available"] else "exhausted"}}


ADAPTERS = {
    # host (+ path prefix) -> adapter
    ("opencode.ai", "/zen/go"): opencode_go,
    ("openrouter.ai", ""): openrouter,
    ("api.deepseek.com", ""): deepseek,
}


def adapter_for(base_url):
    u = urllib.parse.urlparse(base_url)
    for (host, prefix), fn in ADAPTERS.items():
        if u.hostname == host and u.path.startswith(prefix):
            return fn
    return None


def provider_key(settings, base_url):
    for m in settings.get("customModels") or []:
        if core.norm_url(m.get("baseUrl")) == core.norm_url(base_url) and m.get("apiKey"):
            return m["apiKey"]
    return None


def check(cfg, settings):
    """Quota of each provider used by a fallback.

    Returns {provider_id: windows | None}; None means there's no adapter.
    Raises nothing: a provider whose quota can't be read gets
    {"error": message} and counts as available."""
    out = {}
    used = {fb["provider"] for fb in cfg["fallbacks"] if fb.get("provider")}
    for pid in sorted(used):
        base_url = cfg["providers"][pid]["base_url"]
        fn = adapter_for(base_url)
        key = provider_key(settings, base_url)
        if fn is None or key is None:
            out[pid] = None
            continue
        try:
            out[pid] = fn(base_url, key)
        except QuotaError as e:
            out[pid] = {"error": str(e)}
    return out


def hits(windows, threshold, now=None):
    """Like core.pool_hits, also counting any window whose status isn't ok."""
    if not windows or "error" in windows:
        return []
    found = core.pool_hits(windows, threshold, now, names=list(windows))
    for name, w in windows.items():
        if w.get("status", "ok") != "ok" and not any(h.startswith(name + " ") for h in found):
            found.append(f"{name} {w['status']}")
    return found
