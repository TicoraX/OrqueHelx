# Test: worker real ejecutando una tarea del kanban de Hermes

Tarea 2 del plan de verificación ORQUESTER (`.superpowers/sdd/2026-08-14-subagentes-heterogeneos/task-2-brief.md`).
Objetivo: probar que `hermes kanban dispatch` reclama una card, hace spawn de
un worker con un modelo real (`copilot` / `gpt-4.1`), y la lleva hasta `done`.

**Veredicto general: FALLA (BLOCKED).** El dispatcher sí reclama la card y
hace spawn de un worker real (PID real, heartbeats reales, workspace real).
El worker, en dos corridas independientes, se queda colgado indefinidamente
en la fase de inicialización del agente (`Initializing agent...`) y nunca
llega a ejecutar ninguna acción visible (ni escribir `hola.txt` ni producir
más output de log). No se pudo verificar el camino completo hasta `done`.

## Entorno verificado

- Config al empezar (`%LOCALAPPDATA%\hermes\config.yaml`, bloque `model:`):
  ```
  model:
      default: gpt-4.1
      provider: copilot
      base_url: ''
  ```
- Config al terminar: **idéntica**, sin cambios (mismo bloque, verificado
  después de las dos corridas).
- `hermes.exe` no está en el PATH; se usó la ruta completa
  `$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe`.
- No se arrancó el gateway (`hermes gateway start`). Se usó `kanban dispatch`
  directamente, tal como indica el brief.
- No se usó `delegate_task` en ningún momento.
- No se usó `hermes -z` en ningún momento.

## Desviación respecto del brief: sintaxis de `kanban`

El brief especifica `hermes kanban init --board orquester-test` y
`hermes kanban create --board orquester-test ...`. Esa sintaxis no existe en
la versión instalada. `--board` es una opción del subcomando `kanban` en sí
(va **antes** del verbo: `hermes kanban --board <slug> <verbo>`), y no hay
verbo `init` para boards (existe `hermes kanban boards create <slug>`).
Documentado abajo con la salida cruda del `--help`.

```
> & $h kanban init --board orquester-test
usage: hermes [-h] [--version] [-z PROMPT] [--usage-file PATH] [-m MODEL]
              [--provider PROVIDER] [--reasoning LEVEL] [-t TOOLSETS]
              [--resume SESSION] [--no-restore-cwd] [--in DIR]
              [--continue [SESSION_NAME]] [--worktree] [--accept-hooks]
              [--skills SKILLS] [--yolo] [--pass-session-id]
              [--ignore-user-config] [--ignore-rules] [--safe-mode] [--tui]
              [--cli] [--dev]
              {chat,model,moa,fallback,secrets,egress,migrate,gateway,proxy,lsp,setup,whatsapp,whatsapp-cloud,slack,send,login,logout,auth,status,pause,resume,cron,sync,webhook,portal,kanban,project,hooks,doctor,verify,security,approvals,dump,debug,backup,checkpoints,import,import-agent,config,skin,console,pairing,skills,bundles,plugins,curator,pets,journey,learning,memory-graph,memory,tools,computer-use,mcp,sessions,insights,monitoring,claw,version,update,uninstall,acp,profile,completion,dashboard,serve,desktop,gui,logs,prompt-size}
              ...
hermes: error: unrecognized arguments: --board orquester-test
```

Comandos reales usados en su lugar (funcionalmente equivalentes):

```
& $h kanban boards create orquester-test
& $h kanban --board orquester-test create "Escribir hola.txt con la palabra HOLA" --assignee default
& $h kanban --board orquester-test list
& $h kanban --board orquester-test dispatch
& $h kanban --board orquester-test show t_1d37a781
& $h kanban --board orquester-test runs t_1d37a781
& $h kanban --board orquester-test log t_1d37a781
& $h kanban --board orquester-test reclaim t_1d37a781 --reason "..."
```

`--assignee default` funcionó sin error (no hizo falta el fallback de
`profile list` / `kanban assignees`); igual se corrió `assignees` y
`profile list` para confirmar que el perfil `default` (gpt-4.1, copilot)
existe y está en disco:

```
> & $h kanban --board orquester-test assignees
NAME                  ON DISK   COUNTS
default               yes       (idle)

> & $h profile list
 Profile          Model                        Gateway      Alias        Distribution
 ───────────────    ───────────────────────────    ───────────    ───────────    ────────────────────
 ◆default         gpt-4.1                      stopped      —            —
```

## Paso 1: Crear el board y la card

```
> & $h kanban boards create orquester-test
Board 'orquester-test' created.
  Display name: Orquester Test
  DB path:      C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\kanban.db
  Use `hermes kanban boards switch orquester-test` to make it current.

> & $h kanban --board orquester-test create "Escribir hola.txt con la palabra HOLA" --assignee default
Created t_1d37a781  (ready, assignee=default)
⚠  No gateway is running — the task will sit in 'ready' until you start it. Run:
    hermes gateway start
The gateway hosts an embedded dispatcher (tick interval 60s by default); your task will be picked up on the next tick after the gateway comes up.

> & $h kanban --board orquester-test list
Board: orquester-test (1 other board — `hermes kanban boards list`)

▶ t_1d37a781  ready     default               Escribir hola.txt con la palabra HOLA
```

**PASA.** La card `t_1d37a781` se crea en estado `ready` (sin padres), como
esperaba el brief. El warning sobre "no gateway running" es esperado y
correcto: el brief pide explícitamente NO arrancar el gateway y usar
`kanban dispatch` en su lugar.

## Paso 2: Una pasada del dispatcher (corrida 1)

```
> & $h kanban --board orquester-test dispatch
Reclaimed:    0
Crashed:      0
Timed out:    0
Stale:        0
Auto-blocked: 0
Promoted:     0
Spawned:      1
  - t_1d37a781  ->  default  @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781

> & $h kanban --board orquester-test list
Board: orquester-test (1 other board — `hermes kanban boards list`)

● t_1d37a781  running   default               Escribir hola.txt con la palabra HOLA
```

**PASA.** El dispatcher reclama la card, hace spawn de un worker real (PID de
proceso `hermes.exe` verificado vivo en el sistema operativo) y la card pasa
de `ready` a `running`. Esto es la parte crítica del test: confirma que
`kanban dispatch` sí ejecuta un worker de verdad, no solo actualiza estado en
la base.

## Paso 3: Observar hasta el cierre — corrida 1 (colgada)

Se evitó `kanban tail` (el brief advierte que puede quedarse esperando) y se
sondeó el estado con `kanban list` / `kanban show` cada 30-45s durante ~12
minutos, más una inspección directa del log en disco y del proceso del SO.

Progresión de estado observada (via `kanban list`, repetido ~12 veces en 12
minutos): **`running` todo el tiempo, sin cambios.**

Eventos acumulados (`kanban show t_1d37a781`, snapshot antes de cortar):

```
Task t_1d37a781: Escribir hola.txt con la palabra HOLA
  status:    running
  assignee:  default
  workspace: scratch @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781
  max-retries: 2 (default)
  created:   2026-08-15 00:10 by user
  started:   2026-08-15 00:10

Events (15):
  [2026-08-15 00:10] created {...}
  [2026-08-15 00:10] [run 1] claimed {'lock': 'DESKTOP-S1JMVFK:21492', 'expires': 1786771526, 'run_id': 1}
  [2026-08-15 00:10] tip_scratch_workspace {...}
  [2026-08-15 00:10] [run 1] spawned {'pid': 32696}
  [2026-08-15 00:10] [run 1] heartbeat
  [2026-08-15 00:11] [run 1] heartbeat
  [2026-08-15 00:12] [run 1] heartbeat
  [2026-08-15 00:13] [run 1] heartbeat
  [2026-08-15 00:14] [run 1] heartbeat
  [2026-08-15 00:15] [run 1] heartbeat
  [2026-08-15 00:16] [run 1] heartbeat
  [2026-08-15 00:17] [run 1] heartbeat
  [2026-08-15 00:18] [run 1] heartbeat
  [2026-08-15 00:19] [run 1] heartbeat
  [2026-08-15 00:21] [run 1] heartbeat

Runs (1):
  #1   running      @default  active  2026-08-15 00:10
```

Log del worker (`kanban log t_1d37a781`, y confirmado igual leyendo el
archivo crudo en
`C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\logs\t_1d37a781.log`,
que no cambió de tamaño (571 bytes) ni de mtime (00:10:41) durante los 12
minutos de la corrida):

```
Query: work kanban task t_1d37a781
Initializing agent...
────────────────────────────────────────

⚠ Deprecated .env settings detected:
  ⚠ TERMINAL_CWD=C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781 found in .env — this is deprecated.
  Move to config.yaml instead:  terminal:\n    cwd: /your/project/path
  Then remove the old entries from ~/AppData\Local\hermes/.env
```

Proceso del SO (`Get-Process -Id 32696`), verificado a los ~11 minutos:

```
   Id ProcessName StartTime              CPU
   -- ----------- ---------              ---
32696 hermes      8/15/2026 12:10:27 AM 0.03
```

0.03 segundos de CPU acumulados en 11 minutos de vida del proceso: consistente
con un proceso esperando en I/O (ej. una llamada de red que nunca vuelve),
no con un worker haciendo trabajo real.

`Get-ChildItem` sobre el workspace de la card
(`...\workspaces\t_1d37a781`) devolvió **vacío**: no se creó `hola.txt` ni
ningún otro archivo.

**FALLA.** A los ~12 minutos, sin ningún cambio de estado, sin crecimiento
del log más allá de la inicialización, y con CPU casi nula en el proceso del
worker, se decidió cortar la observación (regla del brief: sin señales de
avance en ~5 minutos, cortar y reportar bloqueo en vez de seguir esperando).
Se liberó el claim con:

```
> & $h kanban --board orquester-test reclaim t_1d37a781 --reason "Verificacion Tarea 2: worker colgado 12+ min sin avance en log (Initializing agent... sin cambios), CPU ~0.03s. Se corta la observacion y se libera el claim."
Reclaimed t_1d37a781
```

Evento registrado tras el reclaim:

```
[2026-08-15 00:22] [run 1] reclaimed {'manual': True, 'reason': 'Verificacion Tarea 2: worker colgado 12+ min sin avance en log (Initializing agent... sin cambios), CPU ~0.03s. Se corta la observacion y se libera el claim.', 'prev_lock': 'DESKTOP-S1JMVFK:21492', 'retry_status': 'ready', 'prev_pid': 32696, 'host_local': True, 'termination_attempted': True, 'terminated': True, 'sigkill': False}
```

`task_runs` (`kanban runs t_1d37a781`) para la corrida 1:

```
#    OUTCOME       PROFILE            ELAPSED  STARTED
  1  reclaimed     default                12m  2026-08-15 00:10
     ✖ manual_reclaim: Verificacion Tarea 2: worker colgado 12+ min sin avance en log (Initializing agent..
```

El reclaim confirmó también que el PID 32696 fue efectivamente terminado
(`terminated: True`) — tras el reclaim, `Get-Process -Id 32696` no devolvió
nada. El reclaim devolvió la card a `ready`.

## Paso 3 (repetido): segunda corrida, para descartar un glitch puntual

Dado que el brief exige distinguir un fallo real de una espera razonable, se
hizo una segunda pasada del dispatcher sobre la misma card ya reclamada, con
una ventana de observación más corta (~3.5 minutos), para ver si el
comportamiento se repetía.

```
> & $h kanban --board orquester-test dispatch
Reclaimed:    0
Crashed:      0
Timed out:    0
Stale:        0
Auto-blocked: 0
Promoted:     0
Spawned:      1
  - t_1d37a781  ->  default  @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781
```

Progresión de estado: `running` durante los ~3.5 minutos completos de
observación, sin cambios.

Log del worker tras la segunda corrida (se le agrega al mismo archivo; el
segundo bloque es la corrida 2, idéntico patrón al de la corrida 1):

```
Query: work kanban task t_1d37a781
Initializing agent...
────────────────────────────────────────

⚠ Deprecated .env settings detected:
  ⚠ TERMINAL_CWD=C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781 found in .env — this is deprecated.
  Move to config.yaml instead:  terminal:\n    cwd: /your/project/path
  Then remove the old entries from ~/AppData\Local\hermes/.env

Query: work kanban task t_1d37a781
Initializing agent...
────────────────────────────────────────

⚠ Deprecated .env settings detected:
  ⚠ TERMINAL_CWD=C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781 found in .env — this is deprecated.
  Move to config.yaml instead:  terminal:\n    cwd: /your/project/path
  Then remove the old entries from ~/AppData\Local\hermes/.env
```

Mismo patrón exacto: se cuelga en `Initializing agent...` y no avanza. Se
cortó y liberó el claim de nuevo:

```
> & $h kanban --board orquester-test reclaim t_1d37a781 --reason "Segunda corrida: mismo patron de cuelgue en Initializing agent tras ~3.5 min, sin avance en log. Se corta la observacion."
Reclaimed t_1d37a781
```

`task_runs` final (dos corridas, ambas `reclaimed`):

```
#    OUTCOME       PROFILE            ELAPSED  STARTED
  1  reclaimed     default                12m  2026-08-15 00:10
     ✖ manual_reclaim: Verificacion Tarea 2: worker colgado 12+ min sin avance en log (Initializing agent..
  2  reclaimed     default                 3m  2026-08-15 00:22
     ✖ manual_reclaim: Segunda corrida: mismo patron de cuelgue en Initializing agent tras ~3.5 min, sin av
```

Estado final de la card (`kanban list`): `ready` (max-retries configurado en
2; con dos intentos ya consumidos, un tercer `dispatch` agotaría el
circuit breaker de reintentos por fallo consecutivo).

**FALLA.** Reproducido dos veces de forma independiente con el mismo patrón
exacto: spawn correcto, heartbeats periódicos (cada ~1 min) que indican que
el proceso del worker sigue vivo a nivel de SO, pero el log de la sesión
del agente nunca avanza más allá de "Initializing agent..." y CPU
prácticamente nula. No se generó `hola.txt` en ningún momento en ninguna de
las dos corridas.

## Paso 4: resumen de hallazgos pedidos por el brief

- **Id de la card:** `t_1d37a781`.
- **Recorrido de estados observado:** `ready` → `running` (corrida 1,
  spawn PID 32696) → `ready` (reclaim manual tras 12 min sin avance) →
  `running` (corrida 2, spawn PID 12292) → `ready` (reclaim manual tras
  ~3.5 min, mismo patrón). Nunca alcanzó `done`.
- **`hola.txt`:** no se creó en ningún momento. El workspace de la card
  (`C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_1d37a781`)
  quedó vacío en ambas corridas.
- **Dónde quedó la card:** en `ready`, con dos filas en `task_runs`
  (`kanban runs`), ambas con outcome `reclaimed` y motivo `manual_reclaim`
  (reclaim manual del operador, no un fallo/timeout automático del propio
  dispatcher — el dispatcher nunca marcó la corrida como `crashed` ni
  `timed out` por sí solo dentro de la ventana observada).

## Hipótesis (no confirmadas, no instrumenté el código de Hermes)

Ninguna de estas se probó — quedan como hipótesis para quien continúe la
investigación, no como causa raíz establecida:

1. El worker podría estar esperando indefinidamente una respuesta de la API
   de Copilot (`provider: copilot`, `model: gpt-4.1`) que nunca llega —
   coherente con CPU ~0 y "Initializing agent..." sin avanzar, pero no se
   capturó tráfico de red para confirmarlo.
2. Podría ser un problema de autenticación/token de Copilot que se cuelga en
   vez de fallar rápido (en contraste con `hermes chat -q "..." -Q`, que el
   contexto de la tarea reporta como funcional y rápido para prompts
   simples — no se volvió a probar `chat -q` en esta sesión porque no era
   parte del brief, pero la asimetría entre "chat funciona" y "worker de
   kanban se cuelga" sugiere que el problema está específicamente en el
   camino de arranque del worker de kanban, no en el proveedor en sí).
3. Podría estar relacionado con el mismo patrón de cuelgue reportado para
   `delegate_task` en la tarea anterior (timeouts de 5-10 min) — el síntoma
   externo (colgado en inicialización, sin error explícito) es similar,
   pero `kanban dispatch` es un camino de código distinto y no se confirmó
   que compartan causa.

## Conclusión

El **reclamo y spawn del worker funcionan**: el dispatcher del kanban de
Hermes sí reclama cards `ready` y sí lanza un proceso worker real apuntando
al modelo configurado (`copilot`/`gpt-4.1`), moviendo la card a `running`.
Esa parte de la arquitectura de ORQUESTER (compilar nodos del DAG a cards y
confiar en que el dispatcher las recoja) tiene sustento.

Lo que **no se pudo verificar** es que el worker complete la tarea: en dos
corridas independientes se colgó en la fase de inicialización del agente y
nunca produjo output, nunca escribió el archivo pedido, y nunca llegó a
`done`. Esto es un bloqueo real para la apuesta de ORQUESTER de que el
kanban ejecuta el DAG completo end-to-end con workers reales — el camino de
"reclamar y arrancar" está probado, el camino de "completar el trabajo" no.

Recomendación: antes de confiar en `kanban dispatch` como motor de ejecución
del Studio, reproducir este cuelgue con logging más verboso del lado de
Hermes (fuera del alcance de esta tarea, que pidió medir desde afuera sin
instrumentar el código) o con un modelo/proveedor distinto de `copilot` para
aislar si el cuelgue es específico del proveedor Copilot o general al
worker de kanban.
