# OrqueHelx

[Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that lets one model delegate work to
another, each running on your own subscription: Claude, Codex, Antigravity, OpenCode or any CLI that speaks ACP.

Hermes' `delegate_task` sends every child to the one target set in `config.yaml`. With OrqueHelx the model picks
a route on each call, and the subagent runs on that provider and spends that quota. If a subscription runs out,
you decide where the task goes next; the plugin doesn't switch providers for you.

**Status:** v0.2.0 (not released yet; the latest release is v0.1.4). `delegate_to`, `/ohx routes|quota` and the
OrqueHelx dashboard tab work: routes with their measured quota, a chat with the main agent, a live subagent tree,
and a quota pause until the reset. Two cases
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
        routes:
          claude:   {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
          agy:      {provider: antigravity-subscription-directsdk, model: flash}
          opencode: {acp: [opencode, acp]}
```

- `provider`: any provider Hermes already knows, including the subscription plugins in the catalog
  (`hermes plugins install claude-subscription-directsdk`).
- `acp`: the command of any CLI that speaks ACP. OrqueHelx registers it as a provider, no extra plugin needed.

With no routes configured, the plugin still loads: `delegate_to` and `/ohx` tell you what to set, and the native
`delegate_task` keeps working. A malformed route fails at load time, and Hermes shows the error naming the route.

## When a subscription runs out of quota

If a subagent fails because its provider ran out of quota, `delegate_to` returns `status: out_of_quota` to the
parent model, with the reset time (`reset_at`, or `null` when the provider does not report it: it is never
estimated) and the other available routes. What the parent does next is set by `policy`:

```yaml
    orquehelx:
      settings:
        policy: ask     # ask (default) | parent_decides | wait
```

- `ask`: the parent tells you and asks whether to send the task to another route, naming each route's model.
- `parent_decides`: the parent may pick another route if its model fits the task, and tells you which one.
- `wait`: the parent tells you and does nothing else until your next message.

Quota counts as exhausted in two cases: the error names it (Antigravity's `RESOURCE_EXHAUSTED`, for example),
or a measured window is at 100 % (Claude, Codex). Any other failure is reported as a failure, not as quota.

### When the main agent runs out

In the OrqueHelx tab the conversation pauses: the turn is kept and the model is not switched for you. The pause
card shows the provider, the model and the reset time, with three ways out:

- **Resume**: the same turn again on the same model. If the provider reports a reset time, it resumes on its
  own at that time while the tab is open.
- **Resend**: the same turn on another route you pick (routes with `model`, on another provider). The
  conversation continues on that model.
- **Cancel**: leaves the turn unanswered and frees the chat.

Pauses are stored in `~/.hermes/plugin-data/orquehelx/pauses.db`: if you close the tab or shut down, the pause comes back
with its conversation when you reopen OrqueHelx. Each pause is resolved once, even with two tabs open.

## Dashboard tab

```bash
hermes dashboard
```

Open the **OrqueHelx** tab (`/orquehelx`). It fills the window: each route with its measured quota on the left,
the conversation in the middle, subagents on the right. The tab follows the dashboard's language picker:
English, or Spanish when the dashboard is in Spanish.

## Checking usage

In a Hermes session:

```text
/ohx routes    configured routes, with provider and model
/ohx quota     measured usage per route: windows, percentage and reset time
```

`/ohx quota` measures when you ask. If a provider does not report usage (Antigravity and OpenCode today), it
shows "no data" instead of an estimate. Its output, and config errors, follow Hermes' `display.language`
(English, or Spanish).

## Usage

```bash
hermes chat -t orquehelx -q "Ask agy to review this diff and opencode to write the tests"
```

## Privacy

OrqueHelx has no server of its own, no telemetry, and stores no credentials. Everything runs on your machine
inside Hermes:
- Models are called by Hermes with each subscription's own session.
- To measure quota, OrqueHelx calls Hermes' account-usage fetchers (Anthropic and OpenAI Codex, each with its
  own credential): on `/ohx quota`, when the dashboard tab loads, and after a generic Claude-subscription error.
- The UI only talks to the local dashboard.
- The only file OrqueHelx writes is `~/.hermes/plugin-data/orquehelx/pauses.db` (quota pauses).

## Upgrading from v0.1

- The tool is now `delegate_to(route, goal, context)` (it was `delegar(ruta, objetivo, contexto)`). Prompts or
  skills that name `delegar` need the new name. In a session saved with v0.1, the model may still try
  `delegar` from its history: Hermes answers that the tool does not exist, and `delegate_to` is in its tool list.
- Config keys are `routes` and `policy: ask | parent_decides | wait`. The v0.1 names (`rutas`, `politica`,
  `preguntar | padre_decide | esperar`) still load; setting both the old and the new key is an error.
- `/ohx routes|quota` (the v0.1 `rutas|cuota` still work).
- Pending pauses in `~/.hermes/orquehelx/pausas.db` are copied to the new location the first time the tab opens;
  the old file is left untouched.

## Tests

```bash
pytest tests
```

They run against a real Hermes installed in the same environment.

## License

MIT. See [LICENSE](LICENSE).

---

## En español

Plugin para Hermes Agent: un modelo le pasa trabajo a otro, y cada uno corre con tu propia suscripción
(Claude, Codex, Antigravity, OpenCode o cualquier CLI que hable ACP). El modelo elige la ruta en cada llamada,
y el subagente gasta la cuota de ese proveedor. Si se agota, tú decides a dónde va la tarea. OrqueHelx no
cambia de proveedor por su cuenta.

Se instala con `hermes plugins install TicoraX/OrqueHelx#orquehelx --enable`. Las rutas van en
`~/.hermes/config.yaml`, bajo `plugins.entries.orquehelx.settings.routes`, como en el ejemplo de arriba:
`provider` es un proveedor que Hermes ya conoce y `acp` es el comando de un CLI que hable ACP. Sin rutas, el
plugin carga igual y `delegate_to` y `/ohx` te dicen qué falta. Una ruta mal escrita falla al cargar y el error
la nombra.

Cuando un subagente se queda sin cuota, `delegate_to` responde `status: out_of_quota` con la hora de reinicio
(`reset_at`, o `null` si el proveedor no la informa; nunca se estima) y las otras rutas. Qué hace el padre lo
fija `policy`: `ask` (por defecto), `parent_decides` o `wait`. Solo cuenta como falta de cuota un error que lo
dice o una ventana medida al 100 %.

Si se agota el agente principal, la conversación queda en pausa en la pestaña OrqueHelx, con tres botones:
Reanudar, Reenviar a otra ruta o Cancelar. La pausa vive en `~/.hermes/plugin-data/orquehelx/pauses.db` y
sobrevive a cerrar la pestaña.

`/ohx routes` lista las rutas y `/ohx quota` mide el consumo; lo que un proveedor no informa sale como
"sin dato". La pestaña (`hermes dashboard`, `/orquehelx`) sigue el idioma del selector del dashboard, y `/ohx`
y los errores de config siguen `display.language` de Hermes. Lo que lee el modelo va siempre en inglés.

Si vienes de la v0.1: la herramienta ahora se llama `delegate_to` (antes `delegar`). Tu config con `rutas`,
`politica` y valores en español sigue funcionando, y las pausas de `pausas.db` se copian solas la primera vez.

En Windows, la ruta de Antigravity necesita hermes-antigravity-subscription 1.0.2 o posterior. OrqueHelx no
tiene servidor propio ni telemetría y no guarda credenciales; el único archivo que escribe es `pauses.db`.
