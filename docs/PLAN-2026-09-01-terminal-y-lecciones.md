# Plan: terminal en vivo por nodo + lecciones por repositorio — 2026-09-01

Origen: el usuario pidió features visibles, "que se sientan" — después de
cuatro planes seguidos de infraestructura/refactor sin pantalla nueva.
Combina T2.2 (terminal en vivo) y T2.5 (lecciones por repositorio) de
`docs/PLAN-2026-08-22.md`, elegidas por el usuario entre tres candidatas.

## 0. Premisas (investigadas antes de escribir el plan)

1. **Hoy no hay nada en vivo del contenido de un nodo mientras corre.**
   `dispatcher/backends.py:_correr` (línea 558) usa `proc.communicate(timeout=
   timeout)` — bufferea TODO stdout/stderr y recién devuelve algo cuando el
   proceso termina. `/api/eventos` (SSE) ya existe pero informa cambios de
   *estado* (`_estado()`), no el output crudo del agente.

2. **Decisión de arquitectura central, sin cerrar — la más importante del
   plan.** Hay dos caminos honestos para mostrar output "en vivo":
   - **(A) Streaming real por threads**: dos hilos lectores (uno por
     stdout, uno por stderr) haciendo `readline()` en loop, empujando cada
     línea a un buffer compartido keyed por `task_id`, con un vigía de
     timeout separado. Más "en vivo" de verdad (latencia de líneas, no de
     polling).
   - **(B) Archivo + polling**: redirigir `stdout`/`stderr` del subproceso a
     un archivo temporal por corrida (`stdout=open(ruta, "w")` en vez de
     `PIPE`), y un endpoint liviano que devuelve el contenido actual del
     archivo (con offset, tipo `tail -f` pedido a demanda) cada vez que el
     navegador lo consulta — reusando el patrón de polling que YA existe
     (`refrescarEstado()` cada 2.5s en el cliente).
   
   **Recomendación de este plan: (B).** `select()`/`selectors` sobre pipes
   no es confiable en Windows (este proyecto corre en Windows y en el
   contenedor Linux de CI, `tests/linux.sh`) — la alternativa portable de
   (A) son threads lectores, que agregan sincronización real (lock sobre el
   buffer compartido, manejo de la carrera entre el hilo que lee y el que
   sirve el HTTP) por un beneficio de latencia (segundos vs. 2.5s de
   polling) que no cambia la experiencia de forma perceptible. (B) es
   mecánicamente más simple, cross-platform sin trucos, y reusa
   infraestructura de polling que ya existe y ya se probó. **Esto es una
   decisión real de diseño, no un detalle — la reviso con las dos voces
   antes de comprometerme.**

3. **Riesgo de seguridad, ya identificado por el propio proyecto
   (`docs/PLAN-2026-08-22.md`, T2.2): el filtro de credenciales
   (`dispatcher/loop.py:limpiar_salida`, línea 148) corre hoy sobre el
   RESUMEN final, no sobre el stream.** Un stream crudo (cualquiera de las
   dos arquitecturas) puede mandar al navegador un `.env` que el agente
   leyó a mitad de la corrida, antes de que `limpiar_salida` tenga
   oportunidad de actuar. **Con la opción (B), el filtro se aplica en cada
   lectura del archivo (antes de responder al polling), nunca sobre el
   archivo crudo.** Riesgo adicional propio de leer en pedazos: una
   credencial partida exactamente en el borde de dos lecturas puede evadir
   un filtro por patrones — a resolver en la tarea (relectura con
   solapamiento, ver T1 abajo).

4. **`_correr` no conoce `task_id`.** Lo llama `run_backend` (`backends.py:
   604`), que a su vez lo llama `loop.ejecutar_una` (que SÍ tiene
   `task_id`) y también `run_chat` (que NO tiene un `task_id` de kanban —
   es una conversación, no un nodo). El archivo temporal de la opción (B)
   no puede vivir keyed por `task_id` dentro de `backends.py` sin ensuciar
   una función que hoy es agnóstica de kanban — tiene que ser el LLAMADOR
   (`loop.ejecutar_una`) quien decida la ruta del archivo y se la pase a
   `_correr` como parámetro opcional, no que `_correr` la invente.

5. **Lecciones por repositorio: el punto de inyección ya existe y no hay
   que tocarlo.** `compiler/compile.py:270-281` ya concatena
   `grafo["reglas"]` al `body` de CADA nodo al compilar (`ENCABEZADO_
   REGLAS`). Las lecciones por repo son el MISMO mecanismo, una fuente más:
   un archivo en el workspace del nodo (`<workspace>/.orquester-
   lecciones.md`, a confirmar el nombre en la revisión), leído en el mismo
   punto y concatenado igual. No hay nodo `hermes` sin workspace que
   pueda tener lecciones (coherente con el hallazgo del visor de diff:
   sin `workspace` explícito, no hay carpeta que identifique "este repo").

6. **Cuidado ya anotado en el backlog original**: "cuidado con que crezca
   sin techo" — el archivo de lecciones necesita un cap de tamaño al
   leerlo (mismo criterio que el resto del proyecto: 400/2000 chars según
   el caso, a decidir el número exacto en la tarea) para no inflar el
   contexto del agente sin límite.

## 1. Lo que ya existe y no hay que reconstruir

| Pieza | Dónde | Nota |
|---|---|---|
| Filtro de credenciales | `dispatcher/loop.py:limpiar_salida` | se reusa, no se reescribe — aplicado por chunk en vez de al final |
| Polling del lado cliente | `ui/index.html:refrescarEstado()` (cada 2.5s) | patrón a reusar para el terminal, no inventar SSE nuevo |
| Inyección de reglas al `body` | `compiler/compile.py:270-281` | mismo mecanismo, una fuente más (lecciones) |
| Endurecimiento de lectura de archivos ajenos | `ui/server.py:_git`/`_sin_filtros` (visor de diff) | referencia de criterio, no aplica directo (esto no es git) pero el patrón de "cap de tamaño + degradar con gracia si no existe" sí |
| Registro de procesos activos | `dispatcher/backends.py:registrar_proceso`/`desregistrar_proceso` | ya rastrea el `Popen`, útil para saber si un archivo de terminal sigue "vivo" |

## 2. Alcance de este plan

**Adentro:**
- **T1 (terminal en vivo)**: opción (B) — archivo temporal por corrida +
  endpoint de polling con filtro de credenciales aplicado en cada lectura.
  Panel en la UI (pestaña "Nodo" o "Traza") que hace polling mientras el
  nodo está `running`.
- **T2 (lecciones por repositorio)**: lectura de
  `<workspace>/.orquester-lecciones.md` al compilar, concatenada a `reglas`
  con el mismo mecanismo existente. Panel simple en la UI para ver/editar
  el archivo del workspace del nodo seleccionado.

**Afuera, a propósito:**
- Inferencia automática de lecciones a partir de fallos (analizar por qué
  falló un nodo y sugerir una lección) — requiere un agente extra
  analizando, mucho más caro y con su propio riesgo de alucinar reglas
  falsas. Manual por ahora.
- Streaming real por threads (opción A) — descartada por esta pasada, ver
  Premisa 2. Si (B) resulta insuficiente en uso real, es una revisión
  futura con datos reales de por qué no alcanzó.
- Terminal en vivo para nodos `hermes` (sin `task_id` en nuestro
  dispatcher) — ese runtime lo ejecuta el dispatcher NATIVO de Hermes, en
  otro proceso; esta pasada cubre `orquester-external:*` únicamente.

## 3. Diagrama

```
   T1 — Terminal en vivo
   ┌──────────────────────┐
   │ loop.ejecutar_una()    │  ya tiene task_id
   │  arma ruta_terminal =  │
   │  workspaces_root/.../  │
   │  <task_id>.term.log    │
   └──────────┬─────────────┘
              │ pasa la ruta como parametro nuevo
              ▼
   ┌──────────────────────┐
   │ backends._correr()     │  stdout/stderr -> archivo,
   │ (parametro nuevo,       │  no PIPE. Communicate() sigue
   │  opcional, no rompe     │  existiendo para el join final.
   │  el llamador de chat)   │
   └──────────────────────┘
              │
              ▼
   ┌──────────────────────┐        ┌────────────────────┐
   │ nuevo endpoint GET     │◄───────│ ui/index.html        │
   │ /api/nodo/terminal      │        │ polling cada ~2s      │
   │ lee el archivo desde     │        │ mientras el nodo       │
   │ un offset, filtra con    │        │ esta 'running'          │
   │ limpiar_salida, devuelve │        └────────────────────┘
   │ {texto, offset_nuevo}    │
   └──────────────────────┘

   T2 — Lecciones por repositorio
   ┌──────────────────────┐
   │ compiler/compile.py     │  al compilar, por nodo con
   │ :270-281 (ya existe)    │  workspace: lee <workspace>/
   │                           │  .orquester-lecciones.md
   │                           │  (cap de tamano), concatena
   │                           │  a reglas -- MISMO mecanismo
   └──────────────────────┘

   Sin tocar: dispatcher/loop.py (fuera del nuevo parametro opcional),
   ui/server.py salvo el endpoint nuevo, apps/api/.
```

## 4. Tareas

### T1.1 · `_correr` acepta una ruta de archivo opcional para stdout/stderr
- **Qué**: nuevo parámetro `archivo_terminal: Path = None` en `_correr`.
  Si se pasa, `stdout`/`stderr` del `Popen` van a ese archivo (abierto en
  modo texto, `errors="replace"`) en vez de `PIPE` — el resto de la
  función (timeout, `registrar_proceso`, `CompletedProcess` final) no
  cambia: al terminar, se lee el archivo completo para devolver
  `stdout`/`stderr` como antes (compatibilidad total con `run_backend`).
- **Riesgo**: mezclar stdout/stderr en un solo archivo simplifica pero
  pierde la distinción que el resto del código usa (`proc.stderr` para
  logueo de error) — **a decidir en la revisión**: un archivo por stream,
  o uno combinado con prefijo por línea.
- **Test**: un proceso que escribe progresivamente (`time.sleep` entre
  prints) y se lee el archivo A MITAD de la ejecución — confirma que hay
  contenido parcial antes de que el proceso termine.

### T1.2 · `loop.ejecutar_una` arma la ruta y la pasa
- **Qué**: reusa `workspaces_root` (ya existe, `kanban_db`) o un
  directorio propio para los logs de terminal, nombrado por `task_id`.
  Solo para nodos `orquester-external:*` (Premisa 6).
- **Riesgo**: limpieza — el archivo no puede crecer para siempre en disco.
  A definir: ¿se borra al terminar el nodo, o sobrevive para post-mortem
  como el workspace? (mismo criterio que `_limpiar_workspaces` — a
  confirmar en la revisión).

### T1.3 · Endpoint de polling con filtro por chunk
- **Qué**: `GET /api/nodo/terminal?board=&task=&offset=` — lee el archivo
  desde `offset`, aplica `limpiar_salida` al pedazo nuevo, devuelve
  `{texto, offset_nuevo}`. El cliente guarda `offset_nuevo` y lo manda en
  el próximo pedido (mismo patrón que un `tail -f` a demanda).
- **Riesgo de seguridad (Premisa 3)**: una credencial partida en el borde
  de dos lecturas evade el filtro por patrones. Mitigación: releer con un
  solapamiento fijo hacia atrás (ej. los últimos N bytes ya servidos se
  vuelven a filtrar junto con lo nuevo, se descarta el solapamiento antes
  de mandar) — **a definir el N exacto en la revisión**.
- **Test**: una credencial que cae exactamente en el borde de dos
  lecturas de a K bytes — confirmar que el filtro la agarra igual.

### T1.4 · Panel de terminal en la UI
- **Qué**: en la pestaña "Nodo" o "Traza", un `<pre>` que hace polling a
  T1.3 cada ~2s mientras `estado === "running"`, se detiene solo al
  cambiar de estado.
- **Test**: Playwright, mockeado — confirma que el polling arranca en
  `running` y se detiene al pasar a `done`/`blocked`.

### T2.1 · Lectura de lecciones al compilar
- **Qué**: en `compiler/compile.py`, junto a la lectura de `reglas`
  (línea 270), para cada nodo con `workspace`: si existe
  `<workspace>/.orquester-lecciones.md`, leerlo (capado), concatenarlo
  DESPUÉS de `reglas` con su propio encabezado (`## Lecciones de este
  repositorio`).
- **Riesgo**: ninguno nuevo — mismo mecanismo que `reglas`, que ya está
  probado. El cap de tamaño es la única pieza nueva.
- **Test**: un nodo con workspace que tiene lecciones → aparecen en el
  `body`; un workspace sin el archivo → compila igual, sin error.

### T2.2 · Panel simple para ver/editar lecciones
- **Qué**: en el panel del nodo, si tiene `workspace`, un botón/textarea
  que lee y escribe `<workspace>/.orquester-lecciones.md` directo (no hay
  base de datos nueva — el archivo ES el dato, vive con el repo, es
  git-trackable si el usuario quiere versionarlo).
- **Test**: Playwright — abrir, editar, guardar, releer.

## 5. Qué NO resuelve este plan

- Inferencia automática de lecciones desde fallos.
- Streaming real por threads (opción A) — ver Premisa 2.
- Terminal en vivo para nodos `hermes` nativos.
- Lecciones compartidas entre workspaces distintos del mismo repo lógico
  (ej. dos checkouts del mismo repo en rutas distintas) — el archivo es
  por ruta, no por identidad de repo (hash de remote, etc.).
