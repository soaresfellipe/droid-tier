# droid-tier

[English](README.md) · **Português**

Troca automaticamente os modelos do [Droid](https://factory.ai) (Factory) conforme
os limites de uso da assinatura acabam.

A Factory tem dois pools de uso, cada um com janelas de 5h, 7 dias e mensal:

- **Standard**: modelos Anthropic, OpenAI e Google
- **Droid Core**: modelos open source (GLM, DeepSeek, MiniMax...)

Os seus padrões do Droid ficam como estão. Quando o pool que eles consomem passa
do limiar, o `droid-tier` guarda uma cópia desses padrões e troca os modelos do
`~/.factory/settings.json` para o primeiro fallback com folga. Quando a janela vira,
ele restaura exatamente o que estava lá.

```
seus padrões  ->  Droid Core (GLM, DeepSeek...)  ->  provider externo (OpenCode Go, OpenRouter...)
```

A interface e as mensagens do programa estão em português por enquanto.

## Instalação

Funciona em Linux e Windows. Requer Python 3.11+. Com [uv](https://docs.astral.sh/uv/) ou pipx:

```sh
uv tool install git+https://github.com/soaresfellipe/droid-tier
# ou: pipx install git+https://github.com/soaresfellipe/droid-tier
```

Crie uma API key da Factory em app.factory.ai/settings/api-keys e grave em
`~/.config/droid-tier/factory-api-key.env` (no Windows,
`%USERPROFILE%\.config\droid-tier\factory-api-key.env`):

```sh
FACTORY_API_KEY=fk-...
```

Não exporte essa variável no shell: o Droid passaria a usar a key no lugar do seu login.

Depois abra a interface para cadastrar providers e montar os fallbacks:

```sh
droid-tier setup
```

Por fim, agende a verificação a cada 5 minutos:

```sh
droid-tier schedule install
```

- **Linux:** cria e ativa `droid-tier.timer` no systemd do usuário. Para rodar sem
  sessão aberta, `loginctl enable-linger`.
- **Windows:** cria a Tarefa Agendada `droid-tier` (a cada 5 min e no logon, só com
  a sessão aberta, também na bateria). Ela roda com `pythonw.exe`, então nenhuma
  janela de console aparece.

`droid-tier schedule status` mostra o agendamento; `droid-tier schedule uninstall` remove.

## Interface (`droid-tier setup`)

A tela inicial mostra o degrau atual, o degrau que os limites indicam e o uso de
cada janela (5 horas, semanal, mensal) dos pools Standard e Droid Core, com o tempo
até virar. Atualiza a cada minuto (`r` força). Dali dá para aplicar a decisão na
hora, fixar um degrau, soltar o degrau fixado e restaurar seus padrões.

1. **Providers e modelos**: escolha um provider do catálogo do
   [models.dev](https://models.dev) (OpenCode Go, OpenRouter, Z.AI, DeepSeek...) ou
   informe uma URL compatível com OpenAI/Anthropic, cole a API key e marque os
   modelos que quer usar. A lista vem do `/models` do próprio provider (o que a sua
   key acessa), com contexto e suporte a imagem do models.dev.

   Os modelos marcados entram em `customModels` do `~/.factory/settings.json` e
   aparecem no seletor de modelos do Droid, mesmo fora de um fallback. Modelos que
   você cadastrou à mão continuam intocados; o droid-tier só remove os que ele
   mesmo cadastrou. Antes da primeira alteração, o settings é copiado para
   `settings.json.droid-tier.bak`.

2. **Fallbacks**: para cada fallback, escolha de onde vêm os modelos (pool Standard
   ou Droid Core da Factory, ou um provider cadastrado) e o modelo e esforço de
   cada papel. `k`/`j` reordenam.

### Esforço de raciocínio

O editor só oferece os esforços que o Droid aceita para cada modelo:

- **Modelos nativos**: os listados no `droid exec --help`.
- **customModels com o mesmo nome de um nativo** (ex.: `glm-5.3` no OpenCode Go):
  os do nativo.
- **Outros customModels** (ex.: `z-ai/glm-5.3` no OpenRouter): `off`, `low`,
  `medium` e `high`, desde que a entrada tenha `reasoningEffort`. Sem ele, o Droid
  desliga o raciocínio. O droid-tier põe `reasoningEffort` nos modelos que o
  models.dev marca como de raciocínio.

`droid-tier check` avisa quando o config pede um esforço que o modelo não aceita.

## Configuração

`config.toml` descreve só os fallbacks, em ordem. Seus padrões nunca entram nele.

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
ntfy = "https://ntfy.sh/seu-topico"
```

Vale o primeiro fallback cujo pool ainda tem folga; um fallback sem `pool` está
sempre disponível, então deixe-o por último.

Papéis: `session`, `spec`, `subagent_light`, `subagent_medium`, `subagent_heavy`,
`orchestrator`, `worker`, `validator`. Papel omitido não é alterado. O formato é
`modelo` ou `modelo@esforço`.

Num fallback com `provider`, o modelo é procurado em `customModels` do
`~/.factory/settings.json` pelo par `model` + `baseUrl`. Assim o config não depende
do ID `custom:...-N` que o Droid gera a partir do nome de exibição.

Chaves de topo opcionais (antes de qualquer tabela):

- `home_pool`: pool que seus padrões consomem (`standard`, `core` ou `none`). Sem
  ela, é deduzido pelos modelos: Claude/GPT/Gemini contam como Standard;
  GLM/DeepSeek/Kimi/MiniMax/Qwen/Nemotron como Core; `custom:` não consome pool.
- `settings`: caminho do settings.json do Droid.
- `factory_api`: padrão `https://app.factory.ai`.

### Avisos (`[notify]`)

Quando o degrau muda, e quando a API da Factory para de responder ou volta (uma
vez, não a cada rodada):

- `desktop = true`: toast no Windows, `notify-send` no Linux com sessão gráfica.
- `ntfy = "https://ntfy.sh/<tópico>"`: notificação no celular pelo app
  [ntfy](https://ntfy.sh).
- `webhook = "https://..."`: POST JSON com `title`, `message`, `event`
  (`tier_changed`, `api_error`, `api_ok`), `from`, `to` e `reason`.

Falha em aviso fica no log e nunca impede a troca.

### Seus padrões

Na primeira troca, os 8 pares modelo/esforço atuais vão para
`~/.local/state/droid-tier/home.json`. Enquanto esse arquivo existir, o Droid está
num fallback. Na volta, o settings é restaurado e o arquivo apagado.

Mudanças que você fizer nos modelos enquanto estiver num fallback se perdem na
volta. Para mudar seus padrões nesse período, edite o `home.json`.

### Sessões já abertas

A troca vale para **sessões e missões novas**. O Droid guarda o modelo de cada
sessão no arquivo da própria sessão, então uma sessão aberta (ou retomada com
`droid resume`) continua no modelo com que começou. Se o limite acabar no meio de
uma sessão, troque com `/model`.

## Comandos

```
droid-tier setup         abre a interface
droid-tier init          cria o config.toml de exemplo
droid-tier check         valida o config, sem rede
droid-tier status        mostra limites e degrau, não altera nada
droid-tier run           consulta e aplica (usado pelo timer)
droid-tier pin <degrau>  fixa um degrau ("home" = seus padrões)
droid-tier unpin         devolve o controle ao timer
droid-tier restore       restaura seus padrões agora e solta o pin
droid-tier schedule      install | uninstall | status do agendamento
```

O histórico de trocas fica em `~/.local/state/droid-tier/log`.

Os arquivos do droid-tier ficam no perfil do usuário também no Windows
(`%USERPROFILE%\.config\droid-tier`, `%USERPROFILE%\.local\state\droid-tier`),
e não em AppData: o Python da Microsoft Store redireciona gravações em AppData
para uma pasta privada, e o config ficaria invisível para o Explorer.

## Desinstalar

```sh
droid-tier schedule uninstall
droid-tier restore
uv tool uninstall droid-tier
```

## Aviso

O endpoint `/api/billing/limits` não é documentado pela Factory. Ele foi
identificado no próprio CLI do Droid e pode mudar sem aviso. Se a consulta falhar,
ou a resposta vier num formato diferente do esperado, o `droid-tier` não altera nada.

## Desenvolvimento

```sh
pip install -e ".[dev]"
pytest
```

## Licença

MIT
