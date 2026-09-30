"""Interface (Textual) para cadastrar providers, escolher modelos e montar fallbacks."""
import os
import shutil

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, Footer, Header, Input, Label, OptionList, Select,
                             SelectionList, Static)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from . import catalog, configedit, core

CUSTOM = "__custom__"
ADD = "__add__"
ROLE_LABELS = {
    "session": "Sessão: padrão",
    "spec": "Sessão: spec",
    "subagent_light": "Subagente leve",
    "subagent_medium": "Subagente médio",
    "subagent_heavy": "Subagente pesado",
    "orchestrator": "Missão: orquestrador",
    "worker": "Missão: worker",
    "validator": "Missão: validator",
}


def focus_list(ol, index=None):
    """Foca a lista com um item destacado, para o Enter funcionar de cara."""
    if ol.option_count:
        ol.highlighted = index if index is not None and index < ol.option_count else 0
    ol.focus()


def fmt_tokens(n):
    if not n:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    return f"{round(n / 1000)}k"


class State:
    """Config (tomlkit) + settings do Droid carregados, com escrita segura."""

    def __init__(self, config_path=None):
        self.config_path = config_path or core.CONFIG_FILE
        self.doc = configedit.load_doc(self.config_path)
        self.settings_path = os.path.expanduser(self.doc.get("settings") or core.DEFAULT_SETTINGS)
        self.settings = core.load_settings(self.settings_path) if os.path.exists(self.settings_path) else {}
        self.backed_up = False
        self.md = None
        self.natives = None

    def save_config(self):
        configedit.save_doc(self.doc, self.config_path)

    def save_settings(self):
        if not self.backed_up and os.path.exists(self.settings_path):
            shutil.copy2(self.settings_path, self.settings_path + ".droid-tier.bak")
            self.backed_up = True
        core.write_settings(self.settings_path, self.settings)

    def providers(self):
        return configedit.get_providers(self.doc)

    def seen_native_ids(self):
        """IDs nativos ja usados no settings, nos padroes guardados ou nos fallbacks."""
        seen = {m for m, _ in core.snapshot(self.settings).values()}
        seen |= {m for m, _ in (core.read_home() or {}).values()}
        for fb in configedit.get_fallbacks(self.doc):
            if fb.get("pool"):
                seen |= {(fb.get(r) or "").split("@")[0] for r in core.ROLES}
        return sorted(m for m in seen if m and not m.startswith("custom:"))

    def provider_models(self, pid):
        p = self.providers().get(pid) or {}
        managed = set(p.get("models") or [])
        manual = {m.get("model") for m in catalog.provider_entries(self.settings, p.get("base_url", ""))}
        return sorted(managed | manual)


# ---------------------------------------------------------------- telas auxiliares

class Confirm(ModalScreen[bool]):
    def __init__(self, message):
        super().__init__()
        self.message = message

    def compose(self):
        with Vertical(classes="dialog"):
            yield Static(self.message)
            with Horizontal(classes="buttons"):
                yield Button("Sim", variant="error", id="yes")
                yield Button("Não", id="no")

    @on(Button.Pressed)
    def done(self, event):
        self.dismiss(event.button.id == "yes")


# ---------------------------------------------------------------- menu

class MainScreen(Screen):
    BINDINGS = [Binding("q", "app.quit", "Sair")]

    def compose(self):
        yield Header()
        with Vertical(classes="body"):
            yield Static(id="summary")
            yield OptionList(
                Option("Providers e modelos", id="providers"),
                Option("Fallbacks", id="fallbacks"),
                Option("Sair", id="quit"),
                id="menu",
            )
        yield Footer()

    def on_screen_resume(self):
        st = self.app.state
        providers = st.providers()
        fbs = configedit.get_fallbacks(st.doc)
        lines = [f"config: {st.config_path}", f"settings do Droid: {st.settings_path}", ""]
        counts = [f"{pid} ({len(p.get('models') or [])})" for pid, p in providers.items()]
        lines.append("providers: " + (", ".join(counts) or "nenhum"))
        lines.append("fallbacks: " + (" → ".join(fb["name"] for fb in fbs) or "nenhum"))
        self.query_one("#summary", Static).update("\n".join(lines))

    def on_mount(self):
        self.on_screen_resume()

    @on(OptionList.OptionSelected, "#menu")
    def pick(self, event):
        if event.option.id == "providers":
            self.app.push_screen(ProvidersScreen())
        elif event.option.id == "fallbacks":
            self.app.push_screen(FallbacksScreen())
        else:
            self.app.exit()


# ---------------------------------------------------------------- providers

class ProvidersScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Voltar"), Binding("d", "remove", "Remover provider")]

    def compose(self):
        yield Header()
        with Vertical(classes="body"):
            yield Static("Providers de fallback. Enter edita os modelos; d remove.", classes="hint")
            yield OptionList(id="list")
        yield Footer()

    def on_screen_resume(self):
        ol = self.query_one("#list", OptionList)
        ol.clear_options()
        for pid, p in self.app.state.providers().items():
            n = len(p.get("models") or [])
            ol.add_option(Option(f"{p.get('name') or pid}  ·  {n} modelo(s)  ·  {p.get('base_url', '')}", id=pid))
        ol.add_option(Option("+ Adicionar provider", id=ADD))
        focus_list(ol)

    def on_mount(self):
        self.on_screen_resume()

    @on(OptionList.OptionSelected)
    def pick(self, event):
        if event.option.id == ADD:
            self.app.push_screen(ProviderPickScreen())
            return
        p = self.app.state.providers()[event.option.id]
        prov = catalog.Provider(event.option.id, p.get("name") or event.option.id, p["base_url"],
                                p.get("kind") or "generic-chat-completion-api")
        self.app.push_screen(KeyScreen(prov, prefix=p.get("prefix"), existing=True))

    def action_remove(self):
        ol = self.query_one("#list", OptionList)
        if ol.highlighted is None:
            return
        pid = ol.get_option_at_index(ol.highlighted).id
        if pid == ADD:
            return
        st = self.app.state
        users = configedit.fallback_users(st.doc, pid)
        if users:
            self.notify(f"Usado pelos fallbacks: {', '.join(users)}. Remova-os antes.", severity="error")
            return
        p = st.providers()[pid]
        managed = set(p.get("models") or [])
        ids = [e["id"] for e in catalog.provider_entries(st.settings, p["base_url"]) if e.get("model") in managed]
        busy = catalog.in_use(st.settings, core.read_home(), ids)
        if busy:
            self.notify(f"Em uso no settings do Droid: {', '.join(busy)}", severity="error")
            return

        def go(yes):
            if not yes:
                return
            prov = catalog.Provider(pid, p.get("name") or pid, p["base_url"], p.get("kind") or "")
            catalog.sync_custom_models(st.settings, prov, [], "", "", managed)
            st.save_settings()
            configedit.remove_provider(st.doc, pid)
            st.save_config()
            self.notify(f"{pid} removido")
            self.on_screen_resume()

        self.app.push_screen(Confirm(f"Remover {pid} e os {len(managed)} modelo(s) que o droid-tier cadastrou?"), go)


class ProviderPickScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Voltar")]

    def compose(self):
        yield Header()
        with Vertical(classes="body"):
            yield Static("Escolha o provider (catálogo do models.dev).", classes="hint")
            yield Input(placeholder="buscar provider…", id="search")
            yield OptionList(id="list")
        yield Footer()

    def on_mount(self):
        self.query_one("#list", OptionList).add_option(Option("carregando catálogo…", disabled=True))
        self.run_worker(self.load, thread=True)

    def load(self):
        st = self.app.state
        try:
            if st.md is None:
                st.md = catalog.load_models_dev()
            self.all = catalog.providers(st.md)
        except Exception as e:  # sem rede e sem cache
            self.all = []
            self.app.call_from_thread(self.notify, f"Catálogo indisponível: {e}", severity="warning")
        self.app.call_from_thread(self.refresh_list)

    @on(Input.Changed, "#search")
    def refresh_list(self, _=None):
        q = self.query_one("#search", Input).value.lower().strip()
        ol = self.query_one("#list", OptionList)
        ol.clear_options()
        ol.add_option(Option("+ URL personalizada (compatível com OpenAI ou Anthropic)", id=CUSTOM))
        for p in getattr(self, "all", []):
            if q and q not in p.name.lower() and q not in p.id.lower():
                continue
            ol.add_option(Option(Text(f"{p.name}  ·  {p.base_url}"), id=p.id))
        ol.highlighted = 1 if q and ol.option_count > 1 else 0

    @on(Input.Submitted, "#search")
    def to_list(self):
        self.query_one("#list", OptionList).focus()

    @on(OptionList.OptionSelected, "#list")
    def pick(self, event):
        if event.option.id == CUSTOM:
            self.app.push_screen(KeyScreen(None))
        else:
            prov = next(p for p in self.all if p.id == event.option.id)
            self.app.push_screen(KeyScreen(prov))


class KeyScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Voltar")]

    def __init__(self, provider, prefix=None, existing=False):
        super().__init__()
        self.provider = provider
        self.prefix = prefix
        self.existing = existing

    def compose(self):
        p = self.provider
        st = self.app.state
        key = catalog.provider_api_key(st.settings, p.base_url) if p else ""
        yield Header()
        with VerticalScroll(classes="body form"):
            if p is None:
                yield Label("ID do provider (usado no config)")
                yield Input(id="pid", placeholder="meu-provider")
                yield Label("Nome")
                yield Input(id="pname", placeholder="Meu Provider")
                yield Label("Base URL")
                yield Input(id="base", placeholder="https://api.exemplo.com/v1")
                yield Label("Tipo de API")
                yield Select([(k, k) for k in catalog.DROID_KINDS], value=catalog.DROID_KINDS[0],
                             allow_blank=False, id="kind")
            else:
                yield Static(f"[b]{p.name}[/b]\n{p.base_url}\ntipo: {p.kind}")
            yield Label("API key")
            yield Input(value=key, password=True, id="key")
            yield Label("Prefixo do nome no seletor do Droid (ex.: OC → \"OC GLM-5.3\")")
            yield Input(value=self.prefix if self.prefix is not None else (p.name if p else ""), id="prefix")
            yield Button("Buscar modelos", variant="primary", id="go")
        yield Footer()

    @on(Button.Pressed, "#go")
    @on(Input.Submitted)
    def go(self, _=None):
        p = self.provider
        if p is None:
            pid = self.query_one("#pid", Input).value.strip()
            base = self.query_one("#base", Input).value.strip().rstrip("/")
            if not pid or not base.startswith("http"):
                self.notify("Informe o ID e uma base URL http(s).", severity="error")
                return
            p = catalog.Provider(pid, self.query_one("#pname", Input).value.strip() or pid, base,
                                 self.query_one("#kind", Select).value)
        key = self.query_one("#key", Input).value.strip()
        if not key:
            self.notify("Informe a API key.", severity="error")
            return
        prefix = self.query_one("#prefix", Input).value.strip()
        self.app.push_screen(ModelPickScreen(p, key, prefix))


class ModelPickScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Voltar"), Binding("ctrl+s", "save", "Salvar")]

    def __init__(self, provider, api_key, prefix):
        super().__init__()
        self.provider = provider
        self.api_key = api_key
        self.prefix = prefix
        self.models = []
        self.chosen = set()

    def compose(self):
        yield Header()
        with Vertical(classes="body"):
            yield Static(f"{self.provider.name}: carregando modelos…", id="info")
            yield Input(placeholder="buscar modelo…", id="search")
            yield SelectionList(id="list")
            with Horizontal(classes="buttons"):
                yield Button("Salvar (ctrl+s)", variant="primary", id="save")
        yield Footer()

    def on_mount(self):
        st = self.app.state
        p = st.providers().get(self.provider.id) or {}
        self.managed = set(p.get("models") or [])
        self.chosen = {e.get("model") for e in catalog.provider_entries(st.settings, self.provider.base_url)}
        self.run_worker(self.load, thread=True)

    def load(self):
        st = self.app.state
        try:
            if st.md is None:
                st.md = catalog.load_models_dev()
        except Exception:
            st.md = {}
        self.models, live = catalog.list_models(self.provider, self.api_key, st.md)
        src = "lista ao vivo do provider" if live else "catálogo do models.dev (o provider não respondeu a /models)"
        msg = f"{self.provider.name}: {len(self.models)} modelos, {src}. Espaço marca, ctrl+s salva."
        self.app.call_from_thread(self.query_one("#info", Static).update, msg)
        self.app.call_from_thread(self.refresh_list)

    @on(Input.Changed, "#search")
    def refresh_list(self, _=None):
        q = self.query_one("#search", Input).value.lower().strip()
        sl = self.query_one("#list", SelectionList)
        sl.clear_options()
        for m in self.models:
            if q and q not in m.id.lower() and q not in m.name.lower():
                continue
            label = Text(f"{m.id:<42} {fmt_tokens(m.context):>6} ctx  {'img' if m.image else '   '}  {m.name if m.name != m.id else ''}")
            sl.add_option(Selection(label, m.id, m.id in self.chosen))
        if sl.option_count:
            sl.highlighted = 0

    @on(Input.Submitted, "#search")
    def to_list(self):
        self.query_one("#list", SelectionList).focus()

    @on(SelectionList.SelectionToggled)
    def toggled(self, event):
        mid = event.selection.value
        self.chosen.symmetric_difference_update({mid})

    @on(Button.Pressed, "#save")
    def action_save(self):
        st = self.app.state
        selected = [m for m in self.models if m.id in self.chosen]
        # Modelos marcados que nao vieram na lista (cadastrados a mao) continuam.
        known = {m.id for m in self.models}
        selected += [catalog.Model(mid) for mid in self.chosen - known if mid]
        dropping = self.managed - self.chosen
        ids = [e["id"] for e in catalog.provider_entries(st.settings, self.provider.base_url)
               if e.get("model") in dropping]
        busy = catalog.in_use(st.settings, core.read_home(), ids)
        if busy:
            self.notify(f"Não dá para desmarcar, em uso no Droid: {', '.join(busy)}", severity="error")
            return
        used_by_fb = [fb["name"] for fb in configedit.get_fallbacks(st.doc)
                      if fb.get("provider") == self.provider.id
                      and any((fb.get(r) or "").split("@")[0] in dropping for r in core.ROLES)]
        if used_by_fb:
            self.notify(f"Desmarcados em uso pelos fallbacks: {', '.join(used_by_fb)}", severity="error")
            return
        added, removed = catalog.sync_custom_models(st.settings, self.provider, selected, self.api_key,
                                                    self.prefix, self.managed)
        st.save_settings()
        # O droid-tier passa a gerenciar tudo que foi marcado aqui.
        configedit.upsert_provider(st.doc, self.provider.id, name=self.provider.name,
                                   base_url=self.provider.base_url, kind=self.provider.kind,
                                   prefix=self.prefix, models=sorted(self.chosen))
        st.save_config()
        self.notify(f"{len(added)} adicionado(s), {len(removed)} removido(s) em customModels")
        self.app.pop_screen()
        # volta para a lista de providers
        while not isinstance(self.app.screen, (ProvidersScreen, MainScreen)):
            self.app.pop_screen()


# ---------------------------------------------------------------- fallbacks

class FallbacksScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Voltar"),
        Binding("d", "remove", "Remover"),
        Binding("k", "move(-1)", "Subir"),
        Binding("j", "move(1)", "Descer"),
    ]

    def compose(self):
        yield Header()
        with Vertical(classes="body"):
            yield Static("Fallbacks em ordem: vale o primeiro com folga. Enter edita; k/j reordena; d remove.",
                         classes="hint")
            yield OptionList(id="list")
        yield Footer()

    def on_screen_resume(self):
        ol = self.query_one("#list", OptionList)
        keep = ol.highlighted
        ol.clear_options()
        for i, fb in enumerate(configedit.get_fallbacks(self.app.state.doc), 1):
            src = f"pool {fb['pool']}" if fb.get("pool") else f"provider {fb.get('provider')}"
            roles = sum(1 for r in core.ROLES if fb.get(r))
            ol.add_option(Option(f"{i}. {fb['name']}  ·  {src}  ·  {roles}/8 papéis", id=str(i - 1)))
        ol.add_option(Option("+ Novo fallback", id=ADD))
        focus_list(ol, keep)

    def on_mount(self):
        self.on_screen_resume()

    @on(OptionList.OptionSelected)
    def pick(self, event):
        idx = None if event.option.id == ADD else int(event.option.id)
        self.app.push_screen(FallbackEditScreen(idx))

    def _index(self):
        ol = self.query_one("#list", OptionList)
        if ol.highlighted is None:
            return None
        oid = ol.get_option_at_index(ol.highlighted).id
        return None if oid == ADD else int(oid)

    def action_move(self, delta):
        i = self._index()
        fbs = configedit.get_fallbacks(self.app.state.doc)
        if i is None or not 0 <= i + delta < len(fbs):
            return
        fbs[i], fbs[i + delta] = fbs[i + delta], fbs[i]
        configedit.set_fallbacks(self.app.state.doc, fbs)
        self.app.state.save_config()
        self.query_one("#list", OptionList).highlighted = i + delta
        self.on_screen_resume()

    def action_remove(self):
        i = self._index()
        if i is None:
            return
        fbs = configedit.get_fallbacks(self.app.state.doc)

        def go(yes):
            if yes:
                del fbs[i]
                configedit.set_fallbacks(self.app.state.doc, fbs)
                self.app.state.save_config()
                self.on_screen_resume()

        self.app.push_screen(Confirm(f"Remover o fallback {fbs[i]['name']}?"), go)


class FallbackEditScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Voltar"), Binding("ctrl+s", "save", "Salvar")]

    def __init__(self, index):
        super().__init__()
        self.index = index
        self.extra = set()

    def compose(self):
        st = self.app.state
        fbs = configedit.get_fallbacks(st.doc)
        self.fb = fbs[self.index] if self.index is not None else {"name": ""}
        sources = [("Factory: pool Standard (Claude, GPT, Gemini…)", "pool:standard"),
                   ("Factory: pool Droid Core (GLM, DeepSeek…)", "pool:core")]
        sources += [(f"Provider: {p.get('name') or pid}", f"provider:{pid}") for pid, p in st.providers().items()]
        current = (f"pool:{self.fb['pool']}" if self.fb.get("pool")
                   else f"provider:{self.fb['provider']}" if self.fb.get("provider") else Select.NULL)
        yield Header()
        with VerticalScroll(classes="body form"):
            yield Label("Nome")
            yield Input(value=self.fb.get("name", ""), id="name")
            yield Label("De onde vêm os modelos")
            yield Select(sources, value=current, prompt="escolha…", id="source")
            yield Label("Modelo que não aparece na lista? Digite o ID e Enter")
            yield Input(placeholder="ex.: deepseek-v4.1-flash", id="extra")
            yield Static("Papel sem modelo = não alterado. Esforço vazio = o que estiver no settings.",
                         classes="hint")
            for role, label in ROLE_LABELS.items():
                with Horizontal(classes="role"):
                    yield Label(label, classes="role-label")
                    yield Select([], prompt="não alterar", id=f"m-{role}", classes="role-model")
                    yield Select([(e, e) for e in catalog.EFFORTS], prompt="esforço", id=f"e-{role}",
                                 classes="role-effort")
            yield Button("Salvar (ctrl+s)", variant="primary", id="save")
        yield Footer()

    def on_mount(self):
        st = self.app.state
        if st.natives is None:
            self.run_worker(self.load_natives, thread=True)
        else:
            self.fill_models()

    def load_natives(self):
        self.app.state.natives = catalog.native_models()
        self.app.call_from_thread(self.fill_models)

    @on(Select.Changed, "#source")
    def fill_models(self, _=None):
        st = self.app.state
        source = self.query_one("#source", Select).value
        options = []
        if isinstance(source, str) and source.startswith("pool:"):
            pool = source[5:]
            natives = st.natives or []
            if not natives:
                self.notify("Não achei o `droid` no PATH para listar os modelos da Factory.", severity="warning")
            options = [(f"{m.name} ({m.id})", m.id) for m in natives if m.pool == pool and not m.deprecated]
            # O `droid exec --help` omite modelos que funcionam; os que ja estao em uso entram tambem.
            listed = {v for _, v in options}
            options += [(f"{mid} (fora da lista)", mid) for mid in st.seen_native_ids()
                        if mid not in listed and core.infer_pool({"x": (mid, None)}) == pool]
        elif isinstance(source, str) and source.startswith("provider:"):
            options = [(mid, mid) for mid in st.provider_models(source[9:])]
        values = {v for _, v in options}
        options += [(f"{mid} (digitado)", mid) for mid in sorted(self.extra) if mid not in values]
        values |= self.extra
        for role in core.ROLES:
            sel = self.query_one(f"#m-{role}", Select)
            saved, _, effort = (self.fb.get(role) or "").partition("@")
            model = sel.value if isinstance(sel.value, str) else saved
            options_role = options
            if model and model not in values:
                options_role = options + [(f"{model} (fora da lista)", model)]
            sel.set_options(options_role)
            if model:
                sel.value = model
            esel = self.query_one(f"#e-{role}", Select)
            if effort and esel.value is Select.NULL:
                esel.value = effort

    @on(Input.Submitted, "#extra")
    def add_extra(self, event):
        mid = event.value.strip()
        if mid:
            self.extra.add(mid)
            event.input.value = ""
            self.fill_models()
            self.notify(f"{mid} adicionado às opções")

    @on(Button.Pressed, "#save")
    def action_save(self):
        st = self.app.state
        name = self.query_one("#name", Input).value.strip()
        source = self.query_one("#source", Select).value
        if not name or name == core.HOME_TIER:
            self.notify("Dê um nome ao fallback (\"home\" é reservado).", severity="error")
            return
        if source is Select.NULL:
            self.notify("Escolha de onde vêm os modelos.", severity="error")
            return
        fbs = configedit.get_fallbacks(st.doc)
        if any(fb["name"] == name for i, fb in enumerate(fbs) if i != self.index):
            self.notify(f"Já existe um fallback {name!r}.", severity="error")
            return
        kind, _, ref = source.partition(":")
        new = {"name": name, kind: ref}
        for role in core.ROLES:
            model = self.query_one(f"#m-{role}", Select).value
            effort = self.query_one(f"#e-{role}", Select).value
            if model is Select.NULL:
                continue
            new[role] = model + (f"@{effort}" if effort is not Select.NULL else "")
        if not any(r in new for r in core.ROLES):
            self.notify("Escolha o modelo de pelo menos um papel.", severity="error")
            return
        if self.index is None:
            fbs.append(new)
        else:
            fbs[self.index] = new
        configedit.set_fallbacks(st.doc, fbs)
        st.save_config()
        try:
            cfg = core.load_config(st.config_path)
            core.resolve_all(cfg, st.settings)
        except core.ConfigError as e:
            self.notify(f"Salvo, mas o config tem um problema: {e}", severity="warning", timeout=10)
        else:
            self.notify(f"Fallback {name} salvo")
        self.app.pop_screen()


# ---------------------------------------------------------------- app

class DroidTierApp(App):
    TITLE = "droid-tier"
    SUB_TITLE = "providers, modelos e fallbacks"
    CSS = """
    .body { padding: 1 2; }
    .hint { color: $text-muted; margin-bottom: 1; }
    #summary { margin-bottom: 1; }
    .form Label { margin-top: 1; }
    .form Button { margin-top: 1; }
    .buttons { height: auto; margin-top: 1; }
    .buttons Button { margin-right: 2; }
    .dialog { width: 60; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    Confirm { align: center middle; }
    .role { height: auto; }
    .role-label { width: 22; margin-top: 1; }
    .role-model { width: 1fr; }
    .role-effort { width: 18; }
    SelectionList { height: 1fr; }
    """

    def __init__(self, config_path=None):
        super().__init__()
        self.state = State(config_path)

    def on_mount(self):
        self.push_screen(MainScreen())


def run_tui(config_path=None):
    DroidTierApp(config_path).run()
