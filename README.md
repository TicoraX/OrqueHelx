# OrqueHelx

Plugin para [Hermes Agent](https://github.com/NousResearch/hermes-agent) que permite que un modelo delegue
trabajo a otro, y que cada uno corra con **tu propia suscripción**: Claude, Codex, Antigravity, OpenCode o
cualquier CLI que hable ACP.

El modelo elige la ruta, y el subagente corre en ese proveedor y gasta su cuota. Si esa cuota se agota, el
cambio a otro proveedor lo decides tú: OrqueHelx nunca lo hace automático.

**Estado:** en desarrollo. Ya funcionan `delegar`, el aviso de cuota agotada en subagentes y la primera
versión de la interfaz (rutas con su cuota medida). El chat en la interfaz, el árbol de subagentes en vivo y
la pausa del agente principal hasta el reinicio vienen después.

## Requisitos

- Hermes Agent 0.21.5 o posterior.
- Los CLIs que quieras usar, instalados y con sesión iniciada (`claude`, `agy`, `opencode`, ...).
  OrqueHelx no guarda credenciales: cada CLI usa la suya.

## Instalación

```bash
hermes plugins install TicoraX/OrqueHelx#orquehelx --enable
```

## Interfaz

OrqueHelx se abre dentro del dashboard de Hermes:

```bash
hermes dashboard
```

Entra a la pestaña **OrqueHelx** (`/orquehelx`). Ocupa toda la ventana: a la izquierda cada ruta con su cuota
medida, al centro la conversación y a la derecha los subagentes.

## Rutas

Una ruta es el nombre con que el modelo elige dónde corre el subagente. Van en `~/.hermes/config.yaml`:

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

- `provider`: un proveedor que Hermes ya conoce, incluidos los plugins de suscripción del catálogo
  (`hermes plugins install claude-subscription-directsdk`).
- `acp`: el comando de cualquier CLI que hable ACP. OrqueHelx lo registra como proveedor, sin plugin aparte.

Si una ruta está mal escrita, Hermes muestra el error nombrando la ruta al cargar el plugin.

## Cuando una suscripción se queda sin cuota

Si un subagente falla porque su proveedor agotó la cuota, `delegar` le devuelve al modelo padre
`estado: sin_cuota`, la hora de reinicio (o `null` si el proveedor no la informa: nunca se estima) y las
otras rutas disponibles. Lo que hace el padre lo decide `politica`:

```yaml
    orquehelx:
      settings:
        politica: preguntar     # preguntar (por defecto) | padre_decide | esperar
```

- `preguntar`: el padre te avisa y te pregunta si pasar la tarea a otra ruta, nombrando el modelo de cada una.
- `padre_decide`: el padre puede elegir otra ruta si su modelo sirve para la tarea, y te dice cuál eligió.
- `esperar`: el padre te avisa y no hace nada más hasta tu próxima indicación.

La cuota se da por agotada solo si el error lo dice sin ambigüedad (por ejemplo `RESOURCE_EXHAUSTED` de
Antigravity) o si está medida (una ventana de Claude o Codex al 100 %). Un fallo cualquiera no se reporta
como falta de cuota.

### Si se agota el agente principal

En la pestaña OrqueHelx, la conversación queda en pausa: el turno no se pierde y no se cambia de modelo por
tu cuenta. La tarjeta de pausa muestra el proveedor, el modelo y la hora de reinicio, con tres salidas:

- **Reanudar**: el mismo turno otra vez con el mismo modelo. Si el proveedor informa la hora de reinicio,
  se reanuda solo a esa hora mientras la pestaña esté abierta.
- **Reenviar**: el mismo turno en otra ruta que elijas (solo rutas con `model`, de otro proveedor). La
  conversación sigue con ese modelo.
- **Cancelar**: deja el turno sin responder y libera el chat.

La pausa se guarda en `~/.hermes/orquehelx/pausas.db`: si cierras la pestaña o apagas el equipo, vuelve
con su conversación al abrir OrqueHelx. Cada pausa se resuelve una sola vez, aunque tengas dos pestañas
abiertas.

## Ver el consumo

En una sesión de Hermes:

```text
/ohx rutas     rutas configuradas, con su proveedor y modelo
/ohx cuota     consumo medido de cada ruta: ventanas, porcentaje y hora de reinicio
```

`/ohx cuota` mide al momento de pedirlo. Si un proveedor no informa consumo (hoy Antigravity y
OpenCode), muestra "sin dato" en vez de un número estimado.

## Uso

```bash
hermes chat -t orquehelx -q "Pídele a agy que revise este diff y a opencode que escriba los tests"
```

## Privacidad

OrqueHelx no tiene servidor propio ni telemetría y no guarda credenciales. Todo corre en tu máquina dentro de
Hermes: los modelos y la medición de cuota los llama Hermes con la sesión de cada suscripción, y la UI habla
solo con el dashboard local. Lo único que OrqueHelx escribe es `~/.hermes/orquehelx/pausas.db` (las pausas por
cuota).

## Tests

```bash
pytest tests
```

Corren contra un Hermes real instalado en el mismo entorno.

## Licencia

MIT. Ver [LICENSE](LICENSE).
