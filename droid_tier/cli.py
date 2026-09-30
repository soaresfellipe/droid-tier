"""Linha de comando do droid-tier.

Uso:
  droid-tier setup         abre a interface: providers, modelos e fallbacks
  droid-tier init          cria o config.toml de exemplo
  droid-tier check         valida o config contra o settings.json do Droid, sem rede
  droid-tier status        mostra limites e degrau, nao altera nada
  droid-tier run           consulta limites e aplica o degrau (usado pelo timer)
  droid-tier pin <degrau>  aplica e fixa um degrau ("home" = seus padroes)
  droid-tier unpin         devolve o controle ao timer
  droid-tier restore       restaura seus padroes agora e solta o pin
  droid-tier schedule [install|uninstall|status]
                           roda o `run` a cada 5 min (systemd no Linux, Tarefa Agendada no Windows)
"""
import os
import sys

from . import catalog, core
from .core import CONFIG_FILE, EXAMPLE_CONFIG, HOME_FILE, ConfigError, Droid, describe, home_pool, load_config


def cmd_schedule(args):
    from . import schedule
    try:
        schedule.main(args)
    except schedule.ScheduleError as e:
        sys.exit(f"erro: {e}")


def cmd_setup(_):
    from .tui import run_tui  # Textual so carrega aqui; o timer fica leve
    run_tui()


def cmd_init(_):
    if os.path.exists(CONFIG_FILE):
        sys.exit(f"{CONFIG_FILE} ja existe; nao vou sobrescrever")
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(EXAMPLE_CONFIG)
    print(f"criado {CONFIG_FILE}; ajuste os fallbacks e rode `droid-tier check`")


def print_profile(profile, settings=None, natives=()):
    customs = {m.get("id"): m for m in (settings or {}).get("customModels") or []}
    for role, (model, effort) in profile.items():
        note = ""
        if effort and settings is not None:
            allowed = catalog.efforts_for(model, natives, customs.get(model)) if model else None
            if allowed and effort not in allowed:
                note = f"   <- o Droid nao aceita {effort} aqui (aceita: {', '.join(allowed)})"
        print(f"  {role:16} {model or '-'}" + (f" @{effort}" if effort else "") + note)


def cmd_check(_):
    cfg = load_config()
    d = Droid(cfg)
    natives = catalog.native_models()
    hp = home_pool(cfg, d.home)
    origin = "guardados em " + HOME_FILE if d.saved_home else "no settings.json"
    print(f"home: seus padroes, {origin} (pool {hp or 'nenhum'})")
    print_profile(d.home)
    for fb in cfg["fallbacks"]:
        origin = f"pool {fb['pool']}" if fb.get("pool") else f"provider {fb.get('provider') or '(nenhum)'}"
        print(f"{fb['name']} ({origin})")
        print_profile(d.fallbacks[fb["name"]], d.s, natives)
    if cfg["fallbacks"][-1].get("pool"):
        print(f"aviso: o ultimo fallback depende do pool {cfg['fallbacks'][-1]['pool']}; "
              "sem um fallback sem pool, ele fica mesmo estourado")
    print(f"degrau atual: {d.current()}")


def cmd_pin(args):
    cfg = load_config()
    if not args:
        sys.exit(f"uso: droid-tier pin {{{'|'.join(core.tier_names(cfg))}}}")
    core.pin(cfg, args[0])


def cmd_unpin(_):
    core.unpin()


def cmd_restore(_):
    core.restore(load_config())


def cmd_status(_):
    cfg = load_config()
    try:
        st = core.status(cfg)
    except core.LimitsError as e:
        sys.exit(f"falha ao consultar limites: {e}")
    print(f"degrau atual: {st['current']}" + (f" (fixado em {st['pin']})" if st["pin"] else ""))
    print(f"degrau pelos limites (limiar {cfg['threshold']:.0f}%, padroes no pool "
          f"{st['home_pool'] or 'nenhum'}): {st['tier']}")
    print(describe(st["limits"]))


def cmd_run(_):
    try:
        core.run(load_config())
    except core.LimitsError:
        sys.exit(1)


COMMANDS = {
    "setup": cmd_setup,
    "schedule": cmd_schedule,
    "init": cmd_init,
    "check": cmd_check,
    "status": cmd_status,
    "run": cmd_run,
    "pin": cmd_pin,
    "unpin": cmd_unpin,
    "restore": cmd_restore,
}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in COMMANDS:
        sys.exit(__doc__)
    try:
        COMMANDS[cmd](sys.argv[2:])
    except ConfigError as e:
        if cmd == "run":
            core.log(f"erro de configuracao: {e}; nada alterado")
        else:
            print(f"erro: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
