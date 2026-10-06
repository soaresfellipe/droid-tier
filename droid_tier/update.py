"""droid-tier self-update.

Compares the installed version with pyproject.toml on the main branch and
reinstalls with the tool that installed droid-tier (uv tool, pipx or plain
pip). Standard library only; the scheduled run never imports this module.
"""
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.request

from . import __version__

REPO_URL = "https://github.com/soaresfellipe/droid-tier"
PYPROJECT_URL = "https://raw.githubusercontent.com/soaresfellipe/droid-tier/main/pyproject.toml"


def remote_version(timeout=20):
    with urllib.request.urlopen(PYPROJECT_URL, timeout=timeout) as r:
        data = tomllib.load(r)
    v = (data.get("project") or {}).get("version")
    if not v:
        raise RuntimeError(f"{PYPROJECT_URL} has no project.version")
    return v


def version_tuple(v):
    """'0.6.0' -> (0, 6, 0); a pre/post suffix (0.7.0rc1) is ignored."""
    import re
    return tuple(int(m.group()) if (m := re.match(r"\d+", p)) else 0 for p in v.split("."))


def in_dev_checkout():
    """True when running from a source clone (pyproject.toml next to the package)."""
    import droid_tier
    root = os.path.dirname(os.path.dirname(os.path.abspath(droid_tier.__file__)))
    return os.path.exists(os.path.join(root, "pyproject.toml"))


def in_uv_tools():
    if not shutil.which("uv"):
        return False
    out = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True).stdout
    return any(line.strip().startswith("droid-tier") for line in out.splitlines())


def in_pipx():
    if not shutil.which("pipx"):
        return False
    out = subprocess.run(["pipx", "list"], capture_output=True, text=True).stdout
    return "droid-tier" in out


def install_cmd():
    """The command that reinstalls droid-tier from the git repo, or None on a dev checkout."""
    if in_dev_checkout():
        return None
    if in_uv_tools():
        return ["uv", "tool", "install", "--force", f"git+{REPO_URL}"]
    if in_pipx():
        return ["pipx", "upgrade", "droid-tier"]
    return [sys.executable, "-m", "pip", "install", "--upgrade", f"git+{REPO_URL}"]


def run(argv=None):
    """Handle `droid-tier update [args]`; returns the process exit code."""
    argv = list(sys.argv[2:] if argv is None else argv)
    check = "--check" in argv
    force = "--force" in argv
    try:
        remote = remote_version()
    except (OSError, RuntimeError, ValueError) as e:
        print(f"error: couldn't fetch the latest version: {e}", file=sys.stderr)
        return 1
    newer = version_tuple(remote) > version_tuple(__version__)
    if not (newer or force):
        print(f"installed: {__version__}")
        print(f"latest on main: {remote}")
        print("up to date")
        return 0
    if check:
        print(f"installed: {__version__}")
        print(f"latest on main: {remote}")
        print("update available; run `droid-tier update`")
        return 1
    cmd = install_cmd()
    if cmd is None:
        print("droid-tier runs from a source checkout; update it with git pull "
              "(and pip install -e . if needed)", file=sys.stderr)
        return 1
    print(f"updating {__version__} -> {remote}: {' '.join(cmd)}")
    if os.name == "nt":
        # The droid-tier.exe shim (uv tool / pipx) is locked while this process
        # runs from it; detach the installer so it starts after we exit.
        subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS, close_fds=True)
        print("the installer is running in the background; check `droid-tier --version` in a moment")
    else:
        subprocess.run(cmd, check=True)
        print(f"updated to {remote}; check `droid-tier --version` to confirm")
    return 0
