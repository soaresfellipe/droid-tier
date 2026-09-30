"""Tier change notifications: system notification, ntfy and JSON webhook.

Standard library only (runs in the schedule). A failed notification never stops the switch.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from . import core

KEYS = ("desktop", "ntfy", "webhook")


def validate(section):
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise core.ConfigError("[notify] must be a table")
    unknown = set(section) - set(KEYS)
    if unknown:
        raise core.ConfigError(f"[notify]: unknown fields {sorted(unknown)}")
    for key in ("ntfy", "webhook"):
        url = section.get(key)
        if url is None:
            continue
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            raise core.ConfigError(f"[notify] {key} must be an http(s) URL")
        core.check_url(url, f"[notify] {key}")  # messages may leak tier names; no plain http
    return section


def send(cfg, title, message, event=None):
    """Send through every configured channel. Returns the channels that failed."""
    section = cfg.get("notify") or {}
    failed = []
    for channel, enabled, fn in (
        ("desktop", section.get("desktop"), lambda: desktop(title, message)),
        ("ntfy", section.get("ntfy"), lambda: ntfy(section["ntfy"], title, message)),
        ("webhook", section.get("webhook"), lambda: webhook(section["webhook"], title, message, event)),
    ):
        if not enabled:
            continue
        try:
            fn()
        except Exception as e:  # notifications are secondary; log and move on
            failed.append(channel)
            core.log(f"{channel} notification failed: {e}")
    return failed


def ntfy(url, title, message):
    # HTTP headers are ASCII; ntfy accepts RFC 2047 (=?UTF-8?B?...?=) for anything else.
    encoded_title = "=?UTF-8?B?" + base64.b64encode(title.encode("utf-8")).decode("ascii") + "?="
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST",
                                 headers={"Title": encoded_title, "Tags": "robot", "User-Agent": "droid-tier"})
    urllib.request.urlopen(req, timeout=10).close()


def webhook(url, title, message, event=None):
    body = json.dumps({"source": "droid-tier", "title": title, "message": message, **(event or {})}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "droid-tier"})
    urllib.request.urlopen(req, timeout=10).close()


def desktop(title, message):
    if sys.platform == "win32":
        _windows_toast(title, message)
    elif sys.platform.startswith("linux"):
        if not shutil.which("notify-send"):
            raise RuntimeError("notify-send not found")
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
            raise RuntimeError("no graphical session for notify-send")
        subprocess.run(["notify-send", "-a", "droid-tier", title, message], check=True, timeout=10)
    else:
        raise RuntimeError(f"system notifications are not supported on {sys.platform}")


# PowerShell's AppID: Windows only shows toasts from a registered app.
_PS_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def _windows_toast(title, message):
    def ps_str(s):
        return "'" + s.replace("'", "''") + "'"

    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode({ps_str(title)})) > $null
$x.Item(1).AppendChild($t.CreateTextNode({ps_str(message)})) > $null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({ps_str(_PS_APP_ID)}).Show(
    [Windows.UI.Notifications.ToastNotification]::new($t))
"""
    # CREATE_NO_WINDOW: otherwise PowerShell flashes a console when called from the task's pythonw.
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, timeout=20, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
