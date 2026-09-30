# droid-tier

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
```

Vale o primeiro fallback cujo pool ainda tem folga; um fallback sem `pool` está
sempre disponível, então deixe-o por último.

Papéis: `session`, `spec`, `subagent_light`, `subagent_medium`, `subagent_heavy`,
`orchestrator`, `worker`, `validator`. Papel omitido não é alterado. O formato é
`modelo` ou `modelo@esforço`.

Num fallback com `provider`, o modelo é procurado em `customModels` do
`~/.factory/settings.json` pelo par `model` + `baseUrl`. Assim o config não depende
do ID `custom:...-N` que o Droid gera a partir da posição na lista.

Chaves de topo opcionais (antes de qualquer tabela):

- `home_pool`: pool que seus padrões consomem (`standard`, `core` ou `none`). Sem
  ela, é deduzido pelos modelos: Claude/GPT/Gemini contam como Standard;
  GLM/DeepSeek/Kimi/MiniMax/Qwen/Nemotron como Core; `custom:` não consome pool.
- `settings`: caminho do settings.json do Droid.
- `factory_api`: padrão `https://app.factory.ai`.

### Seus padrões

Na primeira troca, os 8 pares modelo/esforço atuais vão para
`~/.local/state/droid-tier/home.json`. Enquanto esse arquivo existir, o Droid está
num fallback. Na volta, o settings é restaurado e o arquivo apagado.

Mudanças que você fizer nos modelos enquanto estiver num fallback se perdem na
volta. Para mudar seus padrões nesse período, edite o `home.json`.

## Comandos

```
droid-tier init          cria o config.toml de exemplo
droid-tier check         valida o config, sem rede
droid-tier status        mostra limites e degrau, não altera nada
droid-tier run           consulta e aplica (usado pelo timer)
droid-tier pin <degrau>  fixa um degrau ("home" = seus padrões)
droid-tier unpin         devolve o controle ao timer
droid-tier restore       restaura seus padrões agora e solta o pin
droid-tier schedule      install | uninstall | status do agendamento
droid-tier setup         abre a interface
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
o `droid-tier` não altera nada.

## Próximos passos

- [x] Perfis e providers em arquivo de configuração, fora do código
- [x] Interface para cadastrar providers, escolher modelos e montar fallbacks
- [x] Windows: Tarefa Agendada (`droid-tier schedule install`)
- [ ] Ver limites e degrau atual na interface
- [x] Testes com respostas de exemplo da API (`pytest`)

## Licença

MIT
