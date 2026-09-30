"""droid-tier command line.

Usage:
  droid-tier setup         open the interface: limits, providers, models and fallbacks
  droid-tier init          create the example config.toml
  droid-tier check         validate the config against Droid's settings.json, offline
  droid-tier status        show limits and tier, change nothing
  droid-tier run           fetch limits and apply the tier (used by the schedule)
  droid-tier pin <tier>    apply and pin a tier ("home" = your defaults)
  droid-tier unpin         hand control back to the schedule
  droid-tier restore       restore your defaults now and unpin
  droid-tier schedule [install|uninstall|status]
                           run `run` every 5 min (systemd on Linux, Scheduled Task on Windows)
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
        sys.exit(f"error: {e}")


def cmd_setup(_):
    from .tui import run_tui  # Textual only loads here; the scheduled run stays light
    run_tui()


def cmd_init(_):
    if os.path.exists(CONFIG_FILE):
        sys.exit(f"{CONFIG_FILE} already exists; not overwriting it")
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(EXAMPLE_CONFIG)
    print(f"created {CONFIG_FILE}; adjust the fallbacks and run `droid-tier check`")


def print_profile(profile, settings=None, natives=()):
    customs = {m.get("id"): m for m in (settings or {}).get("customModels") or []}
    for role, (model, effort) in profile.items():
        note = ""
        if effort and settings is not None:
            allowed = catalog.efforts_for(model, natives, customs.get(model)) if model else None
            if allowed and effort not in allowed:
                note = f"   <- Droid doesn't accept {effort} here (accepts: {', '.join(allowed)})"
        print(f"  {role:16} {model or '-'}" + (f" @{effort}" if effort else "") + note)


def cmd_check(_):
    cfg = load_config()
    d = Droid(cfg)
    natives = catalog.native_models()
    hp = home_pool(cfg, d.home)
    origin = "saved in " + HOME_FILE if d.saved_home else "in settings.json"
    print(f"home: your defaults, {origin} (pool {hp or 'none'})")
    print_profile(d.home)
    for fb in cfg["fallbacks"]:
        origin = f"pool {fb['pool']}" if fb.get("pool") else f"provider {fb.get('provider') or '(none)'}"
        print(f"{fb['name']} ({origin})")
        print_profile(d.fallbacks[fb["name"]], d.s, natives)
    if cfg["fallbacks"][-1].get("pool"):
        print(f"warning: the last fallback depends on the {cfg['fallbacks'][-1]['pool']} pool; "
              "without a fallback that has no pool, it stays even when exhausted")
    print(f"current tier: {d.current()}")


def cmd_pin(args):
    cfg = load_config()
    if not args:
        sys.exit(f"usage: droid-tier pin {{{'|'.join(core.tier_names(cfg))}}}")
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
        sys.exit(f"failed to fetch limits: {e}")
    print(f"current tier: {st['current']}" + (f" (pinned to {st['pin']})" if st["pin"] else ""))
    print(f"tier from limits (threshold {cfg['threshold']:.0f}%, defaults on the "
          f"{st['home_pool'] or 'no'} pool): {st['tier']}")
    print(describe(st["limits"]))
    if st["quotas"]:
        print(core.describe_quotas(st["quotas"]))


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
            core.log(f"config error: {e}; nothing changed")
        else:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
