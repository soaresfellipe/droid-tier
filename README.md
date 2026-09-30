# droid-tier

[![tests](https://github.com/soaresfellipe/droid-tier/actions/workflows/tests.yml/badge.svg)](https://github.com/soaresfellipe/droid-tier/actions/workflows/tests.yml)

Automatically switches the models used by [Droid](https://factory.ai) (Factory)
when your subscription's usage limits run out.

Factory has two usage pools, each with a 5-hour, 7-day and monthly window:

- **Standard**: Anthropic, OpenAI and Google models
- **Droid Core**: open-weight models (GLM, DeepSeek, MiniMax...)

Your Droid defaults stay as they are. When the pool they use goes over the
threshold, `droid-tier` saves a copy of your defaults and switches the models in
`~/.factory/settings.json` to the first fallback that still has room. When the
window resets, it restores exactly what was there.

```
your defaults  ->  Droid Core (GLM, DeepSeek...)  ->  external provider (OpenCode Go, OpenRouter...)
```

## Install

Works on Linux and Windows. Requires Python 3.11+. With [uv](https://docs.astral.sh/uv/) or pipx:

```sh
uv tool install git+https://github.com/soaresfellipe/droid-tier
# or: pipx install git+https://github.com/soaresfellipe/droid-tier
```

Create a Factory API key at app.factory.ai/settings/api-keys and save it to
`~/.config/droid-tier/factory-api-key.env` (on Windows,
`%USERPROFILE%\.config\droid-tier\factory-api-key.env`):

```sh
FACTORY_API_KEY=fk-...
```

Don't export this variable in your shell: Droid would start using the key instead
of your login.

Then open the interface to add providers and build your fallbacks:

```sh
droid-tier setup
```

Finally, schedule the check every 5 minutes:

```sh
droid-tier schedule install
```

- **Linux:** creates and enables `droid-tier.timer` in the user's systemd. To run
  without an open session, `loginctl enable-linger`.
- **Windows:** creates the `droid-tier` Scheduled Task (every 5 min and at logon,
  only while you're logged in, also on battery). It runs with `pythonw.exe`, so no
  console window pops up.

`droid-tier schedule status` shows the schedule; `droid-tier schedule uninstall` removes it.

## Interface (`droid-tier setup`)

The home screen shows the current tier, the tier the limits call for, and the
usage of each window (5 hours, weekly, monthly) for the Standard and Droid Core
pools, with the time until each resets. It refreshes every minute (`r` forces it).
From there you can apply the decision right away, pin a tier, unpin it and restore
your defaults.

1. **Providers and models**: pick a provider from the [models.dev](https://models.dev)
   catalog (OpenCode Go, OpenRouter, Z.AI, DeepSeek...) or enter an
   OpenAI/Anthropic-compatible URL, paste the API key and check the models you
   want. The list comes from the provider's own `/models` (what your key can
   access), with context size and image support from models.dev.

   Checked models are added to `customModels` in `~/.factory/settings.json` and
   show up in Droid's model picker, even outside a fallback. Models you added by
   hand are left alone; droid-tier only removes the ones it added. Before the first
   change, the settings file is copied to `settings.json.droid-tier.bak`.

2. **Fallbacks**: for each fallback, choose where its models come from (Factory's
   Standard or Droid Core pool, or one of your providers) and the model and effort
   for each role. `k`/`j` reorder them.

### Reasoning effort

The editor only offers the efforts Droid accepts for each model:

- **Native models**: the ones listed by `droid exec --help`.
- **customModels named like a native model** (e.g. `glm-5.3` on OpenCode Go): the
  native model's efforts.
- **Other customModels** (e.g. `z-ai/glm-5.3` on OpenRouter): `off`, `low`,
  `medium` and `high`, as long as the entry has `reasoningEffort`. Without it,
  Droid turns reasoning off. droid-tier sets `reasoningEffort` on models that
  models.dev marks as reasoning models.

`droid-tier check` warns when the config asks for an effort the model doesn't accept.

## Configuration

`config.toml` only describes the fallbacks, in order. Your defaults never go in it.

```toml
threshold = 95

[providers.opencode-go]
base_url = "https://opencode.ai/zen/go/v1"

[[fallback]]
name = "droid"
pool = "core"
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"

[[fallback]]
name = "oc"
provider = "opencode-go"
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"

[notify]
desktop = true
ntfy = "https://ntfy.sh/your-topic"
```

The first fallback whose pool still has room wins; a fallback without `pool` is
always available, so put it last.

Roles: `session`, `spec`, `subagent_light`, `subagent_medium`, `subagent_heavy`,
`orchestrator`, `worker`, `validator`. Roles you leave out are not changed. The
format is `model` or `model@effort`.

In a fallback with `provider`, the model is looked up in `customModels` by the
`model` + `baseUrl` pair, so the config doesn't depend on the `custom:...-N` ID
Droid derives from the display name.

Optional top-level keys (before any table):

- `home_pool`: the pool your defaults use (`standard`, `core` or `none`). Without
  it, it's inferred from the models: Claude/GPT/Gemini count as Standard;
  GLM/DeepSeek/Kimi/MiniMax/Qwen/Nemotron as Core; `custom:` uses no pool.
- `settings`: path to Droid's settings.json.
- `factory_api`: defaults to `https://app.factory.ai`.

### Notifications (`[notify]`)

Sent when the tier changes, and when Factory's API stops responding or comes back
(once, not every run):

- `desktop = true`: toast on Windows, `notify-send` on Linux with a graphical session.
- `ntfy = "https://ntfy.sh/<topic>"`: phone notification through the
  [ntfy](https://ntfy.sh) app.
- `webhook = "https://..."`: JSON POST with `title`, `message`, `event`
  (`tier_changed`, `api_error`, `api_ok`), `from`, `to` and `reason`.

A failed notification is logged and never blocks the switch.

### Your defaults

On the first switch, the current 8 model/effort pairs are saved to
`~/.local/state/droid-tier/home.json`. While that file exists, Droid is on a
fallback. On the way back, the settings are restored and the file is deleted.

Model changes you make while on a fallback are lost when your defaults come back.
To change your defaults during that time, edit `home.json`.

### Sessions that are already open

The switch applies to **new sessions and missions**. Droid stores each session's
model in the session's own file, so an open session (or one resumed with
`droid resume`) keeps the model it started with. If the limit runs out mid-session,
switch with `/model`.

## Commands

```
droid-tier setup         open the interface
droid-tier init          create the example config.toml
droid-tier check         validate the config, offline
droid-tier status        show limits and tier, change nothing
droid-tier run           check and apply (used by the schedule)
droid-tier pin <tier>    pin a tier ("home" = your defaults)
droid-tier unpin         hand control back to the schedule
droid-tier restore       restore your defaults now and unpin
droid-tier schedule      install | uninstall | status
```

The switch history is in `~/.local/state/droid-tier/log`.

droid-tier's files live in your user profile on Windows too
(`%USERPROFILE%\.config\droid-tier`, `%USERPROFILE%\.local\state\droid-tier`),
not in AppData: the Microsoft Store Python redirects writes to AppData into a
private folder, and the config would be invisible to Explorer.

## Uninstall

```sh
droid-tier schedule uninstall
droid-tier restore
uv tool uninstall droid-tier
```

## Caveat

The `/api/billing/limits` endpoint isn't documented by Factory. It was found in
Droid's own CLI and may change without notice. If the request fails, or the
response isn't in the expected shape, `droid-tier` changes nothing.

## Ideas

- **Local web interface**: a light page served on `127.0.0.1`, calling the same
  core as the terminal interface. It edits Droid's settings (which hold API keys),
  so it needs a random per-launch token in the URL and a `Host` header check
  against CSRF and DNS rebinding. On a headless server, reach it through an SSH
  tunnel, never on a network interface.

## Development

```sh
pip install -e ".[dev]"
pytest
```

## License

MIT
