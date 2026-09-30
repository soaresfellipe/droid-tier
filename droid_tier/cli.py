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
"""
import os
import sys
import urllib.error

from .core import (CONFIG_FILE, EXAMPLE_CONFIG, HOME_FILE, HOME_TIER, PIN_FILE, STATE_DIR,
                   ConfigError, Droid, describe, fetch_limits, home_pool, load_config, log,
                   pick_tier, read_pin)


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


def print_profile(profile):
    for role, (model, effort) in profile.items():
        print(f"  {role:16} {model or '-'}" + (f" @{effort}" if effort else ""))


def cmd_check(_):
    cfg = load_config()
    d = Droid(cfg)
    hp = home_pool(cfg, d.home)
    origin = "guardados em " + HOME_FILE if d.saved_home else "no settings.json"
    print(f"home: seus padroes, {origin} (pool {hp or 'nenhum'})")
    print_profile(d.home)
    for fb in cfg["fallbacks"]:
        origin = f"pool {fb['pool']}" if fb.get("pool") else f"provider {fb.get('provider') or '(nenhum)'}"
        print(f"{fb['name']} ({origin})")
        print_profile(d.fallbacks[fb["name"]])
    if cfg["fallbacks"][-1].get("pool"):
        print(f"aviso: o ultimo fallback depende do pool {cfg['fallbacks'][-1]['pool']}; "
              "sem um fallback sem pool, ele fica mesmo estourado")
    print(f"degrau atual: {d.current()}")


def cmd_pin(args):
    cfg = load_config()
    names = [HOME_TIER] + [fb["name"] for fb in cfg["fallbacks"]]
    if not args or args[0] not in names:
        sys.exit(f"uso: droid-tier pin {{{'|'.join(names)}}}")
    d = Droid(cfg)
    before = d.current()
    changed = d.go(args[0])
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PIN_FILE, "w", encoding="utf-8") as f:
        f.write(args[0] + "\n")
    log(f"pin {args[0]} (antes: {before})" + ("" if changed else ", nada a mudar"))


def cmd_unpin(_):
    if os.path.exists(PIN_FILE):
        os.remove(PIN_FILE)
    log("unpin, o timer volta a decidir")


def cmd_restore(_):
    d = Droid(load_config())
    before = d.current()
    changed = d.go(HOME_TIER)
    if os.path.exists(PIN_FILE):
        os.remove(PIN_FILE)
    log(f"restore: {before} -> home" if changed else "restore: ja nos padroes")


def cmd_status_or_run(run):
    cfg = load_config()
    d = Droid(cfg)
    before = d.current()
    pin = read_pin()
    try:
        limits = fetch_limits(cfg)
    except (urllib.error.URLError, OSError, ValueError) as e:
        # Sem resposta confiavel, nao mexe: melhor ficar no degrau atual do que chutar.
        log(f"falha ao consultar limites: {e}; mantendo {before}")
        sys.exit(1)

    hp = home_pool(cfg, d.home)
    tier, hits = pick_tier(cfg, limits, hp)
    if not run:
        print(f"degrau atual: {before}" + (f" (fixado em {pin})" if pin else ""))
        print(f"degrau pelos limites (limiar {cfg['threshold']:.0f}%, padroes no pool {hp or 'nenhum'}): {tier}")
        print(describe(limits))
        return
    if pin:
        return
    if d.go(tier):
        why = "; ".join(f"{p} {', '.join(h)}" for p, h in hits.items() if h)
        log(f"{before} -> {tier} ({'limites liberados' if tier == HOME_TIER else why})")


COMMANDS = {
    "setup": cmd_setup,
    "init": cmd_init,
    "check": cmd_check,
    "status": lambda a: cmd_status_or_run(False),
    "run": lambda a: cmd_status_or_run(True),
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
            log(f"erro de configuracao: {e}; nada alterado")
        else:
            print(f"erro: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
