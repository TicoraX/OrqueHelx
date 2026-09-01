# Plan: visor de diff en vivo por nodo — 2026-08-31 (tercera tanda)

Origen: pedido del usuario de un nuevo plan de mejoras, tras cerrar el plan de
uso diario (PR #3) y el de `BackendError` tipada (PR #4). Candidato tomado de
`docs/PLAN-2026-08-22.md` T2.1 ("Live diff viewer") — **no** de la
refactorización de rutas de `ui/server.py`, que dos revisiones independientes
ya confirmaron correcto dejar afuera dos veces ("no es timidez, es disciplina
correcta") y no hay motivo nuevo para reabrir.

## 0. Premisas (a verificar — algunas están genuinamente sin cerrar)

1. **El workspace de un nodo persiste después de que termina, por defecto.**
   `ui/server.py:_limpiar_workspaces` (docstring): "El scratch de un nodo ES
   su `cwd`: `workspaces_root(board)/<task_id>`" — no se borra solo al
   terminar el nodo, solo con el botón manual "limpiar workspaces". Esto
   contradice lo que sugiere el placeholder del campo `workspace` en la UI
   ("scratch: se borra al terminar") — **posible imprecisión de copy, no
   bloqueante, mencionar en hallazgos**.
2. **Hay dos nociones de "scratch" en el código y no están claramente
   relacionadas.** `ui/server.py:1868-1869` menciona "el scratch vacío de
   Hermes (SS4.1)" como lo que un nodo ve si NO se le declara `workspace` —
   distinto de `workspaces_root(board)/<task_id>` que gestiona
   `_limpiar_workspaces`. **Sin verificar cuál es cuál, el visor de diff
   podría apuntar al lugar equivocado cuando el usuario no declaró workspace
   explícito.** Esto lo tiene que resolver la revisión de ingeniería con
   citas de código antes de escribir nada — es la pregunta central del plan,
   igual que "dónde vive `causa`" lo fue en el plan anterior.
3. **El caso real de uso es un nodo con `workspace` explícito** (el repo de
   verdad del usuario, vía "Carpeta de trabajo" en modo App o el campo
   `workspace` del nodo en Studio) — no el scratch vacío por defecto, que
   probablemente no es ni siquiera un repo git. El visor tiene que degradar
   con gracia ("no es un repo git") en ese caso, no fallar.
4. **No reinventar el endurecimiento de git.** `_analizar_workspace`
   (`server.py:1591-1698`) ya tiene `SIN_HOOKS`, `_sin_filtros()` y `_git()`
   — invocación de git que neutraliza hooks, `core.fsmonitor` y filtros de
   contenido del repo (`clean`/`smudge`/`process`) ANTES de correr un agente
   ahí. Son closures internas de esa función, no reusables tal cual. Este
   plan los extrae a nivel de módulo para que el endpoint nuevo los use
   IGUAL, no una copia — `docs/PLAN-2026-08-22.md` ya lo advirtió
   explícitamente: "no lo escribas de nuevo: el endurecimiento costó trabajo
   y un `git diff` crudo sobre un repo hostil vuelve a abrir el agujero."

## 1. Lo que ya existe y no hay que reconstruir

| Pieza | Dónde | Nota |
|---|---|---|
| Invocación de git endurecida | `server.py:_analizar_workspace` (`SIN_HOOKS`, `_sin_filtros`, `_git`) | closures locales — extraer, no copiar |
| Resolución del workspace de una card | `kanban_db.Task.workspace_path`/`workspace_kind` | ya existe, hay que confirmar su relación con el "scratch vacío de Hermes" (Premisa 2) |
| Panel de traza por nodo | `ui/index.html`, pestaña "Traza" | ya muestra intentos y eventos (`_traza` en server.py) — el diff se agrega ahí, no en una pestaña nueva |
| Límite de tamaño en contenido de card | `_estado()`: `resumen[:400]` | mismo criterio a aplicar al diff (cap de tamaño, no todo el diff crudo si es enorme) |

## 2. Alcance de este plan

**Adentro:**
- Extraer `SIN_HOOKS`/`_sin_filtros`/`_git` de `_analizar_workspace` a
  funciones de módulo en `ui/server.py`, reusadas por ambos.
- Nuevo endpoint (nombre a confirmar en la revisión: `/api/workspace/diff` o
  `/api/nodo/diff`) que recibe board+task_id, resuelve el workspace real de
  la card, corre `git diff --stat` y `git diff` con los mismos flags
  endurecidos, y devuelve el resultado (capado en tamaño).
- Panel nuevo en la pestaña "Traza" del Studio que pide y pinta ese diff.
- Degradación explícita: workspace no es repo git → mensaje claro, no error.

**Afuera, a propósito:**
- Terminal en vivo (T2.2 de `docs/PLAN-2026-08-22.md`) — más grande, more
  security-sensitive (el stream necesita pasar por `limpiar_salida` en
  línea, no al final), plan aparte.
- Refactor de rutas de `ui/server.py` — ya decidido dos veces que no toca
  acá, sin motivo nuevo para reabrirlo.
- Diff en vivo DURANTE la ejecución del nodo (mientras corre) — esta pasada
  es post-ejecución, sobre el workspace ya tocado. Un diff en vivo a mitad
  de un `git status` concurrente con el agente escribiendo es una categoría
  de riesgo distinta (carrera de lectura/escritura), no la ataca este plan.

## 3. Diagrama: qué toca cada tarea

```
        ┌──────────────────────────────┐
        │  ui/server.py                 │
        │  _analizar_workspace()         │
        │  SIN_HOOKS/_sin_filtros/_git   │──extraer a modulo──┐
        └──────────────────────────────┘                    │
                                                              ▼
        ┌──────────────────────────────┐        ┌────────────────────┐
        │  nuevo endpoint                │◄───────│ funciones de git    │
        │  /api/.../diff                 │        │ endurecidas,         │
        │  resuelve workspace de la card │        │ COMPARTIDAS          │
        │  (via kanban_db.Task)          │        └────────────────────┘
        └──────────────┬────────────────┘
                       │ JSON: {ok, es_git, stat, diff, truncado}
                       ▼
        ┌──────────────────────────────┐
        │  ui/index.html                 │
        │  pestaña "Traza" — panel nuevo │
        │  pide el diff, lo pinta         │
        └──────────────────────────────┘

  Sin tocar: dispatcher/, compiler/, apps/api/. No cambia como corre un
  nodo, solo agrega una LECTURA de su workspace despues de que corrio.
```

## 4. Tareas

### F1 · Extraer las funciones de git endurecidas
- **Qué**: mover `SIN_HOOKS`, `_sin_filtros`, `_git` de dentro de
  `_analizar_workspace` a nivel de módulo (mismo comportamiento, misma
  documentación de por qué cada flag existe — no se pierde el porqué).
- **Riesgo**: bajo — es un refactor mecánico de closures a funciones de
  módulo, mismo cuerpo. `_analizar_workspace` sigue funcionando igual.
- **Test**: los tests existentes que ya ejercitan `_analizar_workspace`
  (si los hay) tienen que seguir en verde sin cambios — confirma que la
  extracción no alteró comportamiento.

### F2 · Endpoint de diff
- **Qué**: nueva función que recibe `board`+`task_id`, resuelve el
  workspace real vía `kanban_db.get_task`, corre `git diff --stat` y
  `git diff` con `_git()`/`SIN_HOOKS`, capa el tamaño (mismo criterio que
  `resumen[:400]`, pero para un diff probablemente más — a definir en la
  revisión) y devuelve `{ok, es_git, stat, diff, truncado}`.
- **Riesgo real**: el mismo que ya identificó `_analizar_workspace` para
  lectura de carpetas ajenas — mitigado reusando exactamente su
  endurecimiento (F1), no reinventándolo.
- **Test**: workspace que es un repo git con cambios reales → diff no
  vacío; workspace que no es git → `es_git: false`, sin error; diff
  gigante → se capa, no se manda crudo.

### F3 · Panel en la pestaña Traza
- **Qué**: en `ui/index.html`, la pestaña "Traza" pide el diff del nodo
  seleccionado y lo pinta (colapsable, ya que puede no interesar en cada
  vistazo).
- **Riesgo**: bajo — solo lectura, no cambia ningún flujo de ejecución.
- **Test**: Playwright (`tests/ui_navegador.mjs`) — mockear el endpoint
  nuevo, confirmar que el panel aparece con datos y que degrada bien
  cuando `es_git: false`.

## 5. Qué NO resuelve este plan

- Terminal en vivo (T2.2) — plan aparte, con su propia advertencia de
  seguridad ya escrita.
- Refactor de rutas de `ui/server.py` — no reabierto sin motivo nuevo.
- Diff en vivo durante la ejecución (no post-ejecución) — categoría de
  riesgo distinta, no atacada acá.
- La imprecisión del placeholder "se borra al terminar" (Premisa 1) — se
  menciona como hallazgo menor, no se corrige en este plan salvo que la
  revisión diga que vale la pena en el mismo diff.
