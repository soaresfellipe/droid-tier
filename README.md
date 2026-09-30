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

## Estado atual

Protótipo em uso numa máquina Linux (Python 3 sem dependências + timer do systemd).
Os perfis ainda estão fixos no script (`PROFILES`).

```
droid-tier status        mostra limites e degrau, sem alterar nada
droid-tier run           consulta e aplica (usado pelo timer)
droid-tier pin <degrau>  fixa um degrau
droid-tier unpin         devolve o controle ao timer
```

Precisa de uma API key da Factory (app.factory.ai/settings/api-keys) em
`~/.config/droid-tier/factory-api-key.env` ou na variável `FACTORY_API_KEY`
só do processo do droid-tier. Não exporte no shell: o Droid passaria a usar a key
no lugar do seu login.

## Aviso

O endpoint `/api/billing/limits` não é documentado pela Factory. Ele foi
identificado no próprio CLI do Droid e pode mudar sem aviso. Se a consulta falhar,
o `droid-tier` não altera nada.

## Próximos passos

- [ ] Perfis e providers em arquivo de configuração, fora do código
- [ ] Assistente para cadastrar providers de fallback (OpenCode Go, OpenRouter, Z.AI...) como `customModels`
- [ ] Windows: Tarefa Agendada no lugar do timer do systemd
- [ ] Interface (TUI ou web local) para ver limites e editar perfis
- [ ] Testes com respostas de exemplo da API


## Licença

MIT
