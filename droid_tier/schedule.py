"""Agenda `droid-tier run` a cada N minutos: systemd --user no Linux, Tarefa Agendada no Windows."""
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TASK_NAME = "droid-tier"
UNIT = "droid-tier"
INTERVAL_MIN = 5


class ScheduleError(Exception):
    pass


def python_for_task():
    """Interpretador do pacote instalado. No Windows, pythonw.exe: sem console, sem janela piscando."""
    exe = sys.executable
    if os.name == "nt":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
    return exe


def _run(cmd, check=True):
    # schtasks escreve na codificacao OEM do console (cp850 no Windows em portugues).
    encoding = "oem" if os.name == "nt" else "utf-8"
    r = subprocess.run(cmd, capture_output=True, text=True, encoding=encoding, errors="replace")
    if check and r.returncode != 0:
        raise ScheduleError(f"{' '.join(cmd)}: {(r.stderr or r.stdout).strip()}")
    return r


# ---------------------------------------------------------------- Windows

def current_user():
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or ""
    return f"{domain}\\{user}" if domain else user


def task_xml(python, user, interval=INTERVAL_MIN):
    # XML em vez de flags do schtasks: e o unico jeito de liberar a execucao
    # na bateria e limitar o tempo de execucao.
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Troca os modelos do Droid conforme os limites da Factory (droid-tier)</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Repetition>
        <Interval>PT{interval}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>-m droid_tier run</Arguments>
    </Exec>
  </Actions>
</Task>
'''


def install_windows():
    python = python_for_task()
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(task_xml(python, current_user()))
        _run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"])
    finally:
        os.remove(path)
    _run(["schtasks", "/Run", "/TN", TASK_NAME])
    return f"Tarefa Agendada {TASK_NAME!r} criada: a cada {INTERVAL_MIN} min e no logon, com {python}"


def uninstall_windows():
    r = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
    return "Tarefa Agendada removida" if r.returncode == 0 else "Nao havia Tarefa Agendada"


def status_windows():
    r = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"], check=False)
    if r.returncode != 0:
        return "Tarefa Agendada nao instalada"
    return r.stdout.strip()


# ---------------------------------------------------------------- Linux

def unit_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "systemd", "user")


def service_unit(python):
    return f"""[Unit]
Description=Troca os modelos do Droid conforme os limites da Factory
After=network-online.target

[Service]
Type=oneshot
ExecStart={python} -m droid_tier run
"""


def timer_unit(interval=INTERVAL_MIN):
    return f"""[Unit]
Description=Consulta os limites da Factory a cada {interval} minutos

[Timer]
OnBootSec=1min
OnUnitActiveSec={interval}min
Persistent=true

[Install]
WantedBy=timers.target
"""


def install_linux():
    d = unit_dir()
    os.makedirs(d, exist_ok=True)
    python = python_for_task()
    with open(os.path.join(d, f"{UNIT}.service"), "w", encoding="utf-8") as f:
        f.write(service_unit(python))
    with open(os.path.join(d, f"{UNIT}.timer"), "w", encoding="utf-8") as f:
        f.write(timer_unit())
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
    return (f"Timer {UNIT}.timer ativo: a cada {INTERVAL_MIN} min, com {python}.\n"
            "Para rodar sem sessao aberta: loginctl enable-linger")


def uninstall_linux():
    _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], check=False)
    removed = False
    for ext in ("service", "timer"):
        path = os.path.join(unit_dir(), f"{UNIT}.{ext}")
        if os.path.exists(path):
            os.remove(path)
            removed = True
    _run(["systemctl", "--user", "daemon-reload"], check=False)
    return "Timer removido" if removed else "Nao havia timer"


def status_linux():
    r = _run(["systemctl", "--user", "list-timers", f"{UNIT}.timer", "--no-pager"], check=False)
    return r.stdout.strip() or "Timer nao instalado"


# ---------------------------------------------------------------- comando

def main(args):
    action = args[0] if args else "status"
    if sys.platform == "win32":
        impl = {"install": install_windows, "uninstall": uninstall_windows, "status": status_windows}
    elif sys.platform.startswith("linux"):
        impl = {"install": install_linux, "uninstall": uninstall_linux, "status": status_linux}
    else:
        raise ScheduleError(f"agendamento automatico ainda nao suportado em {sys.platform}")
    if action not in impl:
        raise ScheduleError("uso: droid-tier schedule {install|uninstall|status}")
    print(impl[action]())
