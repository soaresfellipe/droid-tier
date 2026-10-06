"""Editing config.toml while keeping comments (tomlkit)."""
import os
import tempfile

import tomlkit

from . import core

NEW_CONFIG = '''\
# droid-tier: fallbacks for when your Factory limits run out.
# Your Droid defaults don't go here; see `droid-tier check`.

# Switch when any window (5h, weekly, monthly) goes over this %.
threshold = 95
'''

PROVIDER_KEYS = ("name", "base_url", "kind", "prefix", "models")


def load_doc(path=None):
    path = path or core.CONFIG_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return tomlkit.parse(f.read())
    except FileNotFoundError:
        return tomlkit.parse(NEW_CONFIG)


def save_doc(doc, path=None):
    path = path or core.CONFIG_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config.droid-tier.")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(tomlkit.dumps(doc))
    os.replace(tmp, path)


def get_providers(doc):
    return {pid: dict(p) for pid, p in (doc.get("providers") or {}).items()}


def upsert_provider(doc, pid, **values):
    if "providers" not in doc:
        doc["providers"] = tomlkit.table(is_super_table=True)
    table = doc["providers"].get(pid)
    if table is None:
        table = tomlkit.table()
        doc["providers"][pid] = table
    for key in PROVIDER_KEYS:
        if key not in values:
            continue
        value = values[key]
        if value in (None, "", []) and key != "models":
            table.pop(key, None)
        elif key == "models":
            arr = tomlkit.array()
            arr.extend(sorted(value))
            if len(value) > 3:
                arr.multiline(True)
            table[key] = arr
        else:
            table[key] = value


def remove_provider(doc, pid):
    if "providers" in doc and pid in doc["providers"]:
        del doc["providers"][pid]


def get_fallbacks(doc):
    return [dict(fb) for fb in doc.get("fallback") or []]


def set_fallbacks(doc, fallbacks):
    aot = tomlkit.aot()
    for fb in fallbacks:
        t = tomlkit.table()
        t["name"] = fb["name"]
        if fb.get("pool"):
            t["pool"] = fb["pool"]
        if fb.get("provider"):
            t["provider"] = fb["provider"]
        for role in core.ROLES:
            if fb.get(role):
                t[role] = fb[role]
        aot.append(t)
    if "fallback" in doc:
        del doc["fallback"]
    if fallbacks:
        doc["fallback"] = aot


def get_home(doc):
    home = doc.get("home")
    return dict(home) if isinstance(home, dict) else None


def set_home(doc, home):
    """Write the optional [home] defaults; None removes the table."""
    if "home" in doc:
        del doc["home"]
    if not home:
        return
    t = tomlkit.table()
    if home.get("pool"):
        t["pool"] = home["pool"]
    for role in core.ROLES:
        if home.get(role):
            t[role] = home[role]
    doc["home"] = t


def fallback_users(doc, pid):
    return [fb["name"] for fb in get_fallbacks(doc) if fb.get("provider") == pid]
