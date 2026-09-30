"""Avisos de troca de degrau: notificacao do sistema, ntfy e webhook JSON.

So biblioteca padrao (roda no timer). Falha em aviso nunca interrompe a troca.
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
        raise core.ConfigError("[notify] deve ser uma tabela")
    unknown = set(section) - set(KEYS)
    if unknown:
        raise core.ConfigError(f"[notify]: campos desconhecidos {sorted(unknown)}")
    for key in ("ntfy", "webhook"):
        url = section.get(key)
        if url is not None and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            raise core.ConfigError(f"[notify] {key} deve ser uma URL http(s)")
    return section


def send(cfg, title, message, event=None):
    """Dispara todos os canais configurados. Devolve a lista de canais que falharam."""
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
        except Exception as e:  # aviso e acessorio; registra e segue
            failed.append(channel)
            core.log(f"aviso por {channel} falhou: {e}")
    return failed


def ntfy(url, title, message):
    # Cabecalho HTTP nao leva acento; o ntfy aceita RFC 2047 (=?UTF-8?B?...?=).
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
            raise RuntimeError("notify-send nao encontrado")
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("DBUS_SESSION_BUS_ADDRESS")):
            raise RuntimeError("sem sessao grafica para notify-send")
        subprocess.run(["notify-send", "-a", "droid-tier", title, message], check=True, timeout=10)
    else:
        raise RuntimeError(f"notificacao do sistema nao suportada em {sys.platform}")


# AppID do PowerShell: o Windows so mostra toast de um app registrado.
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
    # CREATE_NO_WINDOW: sem isso o PowerShell pisca um console quando chamado pelo pythonw da tarefa.
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, timeout=20, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
