# Plan: visor de diff en vivo por nodo — 2026-08-31 (tercera tanda)

Origen: pedido del usuario de un nuevo plan de mejoras, tras cerrar el plan de
uso diario (PR #3) y el de `BackendError` tipada (PR #4). Candidato tomado de
`docs/PLAN-2026-08-22.md` T2.1 ("Live diff viewer") — **no** de la
refactorización de rutas de `ui/server.py`, que dos revisiones independientes
ya confirmaron correcto dejar afuera dos veces ("no es timidez, es disciplina
correcta") y no hay motivo nuevo para reabrir.

## 0. Premisas — revisadas tras dual review (CEO + eng), corregidas

1. **El workspace de un nodo persiste después de que termina, por defecto**
   — confirmado. El placeholder de la UI ("scratch: se borra al terminar")
   es impreciso; hallazgo menor, no bloqueante.

2. **Premisa 2 original resuelta por la revisión de ingeniería — son TRES
   casos, no dos, y uno es peor de lo que asumía:**
   - **Nodo `hermes` sin `workspace` declarado**: el dispatcher NATIVO de
     Hermes (proceso aparte) resuelve y persiste `workspace_path =
     workspaces_root(board)/task_id` (`kanban_db.py:7748-7789`,
     `resolve_workspace`) — un directorio real en disco, probablemente sin
     repo git.
   - **Nodo `orquester-external:*` (opencode/agy/claude-code) sin
     `workspace` declarado**: `dispatcher/loop.py` **nunca** llama
     `resolve_workspace` — verificado por grep, cero resultados. Con `cwd=
     task.workspace_path or None` (`loop.py:365`) y `workspace_path=NULL`,
     `backends.py:610-612` dice que el proceso hereda el cwd del propio
     dispatcher de ORQUESTER. **No hay carpeta propia del nodo para
     diffear, ni siquiera un scratch vacío** — peor que "no es un repo
     git", es "no existe una carpeta que le pertenezca a este nodo".
   - **Nodo con `workspace` explícito** (cualquier runtime): `task.
     workspace_path` apunta al repo real del usuario — el caso de uso
     principal, confirmado correcto.
   
   F2 tiene que distinguir los tres, no asumir uno: `workspace_path is
   None` → mensaje explícito "no hay workspace resuelto para este nodo"
   (distinto de "no es un repo git"); ruta que no existe en disco (pudo
   limpiarse con el botón manual mientras tanto) → mensaje aparte; ruta
   que existe pero no es git → "no es un repo git".

3. **Corregida por el hallazgo #2**: el caso "sin workspace" no siempre
   degrada con gracia a "no es repo git" — para nodos externos sin
   workspace, degrada a "no hay carpeta que diffear", un mensaje distinto
   que hay que mostrar explícitamente.

4. **No reinventar el endurecimiento de git — con un agujero real que hay
   que cerrar en el camino.** `_analizar_workspace` (`server.py:1591-1698`)
   tiene `SIN_HOOKS`, `_sin_filtros()` y `_git()`, pero **`_sin_filtros()`
   solo neutraliza `filter.*.(clean|smudge|process)`** (lo que usan `git
   status`/`add`/checkout). **`git diff` además invoca
   `diff.<driver>.textconv`** (declarado vía `.gitattributes` +
   `.git/config`), que `_sin_filtros()` NO cubre — confirmado por lectura
   directa de la revisión de ingeniería. Un repo hostil con un `textconv`
   declarado ejecutaría ese comando apenas F2 corriera `git diff` sobre un
   archivo que matchee el patrón. **Esto no es "vale la pena": es
   obligatorio para F1**, con la corrección más simple — pasar `--no-
   textconv` como flag de `git diff` en vez de descubrir y neutralizar
   cada driver declarado (mismo resultado, menos código).

## 0.5 Lo que la revisión CEO cuestionó, sin resolver todavía (ver sección 6)

- **¿"Sin motivo nuevo" para no tocar las rutas de `server.py` sigue siendo
  cierto?** Este es el TERCER plan seguido que agrega código a ese archivo
  (D1/D2b lo tocaron, `causa` lo tocó indirectamente, y F1/F2 de este plan
  lo tocan de nuevo). `ESTADO.md` sigue listando la deuda de rutas como
  pendiente activo. El motivo nuevo puede ser "cada plan que no consolida
  hace el refactor futuro más caro" — no lo decido yo, ver pregunta al
  usuario.
- **¿F3 completo (diff colapsable) vs. la alternativa barata (solo `git
  diff --stat` insertado en el reporte ejecutivo que YA existe)?** El plan
  no comparó costo/valor antes de comprometerse a la versión cara. Ver
  pregunta al usuario.

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
                       │ JSON: {ok, caso, stat, diff, truncado}
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

### F1 · Extraer las funciones de git endurecidas + cerrar el agujero de `textconv`
- **Qué**: mover `SIN_HOOKS`, `_sin_filtros`, `_git` de dentro de
  `_analizar_workspace` a nivel de módulo. **No es copiar y pegar**: `_git`
  y `_sin_filtros` capturan `p` (la carpeta) por closure — al extraer, `p`
  pasa a ser un parámetro explícito (`_git(*args, cwd, extra=())`), y hay
  que actualizar los ~4 call-sites dentro de `_analizar_workspace` a la
  firma nueva.
- **Obligatorio, no opcional**: agregar `--no-textconv` al `git diff` que
  use F2 (no a `git status`, que no lo necesita). Es la corrección más
  simple para el hallazgo de seguridad de la revisión de ingeniería — evita
  tener que descubrir y neutralizar cada driver `diff.<x>.textconv`
  declarado en un repo hostil.
- **Riesgo**: bajo si se hace el cambio de firma con cuidado (test de abajo
  lo confirma); el riesgo de seguridad real es el de `textconv` de arriba,
  ya cerrado con `--no-textconv`.
- **Test**: los tests existentes que ejercitan `_analizar_workspace` (si
  los hay) siguen en verde sin cambios. Nuevo: un repo con un `textconv`
  hostil declarado en `.gitattributes`+`.git/config` — confirmar que
  `git diff` con `--no-textconv` NO ejecuta el comando declarado.

### F2 · Endpoint de diff
- **Qué**: nueva función que recibe `board`+`task_id`, lee `kanban_db.
  get_task(conn, task_id).workspace_path` y maneja los TRES casos de la
  Premisa 2 por separado (`None` / no existe en disco / no es git), corre
  `git diff --stat` primero (barato) y solo pide el diff completo si el
  stat es chico. Cap de tamaño en el mismo orden de magnitud que ya usa el
  código (400 chars en `_estado()`, 2000 en `loop.py:384`) — no un número
  inventado. Mismo `timeout=3` que ya tiene `_git()`, sin relajarlo "porque
  el diff es más grande". Devuelve `{ok, caso, stat, diff, truncado}` donde
  `caso` es uno de `"sin_workspace" | "workspace_perdido" | "no_es_git" |
  "ok"` — los tres primeros son los casos de la Premisa 2, no un solo
  `es_git: false` genérico.
- **Riesgo real**: el mismo que ya identificó `_analizar_workspace` para
  lectura de carpetas ajenas — mitigado reusando exactamente su
  endurecimiento (F1) MÁS `--no-textconv`, no solo el endurecimiento viejo.
- **Test**: workspace que es un repo git con cambios reales → diff no
  vacío; `workspace_path=None` (nodo externo sin workspace) → `caso:
  "sin_workspace"`; ruta que no existe en disco → `caso: "workspace_
  perdido"`; ruta que existe pero no es git → `caso: "no_es_git"`; diff
  gigante → se capa, no se manda crudo.

### F3 · Panel en la pestaña Traza
- **Qué**: en `ui/index.html`, la pestaña "Traza" pide el diff del nodo
  seleccionado y lo pinta (colapsable, ya que puede no interesar en cada
  vistazo).
- **Riesgo**: bajo — solo lectura, no cambia ningún flujo de ejecución.
- **Test**: Playwright (`tests/ui_navegador.mjs`) — mockear el endpoint
  nuevo, confirmar que el panel aparece con datos y que cada `caso`
  (`sin_workspace`/`workspace_perdido`/`no_es_git`) muestra un mensaje
  distinto, no un genérico "no hay diff".

## 5. Qué NO resuelve este plan

- Terminal en vivo (T2.2) — plan aparte, con su propia advertencia de
  seguridad ya escrita.
- Diff en vivo durante la ejecución (no post-ejecución) — categoría de
  riesgo distinta, no atacada acá.
- La imprecisión del placeholder "se borra al terminar" (Premisa 1) — se
  menciona como hallazgo menor, no se corrige en este plan.

## 6. Registro de decisiones (post revisión dual)

| # | Decisión | Origen | Resolución |
|---|---|---|---|
| 1 | Premisa 2: tres casos (hermes-sin-workspace / externo-sin-workspace / con-workspace), no dos | Eng (crítico) | Auto-decidido — verificado línea por línea contra `kanban_db.py`/`loop.py`/`backends.py` |
| 2 | `--no-textconv` obligatorio en F1, no opcional | Eng (crítico, seguridad) | Auto-decidido — agujero real, mismo tipo de riesgo que el proyecto ya cerró antes en `_sin_filtros` |
| 3 | Cap de tamaño del diff sigue el precedente existente (400/2000 chars), no un número inventado | Eng (medio) | Auto-decidido |
| 4 | F1 no es copiar-pegar: `_git`/`_sin_filtros` necesitan `cwd` como parámetro explícito, no closure | Eng (medio) | Auto-decidido |
| 5 | F1+F2+F3 completo, con panel de diff — no solo stat en el reporte | CEO (medio) | **Decidido por el usuario**: el caso real es revisar QUÉ cambió línea por línea, no solo cuántos archivos; con `--no-textconv` ya cerrando el riesgo extra, el costo de seguridad del diff completo sobre el stat-only es chico |
| 6 | Seguir con el visor de diff ahora; el refactor de rutas de `ui/server.py` entra como su propio plan, después | CEO (alto) | **Decidido por el usuario**: mezclar un refactor con una feature en el mismo PR complica la revisión; el refactor es mecánico y de bajo riesgo por sí solo, mejor como su propia pasada enfocada |

**Estado: F1+F2+F3 implementados y verificados** (`tests/test_visor_diff.py`
6/6, incluye el ataque de `textconv` confirmado bloqueado; `test_contrato_ui.py`
119 claves; `ui_navegador.mjs` en verde). El refactor de rutas de
`ui/server.py` queda anotado como el próximo plan natural — no se pierde,
se secuencia.
