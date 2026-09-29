# OrqueHelx

[Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that lets one model delegate work to
another, each running on your own subscription: Claude, Codex, Antigravity, OpenCode or any CLI that speaks ACP.

Hermes' `delegate_task` sends every child to the one target set in `config.yaml`. With OrqueHelx the model picks
a route on each call, and the subagent runs on that provider and spends that quota. If a subscription runs out,
you decide where the task goes next; the plugin doesn't switch providers for you.

**Status:** v0.1.4. `delegar`, `/ohx rutas|cuota` and the OrqueHelx dashboard tab work: routes with their
measured quota, a chat with the main agent, a live subagent tree, and a quota pause until the reset. Two cases
have not been tested against a real subscription yet: a subagent that runs out of quota, and nested subagents.
Tests cover them with errors captured from real providers and hand-built ones.

Español: [más abajo](#en-español).

## Requirements

- Hermes Agent 0.21.5 or later.
- The CLIs you want to use, installed and signed in (`claude`, `agy`, `opencode`, ...). OrqueHelx stores no
  credentials: each CLI uses its own.
- On Windows, the Antigravity route needs hermes-antigravity-subscription 1.0.2 or later. Older versions miss
  the session `agy` keeps in the Windows Credential Manager. To update:
  `hermes plugins update antigravity-subscription-directsdk`.

## Install

```bash
hermes plugins install TicoraX/OrqueHelx#orquehelx --enable
```

## Routes

A route is the name the model uses to pick where a subagent runs. Routes live in `~/.hermes/config.yaml`:

```yaml
plugins:
  entries:
    orquehelx:
      settings:
        rutas:
          claude:   {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
          agy:      {provider: antigravity-subscription-directsdk, model: flash}
          opencode: {acp: [opencode, acp]}
```

- `provider`: any provider Hermes already knows, including the subscription plugins in the catalog
  (`hermes plugins install claude-subscription-directsdk`).
- `acp`: the command of any CLI that speaks ACP. OrqueHelx registers it as a provider, no extra plugin needed.

With no routes configured, the plugin still loads: `delegar` and `/ohx` tell you what to set, and the native
`delegate_task` keeps working. A malformed route fails at load time, and Hermes shows the error naming the route.

## When a subscription runs out of quota

If a subagent fails because its provider ran out of quota, `delegar` returns `estado: sin_cuota` to the parent
model, with the reset time (or `null` when the provider does not report it: it is never estimated) and the other
available routes. What the parent does next is set by `politica`:

```yaml
    orquehelx:
      settings:
        politica: preguntar     # preguntar (default) | padre_decide | esperar
```

- `preguntar` (ask): the parent tells you and asks whether to send the task to another route, naming each
  route's model.
- `padre_decide` (parent decides): the parent may pick another route if its model fits the task, and tells you
  which one.
- `esperar` (wait): the parent tells you and does nothing else until your next message.

Quota counts as exhausted only when the error says so unambiguously (for example Antigravity's
`RESOURCE_EXHAUSTED`) or when it is measured (a Claude or Codex window at 100 %). Any other failure is not
reported as a quota problem.

### When the main agent runs out

In the OrqueHelx tab the conversation pauses: the turn is kept and the model is not switched for you. The pause
card shows the provider, the model and the reset time, with three ways out:

- **Reanudar** (resume): the same turn again on the same model. If the provider reports a reset time, it
  resumes on its own at that time while the tab is open.
- **Reenviar** (resend): the same turn on another route you pick (routes with `model`, on another provider).
  The conversation continues on that model.
- **Cancelar** (cancel): leaves the turn unanswered and frees the chat.

Pauses are stored in `~/.hermes/orquehelx/pausas.db`: if you close the tab or shut down, the pause comes back
with its conversation when you reopen OrqueHelx. Each pause is resolved once, even with two tabs open.

## Dashboard tab

```bash
hermes dashboard
```

Open the **OrqueHelx** tab (`/orquehelx`). It fills the window: each route with its measured quota on the left,
the conversation in the middle, subagents on the right. The tab's interface is in Spanish.

## Checking usage

In a Hermes session:

```text
/ohx rutas     configured routes, with provider and model
/ohx cuota     measured usage per route: windows, percentage and reset time
```

`/ohx cuota` measures when you ask. If a provider does not report usage (Antigravity and OpenCode today), it
shows "sin dato" (no data) instead of an estimate.

## Usage

```bash
hermes chat -t orquehelx -q "Ask agy to review this diff and opencode to write the tests"
```

## Privacy

OrqueHelx has no server of its own, no telemetry, and stores no credentials. Everything runs on your machine
inside Hermes:
- Models are called by Hermes with each subscription's own session.
- To measure quota, OrqueHelx calls Hermes' account-usage fetchers (Anthropic and OpenAI Codex, each with its
  own credential): on `/ohx cuota`, when the dashboard tab loads, and after a generic Claude-subscription error.
- The UI only talks to the local dashboard.
- The only file OrqueHelx writes is `~/.hermes/orquehelx/pausas.db` (quota pauses).

## Tests

```bash
pytest tests
```

They run against a real Hermes installed in the same environment.

## License

MIT. See [LICENSE](LICENSE).

---

## En español

Plugin para Hermes Agent que permite que un modelo delegue trabajo a otro, y que cada uno corra con **tu propia
suscripción**: Claude, Codex, Antigravity, OpenCode o cualquier CLI que hable ACP. El modelo elige la ruta en
cada llamada y el subagente corre en ese proveedor y gasta su cuota. Si esa cuota se agota, el cambio a otro
proveedor lo decides tú: OrqueHelx nunca lo hace automático.

- **Instalación:** `hermes plugins install TicoraX/OrqueHelx#orquehelx --enable`.
- **Rutas:** en `~/.hermes/config.yaml`, bajo `plugins.entries.orquehelx.settings.rutas`, como en el ejemplo de
  arriba. `provider` es un proveedor que Hermes ya conoce; `acp`, el comando de un CLI que hable ACP. Sin rutas
  el plugin carga igual, y `delegar` y `/ohx` te dicen qué configurar. Una ruta mal escrita falla al cargar,
  con el nombre de la ruta.
- **Sin cuota:** `delegar` devuelve `estado: sin_cuota` con la hora de reinicio (o `null`, nunca estimada) y las
  otras rutas. `politica` decide qué hace el padre: `preguntar` (por defecto), `padre_decide` o `esperar`. La
  cuota se da por agotada solo con un error inequívoco o una ventana medida al 100 %.
- **Agente principal sin cuota:** en la pestaña OrqueHelx la conversación queda en pausa, con **Reanudar**,
  **Reenviar** a otra ruta o **Cancelar**. La pausa se guarda en `~/.hermes/orquehelx/pausas.db`.
- **Consumo:** `/ohx rutas` y `/ohx cuota`. Lo que un proveedor no informa se muestra como "sin dato".
- **Interfaz:** `hermes dashboard`, pestaña **OrqueHelx** (`/orquehelx`): rutas con su cuota, conversación y
  subagentes en vivo.
- **Windows y Antigravity:** requiere hermes-antigravity-subscription 1.0.2 o posterior.
- **Privacidad:** sin servidor propio, sin telemetría y sin credenciales guardadas. La cuota se mide con los
  medidores de Hermes; lo único que escribe OrqueHelx es `pausas.db`.
