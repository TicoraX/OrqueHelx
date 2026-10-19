# OrqueHelx

[Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that lets the model send each subagent to
the subscription you choose: Claude, Codex, Antigravity or any provider Hermes knows, each on its own quota.

Hermes' `delegate_task` sends every child to the one target set in `config.yaml`. With OrqueHelx the model picks
a route on each call, and the child runs on that provider. When a subscription runs out of quota, the parent is
told so plainly and you decide what happens next: OrqueHelx never switches providers on its own.

**Status:** v0.3.0, small on purpose and in maintenance mode (see [Scope](#scope)). Español: [más abajo](#en-español).

## Requirements

- Hermes Agent 0.22.0 or later.
- The subscriptions you want to use, set up as Hermes providers and signed in (for example the
  `claude-subscription-directsdk` and `antigravity-subscription-directsdk` plugins from the catalog). OrqueHelx
  stores no credentials.

Using a consumer subscription from another tool may be against that provider's terms (Google, for one, forbids
it for Antigravity). Check them before you add a route.

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
          claude: {provider: claude-subscription-directsdk-experimental, model: claude-haiku-4-5}
          codex:  {provider: openai-codex, model: gpt-5.5}
          agy:    {provider: antigravity-subscription-directsdk, model: flash}
```

`provider` is any provider Hermes knows. `model` is required: without it Hermes would run the subagent with
the parent's model on the route's provider. For a CLI that speaks ACP (OpenCode, Kiro, Junie),
install its provider plugin from the catalog and use that provider's name.

With no routes configured the plugin still loads, `delegate_to` and `/ohx` tell you what to set, and the native
`delegate_task` keeps working. A malformed route fails at load time, and Hermes shows the error naming the route.

## When a subscription runs out of quota

If a subagent fails because its provider ran out of quota, `delegate_to` returns `status: out_of_quota` to the
parent, with the reset time (`reset_at`, or `null` when the provider does not report it: it is never estimated)
and the other routes. What the parent does next is set by `policy`:

```yaml
    orquehelx:
      settings:
        policy: ask     # ask (default) | parent_decides | wait
```

- `ask`: the parent tells you and asks whether to send the task to another route, naming each route's model.
  It cannot delegate again in that turn until you answer. Subagents already launched in parallel in the same
  turn still finish.
- `parent_decides`: the parent may pick another route if its model fits the task, and tells you which one.
- `wait`: the parent tells you and does nothing else until your next message.

Quota counts as exhausted in two cases: the error names it (Antigravity's `RESOURCE_EXHAUSTED`, for example), or
a measured window is at 100 % (Claude, Codex). Any other failure is reported as a failure, not as quota.

## Checking usage

```text
/ohx routes    configured routes, with provider and model
/ohx quota     measured usage per route: windows, percentage and reset time
```

Usage comes from Hermes' own `fetch_account_usage` (Claude and Codex today); a reading less than 30 seconds old
is reused. A provider that does not report usage shows "no data", never an estimate.

## Scope

v0.3.0 keeps only what Hermes does not already do: choosing the provider per call and the honest out-of-quota
policy. Earlier versions also had a dashboard tab (chat, subagent tree, quota cells and a quota pause) and
generic ACP routes. Hermes and its catalog now cover most of that: Desktop and the TUI show the subagent tree
and usage per provider, `/usage` measures quota, and ACP CLIs have their own provider plugins. Keeping a second
copy cost a rewrite on every Hermes release, so it went. The plugin now uses only public Hermes surfaces
(`ctx.subagent_lifecycle`, hooks, commands and `agent.account_usage`).

## Upgrading from v0.2

- Hermes 0.22.0 or later is required.
- The dashboard tab and its quota pauses are gone. You can delete `~/.hermes/plugin-data/orquehelx/`.
- `acp` routes are gone: install the CLI's provider plugin and use `provider:` instead.
- Every route needs a `model`. A route without one fails at load time, naming the route.
- Messages are English only. `routes`, `policy` and the v0.1 names (`rutas`, `politica`, `preguntar`,
  `padre_decide`, `esperar`) still load.

## Privacy

OrqueHelx has no server of its own, no telemetry, and stores no credentials or files. Subagents run inside
Hermes with each provider's own session, and usage is read through Hermes' fetchers, each with its own
credential.

## Tests

```bash
pytest tests
```

They run against a real Hermes installed in the same environment.

## License

MIT. See [LICENSE](LICENSE).

---

## En español

Plugin para Hermes Agent: el modelo manda cada subagente a la suscripción que elijas (Claude, Codex, Antigravity
o cualquier proveedor que Hermes conozca), cada uno con su cuota. Si una se agota, el padre se entera sin
rodeos y tú decides qué sigue; OrqueHelx no cambia de proveedor por su cuenta.

Requiere Hermes 0.22.0 o posterior y se instala con
`hermes plugins install TicoraX/OrqueHelx#orquehelx --enable`. Las rutas van en `~/.hermes/config.yaml`, bajo
`plugins.entries.orquehelx.settings.routes`, como en el ejemplo de arriba; cada ruta lleva `provider` y `model`.
`policy` decide qué hace el padre ante una cuota agotada: `ask` (por defecto), `parent_decides` o `wait`.
`/ohx routes` y `/ohx quota` muestran las rutas y el consumo medido.

La v0.3.0 se recortó a propósito: la pestaña del dashboard y las rutas ACP salieron porque Hermes y su catálogo
ya cubren eso. Los mensajes del plugin están solo en inglés. Usar una suscripción de consumo desde otra
herramienta puede ir contra los términos del proveedor; revísalos antes de agregar una ruta.
