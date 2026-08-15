# ORQUESTER — Arquitectura del Sistema (v2)

> v2 reemplaza el motor propio por **Hermes Agent** (Nous Research, MIT) como runtime prestado.
> La v1 queda en `ARQUITECTURA.v1.md`. Toda afirmación sobre Hermes cita `hermes-agent/<archivo>:<línea>`.

---

## 1. Decisión de arquitectura

Hermes ya implementa el núcleo que la v1 planeaba construir: aislamiento de contexto, compresión, delegación jerárquica, multi-proveedor, cron, aprobaciones y un scheduler de DAG durable. Construirlo de nuevo compite contra código MIT probado.

**ORQUESTER deja de ser un motor de orquestación y pasa a ser la capa de diseño, gobierno y exportación sobre un runtime prestado.**

| Capa | Dueño | Justificación |
|---|---|---|
| Ejecución de agentes, aislamiento, compresión | Hermes | Ya resuelto, MIT |
| Scheduling de DAG, durabilidad, reintentos | Hermes (kanban) | `task_links` + dispatcher |
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
|  Auth / RBAC · Persistencia de grafos (Postgres + Prisma)     |
+---------------------------------------------------------------+
                    | CLI headless · ACP (JSON-RPC) · kanban CLI
                    v
+---------------------------------------------------------------+
|                    HERMES AGENT (runtime, MIT)                |
|  delegate_task · context_compressor · kanban dispatcher       |
|  providers (Anthropic/OpenAI/Gemini/Bedrock/Vertex/local)     |
|  environments: local · Docker · SSH · Modal · Daytona · Vercel|
+---------------------------------------------------------------+
```

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

Único componente de motor que ORQUESTER escribe. No es un motor de workflows; es un mapeo.

```
Nodo de React Flow      ->  hermes kanban create   (goal, assignee=perfil, workspace)
Arista del DAG          ->  hermes kanban link parent child
Ejecución               ->  el dispatcher promueve y hace spawn; ORQUESTER no orquesta
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

Un nodo con `runtime: opencode` compila a `create_task(assignee=<perfil>, skills=["opencode"], ...)`. El dispatcher hace spawn de un worker Hermes con esa skill precargada; el worker supervisa al agente externo por `terminal` y devuelve el resultado. **Hermes normaliza**: con `output_schema` en el nodo, los cuatro backends devuelven el mismo `AgentAdapterOutput`.

Las skills ya existen en el repo, no hay que escribirlas:

| Backend | Skill | Invocación headless | Salida estructurada |
|---|---|---|---|
| Claude Code | `skills/autonomous-ai-agents/claude-code/` | `claude -p '...' --output-format json --json-schema '{...}' --max-turns N` | **Nativa**; además `--resume`, `--fork-session` |
| OpenCode | `skills/autonomous-ai-agents/opencode/` | `opencode run '...' --format json --model p/m` | Eventos JSON, sin schema; `--attach <url>` a server vivo |
| Antigravity | `optional-skills/autonomous-ai-agents/antigravity-cli/` | `agy --print '...'` | Texto plano; contrato impuesto por Hermes |
| Hermes | nativo | `delegate_task` | `output_schema` nativo |

También vienen `codex`, `openhands`, `grok`, `blackbox` y `computer-use` con el mismo patrón.

Notas de diseño:
- Claude Code acepta el JSON Schema en la propia invocación: para esos nodos, empujar `AgentAdapterOutput` hacia abajo en vez de validar después.
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
| `modules/adapters/` | Reducir a uno | Un adapter Hermes (CLI o ACP) |
| `modules/telemetry/` | Invertir rol | Consumidor de `task_events` + ACP, no productor |
| `modules/orchestrator/` | Eliminar | `delegate_task` + dispatcher |

Sobrevive de NestJS: auth/RBAC, persistencia de grafos, compilador, proyector de eventos, exportador MCP.

---

## 7. Estructura del monorepo (v2)

```text
orquester/
├── apps/
│   ├── api/                      # NestJS: fino, sin motor
│   │   └── src/modules/
│   │       ├── graphs/           # CRUD y versionado de grafos del Studio
│   │       ├── compiler/         # DAG -> kanban (topo-check + create + link)
│   │       ├── runtime/          # Adapter Hermes: CLI headless + cliente ACP
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
| Un nodo delega a OpenCode / Antigravity / Claude Code | **Pendiente** | Siguiente tarea del plan |

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
