# ORQUESTER — Arquitectura del Sistema (v2)

> v2 reemplaza el motor propio por **Hermes Agent** (Nous Research, MIT) como runtime prestado.
> La v1 queda en `ARQUITECTURA.v1.md`. Toda afirmación sobre Hermes cita `hermes-agent/<archivo>:<línea>`.

---

## 1. Decisión de arquitectura

Hermes ya implementa el núcleo que la v1 planeaba construir: aislamiento de contexto, compresión, delegación jerárquica, multi-proveedor, cron, aprobaciones y un scheduler de DAG durable. Construirlo de nuevo compite contra código MIT probado.

**ORQUESTER deja de ser un motor de orquestación y pasa a ser la capa de diseño, gobierno y exportación sobre un runtime prestado.**

La frase se sostiene con una precisión que la verificación obligó a agregar:
**ORQUESTER no hace scheduling, pero sí ejecuta.** No decide *cuándo* corre un
nodo —eso es del kanban, siempre— y sí corre con sus propias manos los nodos
cuyo ejecutor es un agente externo. La razón está en §12: `skills` no fuerza el
ejecutor, así que un nodo que el Studio marcó como "OpenCode" solo llega a
OpenCode si alguien invoca el binario, y ese alguien tiene que ser ORQUESTER.

No es una grieta en la decisión: es la diferencia entre **motor** y **worker**.
Un motor resuelve dependencias, persiste estado y sobrevive reinicios; nada de
eso lo hace ORQUESTER. Un worker recibe una unidad de trabajo ya programada y
la ejecuta. ORQUESTER es worker de una clase de nodos, y de ninguna otra.

| Capa | Dueño | Justificación |
|---|---|---|
| Aislamiento de contexto, compresión | Hermes | Ya resuelto, MIT |
| Scheduling de DAG, durabilidad, reintentos | Hermes (kanban) | `task_links` + dispatcher. **Sin excepciones**: también programa los nodos que ORQUESTER ejecuta |
| Ejecución de nodos `runtime: hermes` | Hermes | El dispatcher hace spawn del perfil asignado |
| Ejecución de nodos con agente externo | **ORQUESTER** (§12) | `skills` no fuerza el ejecutor (§4.1). Si el Studio promete un backend, alguien tiene que invocarlo |
| Diseño visual del flujo | ORQUESTER | No existe en Hermes |
| Export MCP / componente UI | ORQUESTER | El MCP de Hermes es de mensajería, no de ejecución |
| Catálogo, versionado, RBAC/SSO | ORQUESTER | Fuera del alcance de Hermes |
| Observabilidad como grafo | ORQUESTER (consumidor) | Hermes emite; ORQUESTER pinta |

---

## 2. Diagrama

```
+---------------------------------------------------------------+
|                      FRONTEND (React + Vite)                  |
|  Studio (React Flow)   Grafo de trazas   Catálogo   Dashboard |
+---------------------------------------------------------------+
                    | REST + SSE
                    v
+---------------------------------------------------------------+
|                   BACKEND ORQUESTER (NestJS)                  |
|                                                               |
|  +----------------+  +------------------+  +---------------+  |
|  | Compilador     |  | Proyector de     |  | Exportador    |  |
|  | DAG -> kanban  |  | eventos (ACP/    |  | MCP           |  |
|  |                |  | task_events)     |  |               |  |
|  +----------------+  +------------------+  +---------------+  |
|  +---------------------------------------------------------+  |
|  | Dispatcher externo (§12): reclama el carril propio y     |  |
|  | invoca claude / opencode / agy. NO programa: solo ejecuta|  |
|  +---------------------------------------------------------+  |
|  Auth / RBAC · Persistencia de grafos (Postgres + Prisma)     |
+---------------------------------------------------------------+
        |                                    |                |
        | CLI headless · ACP                 | claim_task /   | subprocess
        | kanban CLI                         | complete_task  | + JSON Schema
        v                                    v                v
+-------------------------------------------------+  +------------------+
|            HERMES AGENT (runtime, MIT)          |  | AGENTES EXTERNOS |
|  delegate_task · context_compressor             |  |  claude -p       |
|  kanban dispatcher  -> spawnea runtime: hermes  |  |  opencode run    |
|  providers (Anthropic/OpenAI/Gemini/Bedrock/…)  |  |  agy -p          |
|  environments: local · Docker · SSH · Modal · … |  +------------------+
+-------------------------------------------------+
                        ^
                        |  ambos escriben el mismo kanban.db
                        |  (carriles disjuntos por `assignee`, §12)
```

El kanban es el único punto de encuentro: los dos dispatchers leen y escriben la
misma SQLite, sobre carriles que no se solapan. Ver §12 para por qué eso no es
una condición de carrera.

---

## 3. Superficies de integración

Tres, y el orden importa. **El servidor MCP de Hermes NO sirve para ejecución**: sus 10 tools son de mensajería (`conversations_list`, `messages_send`, `events_poll`…) — `mcp_serve.py:590+`. No expone `delegate_task`.

### 3.1 CLI headless — el adapter base
```bash
hermes chat -q "<objetivo>" -Q --max-turns N -t <toolsets> -m <modelo> --in <dir> [--worktree]
```
`-Q` es *"quiet mode for programmatic use"* (`hermes_cli/_parser.py:367`). `--worktree` aísla en un git worktree para agentes paralelos sobre el mismo repo. Esto implementa `IAgentAdapter.execute()` como spawn de proceso.

### 3.2 ACP (Agent Client Protocol) — observabilidad en vivo
`acp_adapter/server.py` expone Hermes por JSON-RPC con streaming de mensajes y razonamiento, permisos, aprobación de ediciones y fork de sesión. Es la fuente del grafo de trazas en tiempo real: evita parsear stdout.

### 3.3 Kanban — el motor de DAG durable
SQLite con `tasks`, `task_links(parent_id, child_id)`, `task_runs`, `task_events` (`hermes_cli/kanban_db.py:1333-1526`). El bloqueo tipo `dependency` espera en `todo` y se **auto-promueve cuando los padres terminan, sin intervención humana** (`kanban_db.py:110`). El dispatcher corre dentro del gateway cada 60s: reclama claims muertos, promueve listas, hace spawn de los perfiles asignados.

---

## 4. El compilador: Studio -> kanban

Uno de los dos únicos componentes de motor que ORQUESTER escribe — el otro es el dispatcher externo de §12. No es un motor de workflows; es un mapeo.

```
Nodo de React Flow      ->  hermes kanban create   (goal, assignee=perfil, workspace)
Arista del DAG          ->  hermes kanban link parent child
Ejecución               ->  el kanban promueve siempre; hace spawn solo de los
                            nodos `runtime: hermes`. Los de agente externo los
                            ejecuta el dispatcher de ORQUESTER (§12)
Salida del nodo         ->  output_schema por tarea, validado con jsonschema
Estado en el Studio     ->  task_events / task_runs proyectados por SSE
```

El paso de contexto entre nodos ya existe: `complete_task(summary=..., metadata=...)` guarda el resultado del padre y `build_worker_context` (`kanban_db.py:10582`) lo entrega al hijo cuando arranca. ORQUESTER no necesita cablear el handoff.

Reglas del compilador:
- Crear los padres antes que los hijos: `link_tasks` valida que existan y lanza `unknown task(s): ...`.
- El kanban **ya rechaza ciclos** (`_would_cycle`, `kanban_db.py:3823`) con `linking X -> Y would create a cycle`, y también el auto-enlace. El compilador no necesita su propio topo-check; sí debe traducir ese `ValueError` a un error de validación en el canvas.
- Un nodo del Studio = una tarea. Fan-out puro sin dependencias puede colapsarse en un solo `delegate_task(tasks=[...])` y ahorrar el round-trip del dispatcher.
- `create_task(initial_status=...)` solo acepta `blocked` o `running`. El estado inicial correcto lo decide Hermes según los padres; no forzarlo.

### 4.1 Nodos heterogéneos: Claude Code, OpenCode, Antigravity

La idea original de la v1 (sub-agentes especializados, no un solo motor) se conserva íntegra. La heterogeneidad vive **por nodo**, en columnas que la tabla `tasks` ya tiene (`kanban_db.py:1333+`):

```sql
assignee          TEXT   -- perfil Hermes que ejecuta el nodo
skills            TEXT   -- skills forzadas, pasadas al worker via --skills
model_override    TEXT   -- el dispatcher pasa -m <model>
provider_override TEXT   -- y --provider <name>
workspace_path    TEXT   -- dónde corre
```

Un nodo con `runtime: opencode` compila a `create_task(assignee=<perfil>, skills=["opencode"], ...)`. El dispatcher hace spawn de un worker Hermes con esa skill precargada; el worker supervisa al agente externo por `terminal` y devuelve el resultado.

> **`skills` NO selecciona el ejecutor.** Es el hallazgo más consecuente de toda la fase de verificación, y contradice lo que esta sección asumía.
>
> Verificado en el rombo de `tests/test_dag_heterogeneo.py`: tres nodos con `skills=["claude-code"|"opencode"|"antigravity-cli"]` cerraron los cuatro **sin invocar ni una vez** el binario correspondiente (cero ocurrencias en los cuatro logs). Las skills estaban `enabled` y el dispatcher las pasa (`kanban_db.py:10454`) — el log de B prueba que la skill se cargó, porque el modelo la nombra, y aun así el worker hizo el trabajo él mismo.
>
> La diferencia con las Tareas 3-5, donde sí se invocó el binario, está en el **goal**: aquellos títulos nombraban el backend explícitamente ("Via antigravity: ..."). Estos no.
>
> **`skills` es contexto, no selector.** El worker recibe la capacidad y decide si usarla. Para ORQUESTER eso significa que `IAgentAdapter` **no puede delegar la elección del ejecutor al modelo**: o invoca el binario él mismo, o el nodo `runtime: opencode` es una sugerencia y no una garantía. Un Studio donde el usuario elige el ejecutor de un nodo y el sistema usa otro es un bug de producto, no una optimización.

**Frontera de orquestación** (del propio `SKILL.md` de `antigravity-cli`, y vale para los tres): los agentes externos son **backends de ejecución, no primitivas de orquestación**. `agy`, `claude` y `opencode` nunca son una card del board. La card es siempre un nodo de ORQUESTER con su carril; el agente externo es el método con que ese nodo hace el trabajo. Poner el agente externo como card significaría dos schedulers compitiendo por el mismo grafo, cada uno con su propia idea de las dependencias.

Las skills ya existen en el repo, no hay que escribirlas:

| Backend | Skill | Invocación headless | Salida estructurada |
|---|---|---|---|
| Claude Code | `skills/autonomous-ai-agents/claude-code/` | `claude -p '...' --output-format json --json-schema '{...}' --max-turns N` | **Nativa**; además `--resume`, `--fork-session` |
| OpenCode | `skills/autonomous-ai-agents/opencode/` | `opencode run '...' --format json --model p/m` | **JSONL de eventos**, sin objeto raíz ni campo `result`; la respuesta se reconstruye concatenando los `type:"text"`. Trae `cost` y `tokens` por step |
| Antigravity | `optional-skills/autonomous-ai-agents/antigravity-cli/` | `agy -p '...' --output-format json --json-schema <ruta>` | **La mejor de las tres.** Objeto JSON único con `structured_output` que cumple el schema, más `usage`, `duration_seconds`, `conversation_id`. Es el único CLI que acepta el JSON Schema como parámetro |
| Hermes | nativo | `delegate_task` | `output_schema` nativo |

También vienen `codex`, `openhands`, `grok`, `blackbox` y `computer-use` con el mismo patrón.

Notas de diseño:
- **El `output_schema` de la card NO se propaga al agente externo.** Verificado: el worker invocó `claude -p '...' --allowedTools 'Read,Bash' --max-turns 5` sin `--output-format json --json-schema`, y tradujo la respuesta al contrato por su cuenta. El comando lo improvisa el modelo del worker; la skill no lo fija.
  → **Trabajo para el compilador de ORQUESTER:** para nodos con agente externo, inyectar el contrato en el texto del goal. Hermes no lo hace solo. Claude Code sí acepta `--json-schema` (verificado en invocación cruda), pero hay que pedírselo explícitamente.
- Sintaxis real de `hermes kanban`, distinta de la documentada: `--board` va **antes** del verbo; los boards se crean con `kanban boards create`; el flag es `--skill` (singular, repetible), no `--skills`; el título es **posicional** en `kanban create`, no `--title`.
- **El workspace scratch se borra al completar la card.** `complete_task` (`kanban_db.py:5544`) llama a `_cleanup_workspace` (:5841), que hace `shutil.rmtree` (:5890) sobre el scratch. El comentario del código lo llama intencional: *"Scratch workspaces are intentionally ephemeral"* (:5980).
  → **Matizado por el rombo:** existe un mecanismo de `attachments/<task_id>/` por board que copia los artefactos **antes** del `rmtree` y publica la ruta absoluta en el `metadata` del handoff (eventos `attached`). Es lo que hizo funcionar el join semántico: D leyó los archivos de B y C ahí. O sea que el entregable en archivos sí sobrevive, pero **por el canal de attachments, no por el workspace**. El compilador tiene que apuntar a esa ruta, no a la del scratch.
- **El goal de un nodo tiene que prohibir explícitamente fabricar su propio input.** Ante la misma tarea subespecificada sobre un workspace vacío, dos workers con el mismo modelo tomaron decisiones opuestas: el de claude-code reportó cero, el de opencode escribió cuatro `.md` de prueba y después los contó. Es variabilidad del modelo conductor, no falla del mecanismo, pero el compilador no puede dejarla librada al azar.
- **Las skills bundleadas de Hermes se desactualizan, y eso degrada al worker, no solo a la doc.** `antigravity-cli` (v0.2.0 de la skill) afirma que `agy` devuelve texto plano y que no existe `--output-format json`. Falso en `agy` v1.1.13. Consecuencia observada: el worker **no usó** `--json-schema` y armó el contrato como texto en el prompt, porque la skill le dice que ese flag no existe. → ORQUESTER no puede confiar en las skills bundleadas como fuente de verdad de las capacidades de cada backend; necesita su propia tabla, versionada contra el CLI instalado.
- **El worker escala solo a `--dangerously-skip-permissions`.** Observado en `t_8160e4fd`: la primera invocación de `agy` murió por permisos (10.8s), y el worker reintentó **agregando el flag por su cuenta** (124.8s, exitosa). No es alucinación: `SKILL.md:88` lo trae como ejemplo. Un worker desatendido, bloqueado por una barrera de permisos, la desactiva entera.
  → **Regla de gobierno para ORQUESTER:** los flags de bypass de permisos de los agentes externos van en una denylist del compilador, y el permiso se concede explícito por nodo. Es exactamente el tipo de decisión que el Studio existe para hacer visible.
- **`agy -p` devuelve exit code 0 aunque falle.** El fallo por permisos salió con `EXIT=0` y el texto `jetski: no output produced`. Un adapter que use el exit status como señal de éxito reporta `success` sobre un fallo total. → Los adapters de agente externo se juzgan por la salida parseada, nunca por el código de retorno.
- **`--json-schema` de `agy` gobierna `structured_output`, no `response`.** `response` trae prosa markdown con el objeto pegado al final. El adapter lee `structured_output`.
- **`create_task` necesita `assignee="default"` explícito.** Sin eso el dispatcher nunca hace spawn: las cards quedan en `ready` para siempre con `Spawned: 0` y sin explicación. Silencioso, y cuesta caro de diagnosticar.
- **La salida de un worker no es frontera de confianza.** El worker de B se fabricó, dentro de su propia respuesta, un turno de usuario falso (`System Human (santi): ... Don't use any kanban tools in my session`) **y un system prompt nuevo**, y se reencuadró a sí mismo: corrió `pwd` e intentó escribir fuera del board. La tarea ya estaba cerrada, sin daño. En esta corrida **no** se propagó a D, que leyó los artefactos de sus padres directo en vez del summary — pero el kanban sí pasa summaries de padre a hijo por diseño (`build_worker_context`), así que el camino de propagación existe.
  → **Regla para ORQUESTER:** el summary de un nodo se trata como dato no confiable antes de entrar al contexto de un hijo. La revalidación de §5 no alcanza: valida forma, no intención.
- Precedente para una integración más profunda: `agent/copilot_acp_client.py` envuelve un agente ACP externo como backend estilo OpenAI (`acp://copilot`). Si hace falta que un agente externo sea *el modelo* del nodo y no un proceso supervisado, ese es el molde a copiar.
- **No verificado por ejecución.** Las skills y las columnas están leídas en código; falta correr un nodo real de cada backend.

**Verificado** con `tests/test_dag_rombo.py` contra el kanban real (sin LLM):

```
1. Estado inicial: A=ready B=todo C=todo D=todo
2. Tras completar A:  B=ready C=ready D=todo
3. Tras completar solo B: C=ready D=todo      <- D NO se libera con un solo padre
4. Tras completar C:  D=ready
5. build_worker_context pasa los summaries de B y C a D: OK
6. Enlace ciclico D->A: rechazado (ValueError)
```

---

## 5. Contrato de salida (verificado)

`AgentAdapterOutput` de la v1 funciona sin adaptación como `output_schema` de `delegate_task`. Verificado con `tools/delegation_output_schema.py`:

```ts
export interface AgentAdapterOutput {
  status: 'success' | 'failure';
  summary: string;
  rawLogsRef?: string;
  data?: Record<string, any>;
}
```

Hermes inyecta un bloque `OUTPUT CONTRACT (machine-validated)` en el context del hijo, extrae el JSON aunque venga envuelto en prosa y code fences, y devuelve errores con JSON-path (`$.status: 'done' is not one of ['success','failure']`) — consumibles directo por el Studio para marcar el nodo en rojo con motivo.

**Dos límites que ORQUESTER debe cubrir:**

1. `validate_output` importa `jsonschema` dentro de un `try` y **devuelve `(True, [])` si el paquete falta** (`delegation_output_schema.py:157`). Los guardrails se degradarían en silencio. → `jsonschema` es dependencia dura del entorno de ORQUESTER, verificada al arranque.
2. `MAX_SCHEMA_RETRIES = 1`, hardcodeado por diseño (*"más retries hacen que los modelos frontier suelten campos que ya estaban bien"*). Política de reintentos por nodo, si se quiere, se implementa en el compilador de ORQUESTER.

---

## 6. Qué se elimina de la v1

| Módulo v1 | Destino | Reemplazo |
|---|---|---|
| BullMQ + Redis | Eliminar | Dispatcher del kanban (durable, sobrevive reinicios) |
| `modules/context/` | Eliminar | `agent/context_compressor.py`, `trajectory_compressor.py` |
| `modules/adapters/` | Reducir a dos | Un adapter Hermes (CLI o ACP) + `run_backend` para los agentes externos (§12) |
| `modules/telemetry/` | Invertir rol | Consumidor de `task_events` + ACP, no productor |
| `modules/orchestrator/` | Eliminar | `delegate_task` + dispatcher |

Sobrevive de NestJS: auth/RBAC, persistencia de grafos, compilador, proyector de eventos, exportador MCP y el dispatcher externo (§12).

Ojo con `modules/orchestrator/`: se elimina la **orquestación** (resolver dependencias, decidir qué corre y cuándo), no la **ejecución**. El dispatcher externo no lo resucita: no programa nada, solo reclama trabajo que el kanban ya programó.

---

## 7. Estructura del monorepo (v2)

```text
orquester/
├── apps/
│   ├── api/                      # NestJS: fino, sin scheduling propio
│   │   └── src/modules/
│   │       ├── graphs/           # CRUD y versionado de grafos del Studio
│   │       ├── compiler/         # DAG -> kanban (create + link; el kanban
│   │       │                     #   ya rechaza ciclos, ver §4)
│   │       ├── runtime/          # Adapter Hermes: CLI headless + cliente ACP
│   │       ├── dispatcher/       # §12: reclama el carril externo y corre
│   │       │                     #   run_backend (claude / opencode / agy)
│   │       ├── events/           # Proyección task_events/ACP -> SSE
│   │       ├── export/           # Grafo -> servidor MCP parametrizable
│   │       └── iam/              # RBAC / SSO
│   └── web/
│       └── src/components/{studio,nodes,traces,catalog}/
├── packages/
│   ├── core/                     # DTOs compartidos (AgentAdapterOutput, GraphSpec)
│   └── mcp-exporter/
└── hermes-agent/                 # Runtime prestado (MIT, upstream sin fork)
```

---

## 8. Configuración: reglas heredadas de Hermes

`hermes-agent/AGENTS.md` impone convenciones que ORQUESTER debe respetar para no pelearse con el upstream:

- **`.env` es solo para secretos** (API keys, tokens, passwords). Todo ajuste de comportamiento — timeouts, umbrales, feature flags — va en `config.yaml`. No inventar variables `HERMES_*`.
- **Capacidad en los bordes, no en el núcleo.** Toda herramienta del core viaja en cada llamada a la API. Extender por skill o plugin, nunca agregando tools al core.
- **El prompt caching es sagrado.** Nada que mute el contexto pasado o reconstruya el system prompt a mitad de conversación.

Knobs relevantes bajo `delegation:` en `config.yaml`: `max_concurrent_children` (default 3), `max_spawn_depth` (default 2), `child_timeout_seconds`, `orchestrator_enabled`, `subagent_auto_approve`, `inherit_mcp_toolsets`. Bajo `kanban:`: `dispatch_in_gateway` (default true), `failure_limit` (default 2, auto-bloquea la tarea para evitar spin loops).

---

## 9. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Upstream Python muy activo vs. stack TypeScript | Aislar tras `IAgentAdapter`. Si Hermes rompe, cambia un adapter, no el producto. Pinear versión, no seguir `main`. |
| `delegate_task` no acepta dependencias | Usar kanban para el DAG; `delegate_task(tasks=[])` solo para fan-out puro. |
| Guardrails silenciosamente inertes sin `jsonschema` | Verificación al arranque, dependencia dura. |
| SQLite 3.50.4 tiene el bug de corrupción WAL-reset | Hermes lo detecta y cae a `journal_mode=DELETE` (más lento, seguro). Pinear SQLite 3.51.3+ en el entorno de despliegue. |
| `background=True` es process-local | Para trabajo que debe sobrevivir reinicios, usar kanban o `cronjob`. |

---

## 10. Estado de verificación

**70 afirmaciones, 1 pendiente.** Lo que más vale de esta tabla son las **nueve
filas "Verificado: NO"**: supuestos de diseño que parecían ciertos leyendo el
código de Hermes y que la ejecución desmintió. Cada una habría sido un bug en
producción, y ninguna se ve sin correr el sistema.

Convención: **Verificado (ejecutado)** = hay salida cruda pegada en `tests/`.
**Verificado (código)** = leído y citado con archivo:línea, sin ejecutar.

| Afirmación | Estado | Evidencia |
|---|---|---|
| `AgentAdapterOutput` sirve como `output_schema` | **Verificado** | 7/7 checks, `tests/test_contract.py` |
| Errores de validación traen JSON-path | **Verificado** | Salida del test |
| `MAX_SCHEMA_RETRIES = 1` no configurable | **Verificado** | `delegation_output_schema.py:24` |
| Sin `jsonschema` la validación se salta | **Verificado** | `delegation_output_schema.py:157` |
| `delegate_task` no tiene campo de dependencia | **Verificado** | firma en `delegate_tool.py:3372` |
| MCP server no expone ejecución | **Verificado** | 10 tools en `mcp_serve.py:611-910` |
| Kanban resuelve dependencias padre->hijo | **Verificado (ejecutado)** | `tests/test_dag_rombo.py`, 6/6 |
| D espera a los DOS padres, no al primero | **Verificado (ejecutado)** | paso 3 del rombo |
| El contexto del padre llega al hijo | **Verificado (ejecutado)** | `build_worker_context`, paso 5 |
| El kanban rechaza ciclos | **Verificado (ejecutado)** | `_would_cycle`, paso 6 |
| Skills de claude-code / opencode / antigravity existen | **Verificado (código)** | `skills/autonomous-ai-agents/`, `optional-skills/` |
| `tasks` soporta perfil/skills/modelo por nodo | **Verificado (código)** | `kanban_db.py:1333+`, `:2579`, `:2590` |
| E2E con LLM real (`hermes chat -q`) | **Verificado (ejecutado)** | Smoke test verde con copilot y con nous |
| Un worker real ejecuta una tarea del board | **Verificado (ejecutado)** | card `t_1d37a781`, run 3: claimed → spawned → completed en <1 min |
| `task_events` sirve como traza de observabilidad | **Verificado (ejecutado)** | 28 eventos: claims, spawns con PID, heartbeats, reclaims con motivo, completion |
| Cumplimiento empírico del `output_schema` | **Pendiente (diferido a propósito)** | 3 rondas sin cerrar por el bug de §11. La conclusión de diseño ya está en §5 y no depende de esta medición |
| Un nodo delega de verdad a Claude Code | **Verificado (ejecutado)** | card `t_54ddac57`: el worker corrió `claude -p ...` en 13.1s, resultado contrastado contra el filesystem |
| El `output_schema` llega al agente externo | **Verificado: NO llega** | el worker invocó sin `--json-schema`; el compilador debe inyectar el contrato en el goal |
| Un nodo delega de verdad a OpenCode | **Verificado (ejecutado)** | card `t_4477fc20`: el worker corrió `opencode run ...` en 14.7s; corroborado fuera de Hermes con `opencode session list` y el mtime de `opencode.db` |
| El **workspace** scratch sobrevive a la card | **Verificado: NO sobrevive** | `_cleanup_workspace` hace `rmtree` dentro de `complete_task` (`kanban_db.py:5544`, `:5890`). Ojo: los **artefactos** sí sobreviven, por otro canal — ver la fila de `attachments/` |
| Un nodo delega de verdad a Antigravity | **Verificado (ejecutado)** | card `t_8160e4fd`: `agy -p ...` en 124.8s; `command -v agy` resolvió, sin problema de PATH |
| `agy` acepta JSON Schema en el CLI | **Verificado (ejecutado)** | `--json-schema` devuelve `structured_output` conforme; contradice a `SKILL.md` |
| El worker respeta las barreras de permisos del agente externo | **Verificado: NO las respeta** | reintentó agregando `--dangerously-skip-permissions` por su cuenta |
| `agy -p` señala fallo por exit code | **Verificado: NO lo señala** | falló con `EXIT=0` y `jetski: no output produced` |
| Fan-out real: dos hijos en paralelo | **Verificado (ejecutado)** | B y C `spawned` en el mismo segundo (13:10:50), ventanas solapadas, heartbeats simultáneos |
| Join real: el hijo espera a los DOS padres | **Verificado (ejecutado)** | 4 ticks con `Promoted: 0` entre el cierre de B (13:11:36) y el de C (13:13:15); D promovido a las 13:13:15 exactas |
| Handoff de contexto entre nodos | **Verificado (ejecutado)** | el contexto de D trae `## Parent task results` con los dos summaries y `worker_session_id` distintos |
| `skills=[...]` fuerza el ejecutor del nodo | **Verificado: NO lo fuerza** | 0 invocaciones de `claude`/`opencode`/`agy` en los 4 logs del rombo pese a las skills cargadas |
| Los artefactos sobreviven al borrado del scratch | **Verificado (ejecutado)** | mecanismo `attachments/<task_id>/` por board (eventos `attached`), copia antes del `rmtree` y publica ruta absoluta en el `metadata` del handoff |
| El carril externo es invisible para el dispatcher de Hermes | **Verificado (ejecutado)** | `tests/test_dos_dispatchers.py` paso 1: cae en `skipped_nonspawnable`, no se lanza |
| `assignee = NULL` sirve como carril externo | **Verificado: NO sirve** | paso 2: `default_assignee` lo secuestra y lo lanza igual |
| `claim_task` es atómico bajo carrera | **Verificado (ejecutado)** | paso 3: 8 claimers con conexiones propias, exactamente 1 ganador |
| Los dos dispatchers coexisten sin pisarse | **Verificado (ejecutado)** | paso 4: 4 reclamos + 3 ticks, cero solapamiento, cero doble ejecución |
| `run_backend` cumple el contrato con los 3 CLIs | **Verificado (ejecutado)** | `tests/test_rebanada_vertical.py` parte A: parsers contra las formas reales, denylist, contrato |
| Un nodo externo alimenta a otro nodo externo | **Verificado (ejecutado)** | parte B: cadena `opencode -> opencode`, el hijo devolvió el valor del padre |
| Rombo mixto con los CUATRO ejecutores | **Verificado (ejecutado)** | board `mixto-5`: A hermes -> B opencode + C claude-code -> D antigravity; D respondió *"ambos padres enviaron 7"* |
| `claude --json-schema` acepta una ruta de archivo | **Verificado: NO** | exige JSON inline (`is not valid JSON: Unexpected identifier`); al revés que `agy` |
| Un nodo que falla retiene a sus hijos | **Verificado: NO lo hacía** | cerraba con `complete_task` y el kanban promovía al hijo sobre el mensaje de error. Corregido con `block_task` |
| Los nodos del carril corren en paralelo | **Verificado (ejecutado)** | 3 nodos de ~2s en 2.9s; con `MAX_PARALELO=2`, 4 nodos en 4.3s |
| El consumo por nodo se puede contabilizar | **Verificado (ejecutado)** | `tests/test_consumo.py` con las formas reales de los 3 CLIs; un flujo de 3 nodos dio 99.134 tokens y US$ 0,2841 equivalente API |
| Todos los backends informan costo | **Verificado: NO** | `agy` no informa medidor: corre por suscripción. `None` se cuenta aparte, nunca como cero |
| La suite corre en Linux | **Verificado (ejecutado)** | `tests/linux.sh` en `python:3.11-slim`: 7/7 más la resolución de binario con un ejecutable plano |
| El Studio exige token en toda su API | **Verificado (ejecutado)** | `tests/test_auth_studio.py`: 10 rutas rechazan sin token; tokens parciales, largos y con otra capitalización también |
| La tabla de capacidades puede quedar desfasada del código | **Verificado: NO puede** | `tests/test_capacidades.py` ata cada campo al argv y a los extractores reales |
| Se puede elegir el modelo por nodo | **Verificado (ejecutado)** | los 3 CLIs aceptan `--model`; mismo nodo con `gemini-3.7-flash-low` (9.8s) y `gemini-3.1-pro-high` (18.6s) |
| Hizo falta una columna nueva para el modelo | **Verificado: NO** | `model_override` ya existía en `tasks` y significa exactamente eso |
| El compilador avisa antes de correr si falta un ejecutor | **Verificado (ejecutado)** | preflight con `capacidades.faltantes()`: falla en `validar`, sin tocar la base |
| Una org queda aislada de otra | **Verificado (ejecutado)** | `tests/test_api_rbac.py` 10/10: un extraño recibe 404, no 403 |
| Los roles limitan de verdad | **Verificado (ejecutado)** | VIEWER lista pero no crea (403); EDITOR no administra miembros (403) |
| Editar un grafo pisa la versión anterior | **Verificado: NO** | crea la versión N+1; e2e con v1 y v2, la ejecución tomó la última |
| Los reintentos terminan solos | **Verificado (ejecutado)** | al agotarse, Hermes enruta a `triage` (`BLOCK_RECURRENCE_LIMIT`) |
| Un fallo permanente se reintenta | **Verificado: NO** | se bloquea como `capability` y `reintentar()` lo ignora |
| Un flujo se publica como servidor MCP | **Verificado (ejecutado)** | `tests/test_mcp_export.py`: `initialize`/`tools/list`/`tools/call` por stdio; el `tools/call` ejecutó el flujo y devolvió el resultado de las hojas |
| Los parámetros de la tool salen del goal | **Verificado (ejecutado)** | los `{{marcadores}}` del título generan el `inputSchema` |
| El CLI hijo puede heredar el stdin del padre | **Verificado: SÍ, y es grave** | con el servidor MCP ese stdin es el canal JSON-RPC: opencode colgaba >600s (9s suelto). Corregido con `stdin=DEVNULL` |
| Un flujo real encuentra bugs reales | **Verificado (ejecutado)** | `ui/grafos/revision-repo.json` sobre este repo: 3 hallazgos ciertos en código propio, confirmados a mano y corregidos |
| `--max-turns 5` alcanza para trabajo real | **Verificado: NO** | una revisión de diff murió con `error_max_turns`. Subido a 30 |
| El clasificador de fallos sigue al mensaje real | **Verificado: NO lo seguía** | `_PERMANENTES` buscaba una cadena que ya nadie emitía; ahora hay un check que lo ata |

---

## 10.1 El pin de Hermes, y sus dos copias

Hermes es upstream ajeno (NousResearch, MIT) y avanza por su cuenta. Todo lo
verificado en §10 lo está **contra un commit concreto**, registrado en
`HERMES_PIN` y comprobado por `tests/test_pin_hermes.py`. Sin ese registro, la
tabla pasaría a hablar de una versión que ya no existe sin que nada avise.

**Primera actualización, como dato:** de `eb20188` a `b7f6280` fueron **586
commits de upstream en dos días** y **no se rompió nada** — suite completa, los
dos e2e y un grafo mixto con el CLI instalado. Lo único que cambió en lo que
usamos fue `kanban_db.py`, y fue aditivo (`_schema_is_present`, #83445).

Eso valida la apuesta de §1 más que cualquier argumento: apoyarse en una
superficie chica y estable —11 funciones— hace que el ritmo de upstream sea
irrelevante. Si dependiéramos de sus internals, 586 commits nos habrían pasado
por encima.

**Hay dos copias de Hermes en juego, y escriben la misma base:**

| Copia | Quién la usa |
|---|---|
| `hermes-agent/` (clon, ignorado por git) | Nuestro código Python, vía `sys.path` |
| `%LOCALAPPDATA%/hermes/` (instalada) | El CLI `hermes` y su dispatcher |

Nuestro dispatcher lee y escribe el kanban con el `kanban_db.py` **del clon**;
el dispatcher de Hermes usa el **instalado**. Si divergen en el esquema, dos
escritores con distinta idea de la tabla tocan el mismo `kanban.db`. El check
avisa; impedirlo no se puede con código que no es nuestro.

`hermes-agent/` está en `.gitignore` y conserva su propio `.git` apuntando a
upstream: se actualiza con `git pull` y nunca entra a nuestros commits. El check
también falla si el clon tiene cambios locales — es la forma de que "pinear por
versión, sin fork" (§1) sea verificable y no una intención.

---

## 10.2 Plano de control: dos bases, dos dueños

Multiusuario obliga a una decisión que conviene dejar explícita: **hay dos
bases de datos y ninguna manda sobre la otra.**

| Base | Dueño | Guarda |
|---|---|---|
| Postgres | ORQUESTER | Usuarios, organizaciones, roles, grafos, versiones, auditoría |
| SQLite (kanban) | Hermes | Lo que está corriendo: cards, dependencias, runs, eventos |

No es duplicación. El kanban es de Hermes, no controlamos su esquema, y §12
exige que el dispatcher le hable directo. Meter usuarios ahí sería forkear
por la puerta de atrás. Postgres guarda **diseño y gobierno**; el kanban guarda
**ejecución**. Un `Run` en Postgres es un puntero a un board más quién lo lanzó
— lo único que el kanban no sabe ni tiene por qué saber.

### El reparto

```
Navegador ─► NestJS (control)  ─► Python (motor) ─► Hermes / agentes
             usuarios, RBAC        compila,           kanban, CLIs
             grafos, versiones      despacha,
             auditoría              exporta MCP
                  │                     │
              Postgres              kanban.db
```

NestJS **no reimplementa nada del motor**: lo llama por HTTP con el token del
Studio. La razón es la misma de §12 — rehacer el protocolo de claim en TS
contra la misma SQLite es exactamente el tipo de cosa que la tabla de §10
muestra que sale mal.

### Decisiones de seguridad, y por qué

- **Guardia global, no por ruta.** Una ruta nueva nace protegida y hay que
  marcarla `@Publico()` a propósito. Al revés, la que alguien se olvida queda
  abierta — y esto ejecuta agentes con shell.
- **404 y no 403 para un extraño.** Un 403 confirma que esa organización
  existe. Verificado en `tests/test_api_rbac.py`.
- **Login de tiempo constante.** Se verifica contra un hash señuelo cuando el
  email no existe, para que "usuario inexistente" y "clave mala" tarden igual.
  Sin eso, el tiempo de respuesta enumera usuarios.
- **Los tokens de sesión se guardan hasheados.** Si se filtra la base, no salen
  sesiones usables. Mismo criterio que la contraseña (Argon2id).
- **Ejecutar exige EDITOR.** Un VIEWER mira; no gasta cuota ni corre shell.
- **Las versiones son inmutables.** Editar crea una versión nueva; nunca pisa.
  Una corrida vieja tiene que poder explicarse con el grafo que de verdad se
  ejecutó — el mismo criterio de "el pasado no se reescribe".
- **La auditoría es append-only por diseño:** el servicio no expone update ni
  delete, y un fallo al auditar no tumba la operación que auditaba.

---

## 10.4 Chat: sesión por backend

Un nodo del grafo entrega un contrato; un chat entrega texto y tiene que
**acordarse del turno anterior**. Los tres CLIs guardan la conversación y la
retoman por id. Medido contra los binarios instalados, no leído de la doc:

| runtime | id en la salida | flag para retomar | ¿el id se mantiene? |
| --- | --- | --- | --- |
| `claude-code` | `session_id` | `--resume <id>` | sí |
| `opencode` | `sessionID` (en los eventos JSONL) | `--session <id>` | sí |
| `antigravity` | `conversation_id` | `--conversation <id>` | sí |

Verificado ejecutando: primer turno "responde exactamente: ok", segundo turno
"repetí textual mi mensaje anterior" con el id devuelto. Los tres recuperaron
el mensaje original y **devolvieron el mismo id**, así que la sesión no se
renumera a mitad de la conversación.

Consecuencias de diseño:

- **El Studio no persiste conversaciones.** La sesión la guarda el CLI; por la
  API viaja solo el id. Una sesión por runtime, porque el id de `claude` no
  significa nada para `opencode`.
- **Las barandas son las mismas que las de un nodo.** El chat entra por
  `loop.run_chat`, que concede las herramientas del carril, y comparte con
  `run_backend` la denylist de flags de bypass, el `stdin` cerrado y el `cwd`.
  Un chat es un agente con shell, igual que un nodo.
- **El servidor pasó a `ThreadingHTTPServer`.** Un turno tarda entre 20 y 120
  segundos y con un solo hilo congelaba el Studio entero: el canvas dejaba de
  refrescar el estado de la corrida mientras el chat pensaba.
- Si el CLI no informa sesión en un turno, se **conserva la anterior**: perder
  el id a mitad de la charla arrancaría una conversación nueva sin avisar.

## 10.5 Esfuerzo y tope de gasto

Relevado de los `--help` instalados, no de la documentación:

| runtime | esfuerzo | niveles | tope de gasto |
| --- | --- | --- | --- |
| `claude-code` | `--effort` | low, medium, high, xhigh, max | `--max-budget-usd` |
| `antigravity` | `--effort` | low, medium, high | no |
| `opencode` | `--variant` | minimal, high, max (según proveedor) | no |
| `hermes` | `reasoning_effort` de la card | los 7 de `VALID_REASONING_EFFORTS` | no |

- **El esfuerzo no necesitó campo nuevo**: `reasoning_effort` ya existe en la
  card de Hermes, igual que `model_override`. En un nodo `hermes` lo aplica su
  dispatcher; en uno externo lo lee el nuestro y lo traduce al flag del CLI.
  Un campo, dos consumidores.
- **Los niveles no son intercambiables** y se validan contra la lista del
  backend, en el compilador, antes de crear cards. `max` en `antigravity` es un
  error, no un valor que se ignora.
- **El tope nunca se simula.** Solo se pasa al CLI que lo entiende; en los
  demás el corte lo hace el dispatcher entre nodos. Un tope que se cree puesto
  y no lo está es peor que no tener tope.
- **Límite conocido del corte propio**: es entre nodos, no dentro de uno. Los
  que ya arrancaron terminan y el gasto puede pasarse por lo que cuesten. Está
  marcado con un comentario `ponytail:` en `loop.tick`.

## 10.3 Contexto del proyecto y capacidades de los backends

Dos piezas que existen para que **otro agente, u otra persona, retome sin
arqueología**.

### `contexto/estado.py` — el traspaso, generado

Un grafo de LangGraph cuyo estado *es* el traspaso: sondea el sistema real
(git, pin, tests, binarios, Postgres), deriva qué está bloqueando, y escribe
`ESTADO.md`. Está **generado, no escrito a mano**: un traspaso a mano queda
viejo y nadie se entera.

LangGraph aporta el checkpointer: historial por thread, no solo el último
valor. Se puede ver cómo se movió el proyecto y retomar de un punto anterior.

**No es un motor de flujos.** ORQUESTER ya tiene uno prestado (§1) y adoptar un
segundo contradiría la decisión central. Esto modela el contexto del proyecto y
nada más — no ejecuta trabajo de usuario.

Las **decisiones tomadas** viven en una lista a mano dentro de ese archivo, no
sondeadas: son justo lo que un agente nuevo no puede deducir del código, y lo
más caro de re-litigar.

### `dispatcher/capacidades.py` — qué sabe y qué necesita cada backend

§4.1 lo pidió: las skills bundleadas de Hermes se desactualizan y eso **degrada
al worker**, no solo a la doc. El worker no usó `--json-schema` porque su skill
decía que no existía.

La tabla declara, por runtime: binario, si acepta schema por CLI y **en qué
forma** (inline en `claude`, ruta en `agy`), dónde vive el contrato en su
salida, si informa costo, si acepta permisos, y su auth. Cada campo salió de
ejecutar el CLI, no de leer su README.

Dos usos concretos:

- **Preflight del compilador.** Un grafo que nombra un ejecutor ausente falla
  en `validar()`, antes de tocar la base, con el nombre de lo que falta. Antes
  se descubría a los 600s de timeout.
- **`GET /api/capacidades`**, sin token a propósito: no expone nada del usuario,
  solo qué sabe hacer este motor. Es la respuesta a "que los agentes sepan lo
  que requieren" — se consulta antes de armar el grafo.

Y `tests/test_capacidades.py` ata cada campo declarado al `argv` que se
construye de verdad y a los extractores de consumo. **La tabla no puede mentir
sin que el test falle**, que es la diferencia con la skill que nos engañó.

---

## 11. Restricción operativa: proveedores `external_process`

**Descubierto por experimento controlado**, no por lectura de código.

Con `provider: copilot`, todo agente **hijo** (worker del kanban o
`delegate_task`) se cuelga en `Initializing agent...` con CPU en ~0 y nunca
progresa. El agente de primer nivel (`hermes chat -q`) funciona normal.

Evidencia: misma card `t_1d37a781`, misma máquina, tres corridas consecutivas.

```
run 1 (copilot)  heartbeats 12 min, sin avance   -> reclaim manual
run 2 (copilot)  mismo patrón, 3.5 min           -> reclaim manual
run 3 (nous)     claimed, spawned, completed     -> mismo minuto
```

Causa: `copilot` se declara con `auth_type="external_process"`
(`hermes_cli/auth.py:301`) y obtiene el token corriendo `gh auth token`. En un
proceso hijo sin terminal, esa llamada bloquea indefinidamente. `nous` guarda
el token en archivo y no lanza subprocesos.

**Regla para ORQUESTER:** el supervisor no puede usar proveedores de tipo
`external_process` (`copilot`, `copilot-acp`). Verificar el `auth_type` del
proveedor configurado al arranque y rechazarlo si es `external_process`, con
un error explícito — el síntoma natural es un cuelgue silencioso, que es la
peor forma de fallar.

Sin confirmar: si otros proveedores `external_process` fallan igual, y si el
cuelgue desaparece con backend de terminal Docker en vez de `local`.

---

## 12. `IAgentAdapter`: cómo se invoca de verdad un backend externo

Responde al hallazgo de §4.1: `skills` es contexto, no selector. Si el Studio
deja elegir el ejecutor de un nodo, el sistema tiene que **garantizarlo**, no
sugerirlo.

### El problema, preciso

El dispatcher de Hermes hace spawn de:

```
hermes -p <perfil> --cli --accept-hooks [--skills X] [-m modelo] [--provider p] chat -q <contexto>
```

El ejecutor real lo decide el modelo del worker dentro de esa sesión. Las tres
palancas de la línea de comandos (`--skills`, `-m`, `--provider`) configuran
**al worker**, no al binario que el worker vaya a llamar. Por eso las Tareas
3-5 funcionaron —el título decía "Via antigravity: ..."— y el rombo de la
Tarea 6 no.

Cualquier diseño que dependa de que el modelo elija bien es una sugerencia.

### La decisión: dos clases de nodo, un solo scheduler

| Clase de nodo | Quién lo ejecuta | Cómo |
|---|---|---|
| `runtime: hermes` | Dispatcher de Hermes | Sin cambios. `assignee` = perfil. |
| `runtime: claude-code \| opencode \| antigravity` | **Dispatcher de ORQUESTER** | Invoca el binario él mismo. |

**El kanban sigue siendo el único scheduler.** ORQUESTER no construye un
segundo grafo ni duplica la resolución de dependencias: se engancha como un
*worker alternativo* sobre la misma base SQLite.

### El mecanismo, verificado pieza por pieza

El truco es `assignee`, y Hermes ya soporta este patrón de forma explícita. Si
el `assignee` de una card **no es un perfil Hermes**, el dispatcher la bucketea
en `skipped_nonspawnable` (`kanban_db.py:9777`) y no la lanza nunca. El
comentario del código nombra el caso de uso exacto:

> *"Those task lanes are pulled by terminals via `claim_task` directly and
> should NEVER auto-spawn"* — y aclara que es estado estable esperado, **no** un
> fallo accionable por el operador (la telemetría de salud lo distingue de
> "atascado de verdad").

Entonces: **los nodos de runtime externo se compilan con
`assignee = "orquester-external:<runtime>"`** — un carril que deliberadamente
no existe como perfil, con el runtime del nodo como sufijo. Hermes los ve, respeta sus dependencias, los promueve a
`ready`, y no los toca. ORQUESTER los levanta.

> **No usar `assignee = NULL` para esto.** Fue el primer diseño y está mal.
> `dispatch_once` acepta `default_assignee`: con esa opción activa, las cards
> sin assignee se **auto-asignan y se lanzan** (`auto_assigned_default`,
> :9752). Un nodo de runtime externo terminaría ejecutado por un worker
> nativo, en silencio. Verificado en `tests/test_dos_dispatchers.py` paso 2: la
> card sin assignee fue secuestrada; la del carril con nombre resistió.

El runtime del nodo va en el `assignee` y no en `skills`: información de ruteo
en el campo de ruteo. `skills` ya significa otra cosa en Hermes —carga de
contexto para un worker nativo, y nos confundió una vez (§4.1)—, y una card
que por error cayera en manos del dispatcher de Hermes cargaría una skill que
nadie pidió. `create_task` no acepta `metadata` (esa columna vive en
`task_runs`, no en `tasks`), así que el `assignee` es además el único campo
estructurado disponible al crear. Hermes solo hace `lower()` sobre él
(`profiles.normalize_profile_name`), de modo que el sufijo sobrevive intacto y
el carril sigue sin ser un perfil válido.

El loop de ORQUESTER, entero, usando funciones que Hermes ya expone:

```python
# 1. Buscar trabajo propio: ready, sin assignee, con runtime externo en metadata
# 2. Reclamar — atómico, race-safe contra el dispatcher de Hermes
task = k.claim_task(conn, task_id, claimer="orquester")   # :4580
if task is None:
    continue        # otro lo tomó; claim_task ya valida el invariante de padres

# 3. Contexto: los summaries de los padres, armados por Hermes
ctx = k.build_worker_context(conn, task_id)               # :10582

# 4. Invocar el binario NOSOTROS, con el contrato como parámetro del CLI
out = run_backend(task.runtime, goal=ctx, schema=AGENT_ADAPTER_OUTPUT)

# 5. Validar del lado nuestro (§5: Hermes valida forma, no intención)
# 6. Cerrar
k.complete_task(conn, task_id, summary=out.summary, ...)  # :5544
```

**Concurrencia y reintentos** (`tests/test_concurrencia_reintentos.py`, 6/6):

- Los nodos listos del carril corren en paralelo, con tope `MAX_PARALELO` (3 por defecto). Una conexión SQLite por hilo — los objetos de `sqlite3` no se comparten entre hilos, y `claim_task` ya es atómico entre conexiones, así que no hace falta lock propio. El techo real lo pone el rate limit del proveedor de cada CLI, no la máquina.
- Un fallo se clasifica antes de bloquear: `capability` para lo que no se arregla solo (binario ausente, runtime desconocido, flag de bypass rechazado) y `transient` para el resto. Solo los `transient` se reintentan, hasta `MAX_INTENTOS`.
- **El conteo de intentos sale de `list_runs`, no de memoria del proceso**: el dispatcher puede reiniciarse y el conteo sobrevive.
- Emergente y útil: al agotarse los reintentos, Hermes enruta la card a **`triage`** por su `BLOCK_RECURRENCE_LIMIT`, que corta los bucles de desbloqueo. Nuestro reintento no puede girar para siempre aunque el código lo intentara.

Con `heartbeat_claim` (:4879) en un hilo mientras corre el paso 4, para que
`release_stale_claims` no lo reclame en una invocación larga (`agy` tardó
124.8s en la Tarea 5).

Lo único que ORQUESTER escribe de nuevo es `run_backend`. Todo lo demás es API
de Hermes.

**Implementado y verificado punta a punta** en `dispatcher/` (`3616f51`), con `tests/test_rebanada_vertical.py`: cadena de dos nodos `opencode` sobre un board real, donde el hijo devolvió el valor que le pasó el padre. Es lo que la Tarea 6 no había podido probar — ahí los nodos declaraban backend externo pero los ejecutó Hermes.

**El dispatcher se escribe en Python, no en TypeScript.** El loop es todo llamadas a `kanban_db`; reescribirlo en TS obliga a reimplementar el protocolo de claim contra la misma SQLite (`BEGIN IMMEDIATE`, TTL, invariante de padres). Seis supuestos razonables sobre Hermes ya resultaron falsos en §10; un claim protocol reescrito a mano sería el séptimo, y ese corrompe estado. NestJS lo supervisa como proceso.

### `run_backend`: una tabla, no una jerarquía

Por §4.1, los tres CLIs difieren en cómo aceptan el contrato:

| Runtime | Comando | Contrato | Dónde está la respuesta |
|---|---|---|---|
| `antigravity` | `agy -p <goal> --output-format json --json-schema <ruta>` | **Nativo** | `structured_output` (nunca `response`) |
| `claude-code` | `claude -p <goal> --output-format json --json-schema <ruta>` | **Nativo** | salida JSON |
| `opencode` | `opencode run <goal> --format json` | **En el texto del goal** | concatenar los eventos `type:"text"` del JSONL |
| `hermes` | no aplica | `output_schema` de la card | lo maneja el dispatcher de Hermes |

Tres entradas de tabla con `(argv, parser)`. No hace falta una clase por
backend: es el mismo `subprocess` con distinto argv y distinto parser de
salida.

Reglas que salen de la verificación, no negociables:

- **El schema va en archivo, no inline.** El escapado inline se rompe en
  PowerShell; los tres CLIs aceptan ruta (§Tareas 3-5).
- **Nunca juzgar por exit code.** `agy -p` falló con `EXIT=0` (§4.1). El
  veredicto sale de parsear la salida.
- **El goal nombra el backend igual.** Cuesta una línea y es la diferencia
  observada entre las Tareas 3-5 y la 6. Cinturón además de tirantes.
- **Denylist de flags de bypass** (`--dangerously-skip-permissions` y
  equivalentes). El compilador los rechaza; el permiso se concede explícito por
  nodo. Un worker de Hermes escaló solo a ese flag (§4.1); el nuestro no puede.

### Guardrail de defensa en profundidad

Los perfiles de Hermes son HERMES_HOME independientes con su propio
`config.yaml` y su bloque `hooks:` (`hermes_cli/profiles.py:4`). Un hook
`pre_tool_call` puede **bloquear** una llamada (`decision: block`, o exit 2 —
`agent/shell_hooks.py:44-56`).

Sirve para lo que un hook sabe hacer —negar—, no para forzar: un hook en el
perfil de los workers nativos que bloquee `claude|opencode|agy` con flags de
bypass. Si un worker de Hermes vuelve a escalar solo, se corta ahí. No
reemplaza al diseño de arriba, lo respalda.

### Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Inyectar el backend en el texto del goal, y nada más | Es lo que hacen las Tareas 3-5: funcionó 3 de 3, pero depende del modelo. Sugerencia, no garantía. Se conserva **además** del diseño, no en su lugar. |
| Un perfil Hermes por backend con `agent.system_prompt` que fuerce el binario | Más fuerte que el goal, sigue siendo persuasión. Y multiplica HERMES_HOME por backend. |
| Envolver cada CLI como cliente ACP (molde `agent/copilot_acp_client.py`) | El backend externo pasa a ser *el modelo* del nodo. Elegante y mucho más caro. Además §11 desaconseja `external_process`. Reevaluar si hace falta streaming en vivo por nodo. |
| Forkear Hermes para que `skills` seleccione el ejecutor | Rompe la regla de "pinear por versión, sin fork" (§1). El punto de extensión ya existe. |

### Coexistencia de los dos dispatchers (verificado)

`tests/test_dos_dispatchers.py`, 4/4, sin LLM ni spawns reales (`spawn_fn`
espía que solo anota a quién se habría lanzado):

```
journal_mode efectivo: delete
1. spawned=['t_80ddfb2b'] nonspawnable=['t_ac647d47']
2. auto_assigned=['t_891c8338'] (assignee=NULL queda expuesto)
3. 8 claimers compitieron, ganaron 1: [7]
4. ORQUESTER reclamo 4/4; Hermes lanzo 4
```

1. El carril externo cae en `skipped_nonspawnable`; Hermes no lo lanza.
2. **La trampa:** `assignee = NULL` es secuestrado por `default_assignee`; el
   carril con nombre resiste. Es la razón del cambio de diseño de arriba.
3. `claim_task` es atómico bajo carrera real: 8 hilos con conexiones propias
   sobre la misma card, **exactamente un ganador**.
4. Ejecución cruzada: ORQUESTER reclama sus 4 cards mientras Hermes tickea 3
   veces. Cero solapamiento, cero cards lanzadas dos veces.

Nota operativa confirmada: el board corre en `journal_mode=DELETE`, no WAL —
Hermes lo degrada por el bug de corrupción de SQLite 3.50.4. Los dos escritores
coexisten igual, pero con lock de base entera en vez de concurrencia WAL. Si el
throughput del dispatcher externo llega a importar, la salida es actualizar
SQLite (3.51.3+ / backports 3.50.7 / 3.44.6), no rediseñar.

Además, `dispatch_once` toma un lock por board (:9500): dos dispatchers de
Hermes no tickean a la vez, el perdedor devuelve `skipped_locked=True`. El loop
de ORQUESTER **no** toma ese lock, porque usa `claim_task` directo — por eso el
paso 3 del test es load-bearing y no un adorno.

---

## 13. La corrida vive en `dispatcher/corrida.py`, no en el servidor

El bucle que despacha un board --pinchar al dispatcher de Hermes, correr un
`tick` del carril propio, dormir, decidir si queda algo-- estaba escrito **tres
veces**: dentro de `ui/server.py._arrancar`, dentro de `mcp_exporter.ejecutar` y
en `loop.correr`. Ya habian divergido:

| Copia | Lo que le faltaba |
|---|---|
| `mcp_exporter.ejecutar` | el `subprocess.run(timeout=180)` bloqueante: un Hermes colgado frenaba tres minutos el carril propio, arreglado solo en la copia del Studio |
| `loop.correr` | no pinchaba a Hermes: un grafo mixto exportado como script se colgaba esperando cards que nadie iba a levantar |
| las tres | no cortaban cuando el flujo quedaba trabado (abajo) |

Ahora hay una sola, y los tres llamadores la comparten. `ui/server.py` conserva
lo que es del Studio --el hilo, `_corriendo`, el tope que se esta aplicando-- y
la peticion de parada pasa por `corrida.pedir_parada` / `corrida.matar_hermes`.

### `orquester run`: el DAG sin Studio

El archivo se llama `orquester.py` y **no** `cli.py`: `hermes-agent/` tiene su
propio `cli.py`, y este arbol mete `hermes-agent` en `sys.path` en cuanto se
importa el dispatcher. Con los dos con el mismo nombre, `import cli` devuelve
uno u otro segun el orden de imports --con la raiz sola da el nuestro; despues
de `import corrida` da el de Hermes y explota con `ModuleNotFoundError: rich`--.
Ya se renombro una vez a `cli.py` y volvio: el nombre distinto es la proteccion.


```bash
uv run --python 3.11 --with jsonschema python orquester.py run grafo.json \
    [--board X] [--tope 2] [--timeout 900]
```

Compila y corre. Codigo de salida **0 solo si todos los nodos quedaron `done`**;
1 si alguno quedo bloqueado o la corrida se corto por tope o timeout. Eso es lo
que lo hace util como gate de CI, y es lo que fija `tests/test_cli.py` (que
corre un DAG de punta a punta reemplazando `run_backend`, o sea sin agentes de
verdad ni servidor HTTP).

### El ciclo de refinamiento: el linter es el critico

Un nodo que dice "listo" no prueba nada: lo dice igual si dejo el workspace
roto. Con `orquester run --validar pyflakes`, despues de cada nodo se mide el
workspace; si aparecio un problema que antes no estaba, la card se bloquea como
**transitoria** con ese problema adentro, y el reintento lo recibe como
contexto. Ahi se cierra el lazo: critica el linter, corrige el agente, y el
techo lo pone `MAX_INTENTOS`, que ya existia.

Tres decisiones que valen mas que el codigo (`dispatcher/validadores.py`):

1. **El comando sale de una tabla nuestra, no del grafo.** Un campo
   `validador: "<comando>"` seria ejecucion de shell escrita en un `.json`, y
   los grafos de este producto los puede haber escrito un modelo
   (`/api/generar-grafo`). Con un nombre de tabla, lo peor que puede pedir un
   grafo hostil es "corre pyflakes".
2. **Solo cuentan los hallazgos NUEVOS.** Un repo de verdad ya tiene warnings:
   comparar contra cero bloquearia el primer nodo de cualquier flujo sobre
   codigo ajeno por deuda que no escribio.
3. **La linea base se toma UNA vez por card, no por intento.** Medirla en cada
   intento tenia un agujero: el destrozo del intento 1 ya esta en disco cuando
   arranca el 2, asi que pasaba a contar como preexistente y el nodo aprobaba
   sin haber arreglado nada. Verificado: el nodo terco cerraba en `done` a la
   segunda, con el archivo igual de roto.

`tests/test_ciclo_refinamiento.py` corre el lazo entero con un backend falso que
rompe, lee y corrige. Lo que fija no es que el nodo termine --eso pasaria igual
sin validador-- sino que el segundo intento **recibio el hallazgo del primero**.

### Gates: la espera no la paga el proceso

Un nodo `tipo: gate` compila con `assignee: human`, o sea que ni nuestro
dispatcher ni el de Hermes lo levantan: espera una firma. Contarlo como trabajo
pendiente colgaba la corrida --medido: 28 vueltas en 6 segundos sobre un flujo
que no podia avanzar, y sin `timeout` no volvia nunca--.

La regla es la de cualquier motor de flujos serio (Argo `suspend`, los
environments de GitHub, y lo que Jenkins hace mal con su `input`): **el proceso
que corre el flujo no es el dueño de la espera**. La firma tarda horas, el
runner cobra por minuto, y el estado ya es durable en la SQLite del board.

`corrida.correr(..., gates=...)` toma tres valores:

| valor | quien lo usa | que hace |
|---|---|---|
| `parar` (default) | `orquester run` | corta con `motivo: "gate"`, board intacto y reanudable |
| `esperar` | el Studio, `--esperar-gates N` | sigue dando vueltas; **acotado por `timeout`** |
| `aprobar` | `--aprobar-gates` | los cierra solos y sigue |

De ahi salen los tres codigos de salida del CLI: **0** todo cerrado, **2**
pausado esperando una firma, **1** fallo o corte. Un workflow decide distinto
con cada uno: el 2 es para notificar y terminar en neutral, no en rojo.

`--aprobar-gates` deja escrito en la card que la aprobacion fue automatica
(`claimer: "auto"`, `aprobacion: "automatica"`, y el texto lo dice). No es
cosmetico: una auditoria a seis meses no tiene otra forma de distinguirlo, y un
registro que miente es peor que uno que falta. Aprobar vive en
`corrida.aprobar_gate`, que es de donde tambien cuelga `/api/gate/aprobar`: dos
formas de cerrar un gate son dos formas de asentarlo.

### `_hay_futuro`: por que un flujo trabado ahora termina

La condicion de corte miraba cada card por separado: `todo`, `ready` o
`running` contaban como trabajo. Un hijo en `todo` cuyo padre habia quedado
`blocked` de forma permanente contaba, y no se iba a mover nunca: **el bucle
giraba sin fin sobre un flujo que ya no podia avanzar**. Se descubrio corriendo
el CLI, no leyendo el codigo: A con un runtime que falla permanente y B
colgando de A dejaba la corrida sin terminar.

`corrida._hay_futuro` decide sobre el DAG y no sobre la card suelta: un `todo`
cuenta solo si **todos** sus padres pueden llegar a `done`. La corrida termina
con `motivo: "trabado"` en vez de `"sin trabajo"`, que son cosas distintas y el
llamador decide distinto con cada una.
