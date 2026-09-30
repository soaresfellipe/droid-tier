# droid-tier

Troca automaticamente os modelos do [Droid](https://factory.ai) (Factory) conforme
os limites de uso da assinatura acabam.

A Factory tem dois pools de uso, cada um com janelas de 5h, 7 dias e mensal:

- **Standard**: modelos Anthropic, OpenAI e Google
- **Droid Core**: modelos open source (GLM, DeepSeek, MiniMax...)

Quando um pool passa do limiar, o `droid-tier` reescreve os campos de modelo do
`~/.factory/settings.json` para o próximo degrau. Quando a janela vira, ele volta sozinho.

```
standard  ->  droid (Core)  ->  fallback (customModels: OpenCode Go, OpenRouter, ...)
```

## Instalação (Linux)

Requer Python 3.11+ (sem dependências).

```sh
install -m 755 droid-tier ~/.local/bin/droid-tier
droid-tier init            # cria ~/.config/droid-tier/config.toml
droid-tier check           # valida o config contra o settings.json do Droid
```

Crie uma API key da Factory em app.factory.ai/settings/api-keys e grave em
`~/.config/droid-tier/factory-api-key.env` (permissão 600):

```sh
FACTORY_API_KEY=fk-...
```

Não exporte essa variável no shell: o Droid passaria a usar a key no lugar do seu login.

Para rodar a cada 5 minutos com systemd:

```sh
cp contrib/systemd/droid-tier.* ~/.config/systemd/user/
systemctl --user enable --now droid-tier.timer
```

## Configuração

Os degraus ficam em `config.toml`, em ordem de preferência. Vale o primeiro cujo
pool da Factory ainda tem folga; um degrau sem `pool` (provider de fallback) está
sempre disponível.

```toml
threshold = 95

[providers.opencode-go]
base_url = "https://opencode.ai/zen/go/v1"

[[tier]]
name = "droid"
pool = "core"
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"

[[tier]]
name = "oc"
provider = "opencode-go"
session = "glm-5.3-flash@high"
spec = "glm-5.3@high"
```

Papéis: `session`, `spec`, `subagent_light`, `subagent_medium`, `subagent_heavy`,
`orchestrator`, `worker`, `validator`. Papel omitido não é alterado. O formato é
`modelo` ou `modelo@esforço`.

Num degrau com `provider`, o modelo é procurado em `customModels` do
`~/.factory/settings.json` pelo par `model` + `baseUrl`. Assim o config não depende
do ID `custom:...-N` que o Droid gera a partir da posição na lista.

Outras chaves de topo (antes de qualquer tabela): `settings` (caminho do
settings.json do Droid) e `factory_api` (padrão `https://app.factory.ai`).

## Comandos

```
droid-tier init          cria o config.toml de exemplo
droid-tier check         valida o config, sem rede
droid-tier status        mostra limites e degrau, não altera nada
droid-tier run           consulta e aplica (usado pelo timer)
droid-tier pin <degrau>  fixa um degrau
droid-tier unpin         devolve o controle ao timer
```

O histórico de trocas fica em `~/.local/state/droid-tier/log`.

## Aviso

O endpoint `/api/billing/limits` não é documentado pela Factory. Ele foi
identificado no próprio CLI do Droid e pode mudar sem aviso. Se a consulta falhar,
o `droid-tier` não altera nada.

## Próximos passos

- [x] Perfis e providers em arquivo de configuração, fora do código
- [ ] Assistente para cadastrar providers de fallback (OpenCode Go, OpenRouter, Z.AI...) como `customModels`
- [ ] Windows: Tarefa Agendada no lugar do timer do systemd
- [ ] Interface (TUI ou web local) para ver limites e editar perfis
- [x] Testes com respostas de exemplo da API (`python -m unittest discover -s tests`)

## Licença

MIT
