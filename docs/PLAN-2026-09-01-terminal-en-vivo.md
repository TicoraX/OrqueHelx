# Plan: terminal en vivo por nodo — 2026-09-01

Origen: split de `docs/PLAN-2026-09-01-terminal-y-lecciones.md` — la
revisión CEO marcó que esto (T1) es la parte compleja/riesgosa del plan
combinado y no debería bloquear la ganancia simple (lecciones, plan
separado). Este plan va después, en su propio PR.

## 0. Premisas (investigadas antes de escribir el plan; corregidas por
   dos revisiones + un spike empírico)

1. **Hoy no hay nada en vivo del contenido de un nodo mientras corre.**
   `dispatcher/backends.py:_correr` (línea 558) usa `proc.communicate(timeout=
   timeout)` — bufferea TODO stdout/stderr y recién devuelve algo cuando el
   proceso termina. `/api/eventos` (SSE) ya existe pero informa cambios de
   *estado* (`_estado()`), no el output crudo del agente.

2. **Decisión de arquitectura central: (B) archivo + polling, no (A)
   threads lectores.** `select()`/`selectors` sobre pipes no es confiable
   en Windows (este proyecto corre en Windows y en el contenedor Linux de
   CI) — la alternativa portable de (A) son threads lectores, que agregan
   sincronización real por un beneficio de latencia que no cambia la
   experiencia de forma perceptible. (B) reusa el patrón de polling que ya
   existe (`refrescarEstado()` en el cliente).

   **Validado con un spike empírico (sin costo, sin invocar agentes
   reales) antes de comprometerse**: la duda de la revisión de ingeniería
   era si un proceso hijo cambia a buffering por bloques al redirigir su
   stdout a un archivo en vez de a una terminal — lo que dejaría el
   "polling" sin mostrar nada hasta que el proceso termine.
   - Python sin `-u`: confirmado que SÍ bufferea todo (`[0, 0, 0, 0, 45,
     45]` — nada hasta el final). El riesgo es real en general.
   - **Node.js `console.log` redirigido a archivo: NO bufferea por
     bloques** — crecimiento progresivo confirmado (`[16, 24, 32, 40, 40,
     40]` bytes cada 0.5s, contenido correcto línea por línea). Esto
     cubre `opencode` (confirmado Node.js vía su wrapper npm) y
     probablemente `claude` (binario PE, con alta probabilidad
     empaquetado sobre Node/bun como el resto de CLIs de Anthropic,
     aunque esto último no está 100% confirmado por no poder inspeccionar
     el bundle).
   - **`agy` (binario nativo PE32+, no Node/Python) queda sin verificar.**
     Probarlo requeriría una invocación real y paga, no autorizada. Se
     acepta como riesgo residual (ver Premisa 7).

3. **Riesgo de seguridad, ya identificado por el propio proyecto: el
   filtro de credenciales (`dispatcher/loop.py:limpiar_salida`, línea 148)
   corre hoy sobre el RESUMEN final, no sobre el stream.** Un stream
   crudo puede mandar al navegador un `.env` que el agente leyó a mitad de
   la corrida, antes de que `limpiar_salida` tenga oportunidad de actuar.
   Con la opción (B), el filtro se aplica en cada lectura del archivo
   (antes de responder al polling), nunca sobre el archivo crudo. Riesgo
   adicional propio de leer en pedazos: una credencial partida
   exactamente en el borde de dos lecturas puede evadir un filtro por
   patrones — resuelto en T1.3 con relectura solapada.

4. **`_correr` no conoce `task_id`, y `run_backend` es la capa
   intermedia que faltaba en la primera versión de este plan (corregido
   por la revisión de ingeniería).** La cadena real es:
   `loop.ejecutar_una(conn, task_id, ...)` (tiene `task_id`) →
   `run_backend(runtime, goal, ...)` (`backends.py:604`, NO tiene
   `task_id`) → `_correr(runtime, argv, ...)` (`backends.py:558`, NO
   tiene `task_id`). El parámetro nuevo tiene que atravesar las TRES
   funciones, no solo `_correr` — `run_backend` necesita el mismo
   parámetro opcional para pasarlo hacia abajo.

5. **`_correr` no puede tratar el parámetro como un simple default
   opcional (corregido por la revisión de ingeniería) — necesita una
   rama condicional real.** Hoy usa `subprocess.Popen(..., stdout=PIPE,
   stderr=PIPE, ...)` seguido de `proc.communicate(timeout=timeout)`. Si
   `stdout`/`stderr` se abren como archivos en vez de `PIPE`,
   `communicate()` devuelve `(None, None)` para esos streams — el código
   que arma el `CompletedProcess` final tiene que leer el archivo en ese
   caso, no asumir que `communicate()` le da el texto. El manejo de
   `except subprocess.TimeoutExpired` (líneas ~595-601) también cambia:
   hoy usa lo que alcanzó a leer del pipe antes del timeout, con archivo
   tiene que leer lo que hay escrito hasta ese momento.

6. **Ubicación del archivo de log de terminal: dentro de
   `workspaces_root(board)/<task_id>/` (recomendación concreta de la
   revisión de ingeniería, adoptada).** `ui/server.py:_limpiar_workspaces`
   ya limpia ese árbol completo con un botón manual explícito y lo
   preserva post-mortem por defecto — poner el log ahí lo cubre gratis,
   sin lógica de limpieza nueva ni riesgo de dejarlo huérfano en otro
   lado.

7. **Riesgo residual aceptado: `agy` sin verificar (Premisa 2).** Si su
   buffering resulta ser por bloques, la degradación es benigna: el panel
   de terminal para nodos `agy` se ve "a saltos" (aparece todo junto más
   tarde) en vez de progresivo — no rompe nada, no hay pérdida de datos.
   Revisar con datos reales la primera vez que se use en producción con
   ese runtime.

## 1. Lo que ya existe y no hay que reconstruir

| Pieza | Dónde | Nota |
|---|---|---|
| Filtro de credenciales | `dispatcher/loop.py:limpiar_salida` | se reusa, no se reescribe — aplicado por chunk en vez de al final |
| Polling del lado cliente | `ui/index.html:refrescarEstado()` | patrón a reusar para el terminal, no inventar SSE nuevo |
| Directorio de workspace por tarea | `ui/server.py:_limpiar_workspaces`, `workspaces_root(board)/<task_id>` | el log de terminal vive ahí (Premisa 6) |
| Registro de procesos activos | `dispatcher/backends.py:registrar_proceso`/`desregistrar_proceso` | ya rastrea el `Popen`, útil para saber si un archivo de terminal sigue "vivo" |

## 2. Alcance

**Adentro**: terminal en vivo (opción B) — archivo temporal por corrida +
endpoint de polling con filtro de credenciales aplicado en cada lectura +
panel en la UI que hace polling mientras el nodo está `running`.

**Afuera, a propósito:**
- Streaming real por threads (opción A) — descartada, ver Premisa 2.
- Terminal en vivo para nodos `hermes` nativos (sin `task_id` en nuestro
  dispatcher, otro proceso).
- Verificación en vivo del buffering de `agy` con una invocación real
  paga — riesgo residual aceptado (Premisa 7).

## 3. Diagrama

```
   ┌──────────────────────┐
   │ loop.ejecutar_una()    │  ya tiene task_id
   │  arma ruta_terminal =  │
   │  workspaces_root/board/│
   │  <task_id>/terminal.log│
   └──────────┬─────────────┘
              │ pasa la ruta como parametro nuevo
              ▼
   ┌──────────────────────┐
   │ backends.run_backend() │  capa intermedia -- recibe y
   │ (parametro nuevo,       │  reenvia el parametro, no lo usa
   │  opcional, default None)│  directamente
   └──────────┬─────────────┘
              ▼
   ┌──────────────────────┐
   │ backends._correr()     │  rama condicional real:
   │ stdout/stderr -> archivo│  con archivo, communicate() da
   │ si se paso el parametro,│  (None, None) -- leer el archivo
   │ PIPE si no (compat total│  para armar CompletedProcess
   │ con run_chat, que no lo │  final. Timeout: leer lo escrito
   │ pasa)                    │  hasta el corte.
   └──────────────────────┘
              │
              ▼
   ┌──────────────────────┐        ┌────────────────────┐
   │ nuevo endpoint GET     │◄───────│ ui/index.html        │
   │ /api/nodo/terminal      │        │ polling cada ~1s      │
   │ lee el archivo desde     │        │ mientras el nodo       │
   │ un offset, filtra con    │        │ esta 'running'          │
   │ limpiar_salida (con       │        └────────────────────┘
   │ solapamiento en el borde),│
   │ devuelve {texto, offset}  │
   └──────────────────────┘

   Sin tocar: dispatcher/loop.py (fuera del nuevo parametro opcional),
   ui/server.py salvo el endpoint nuevo, apps/api/.
```

## 4. Tareas

### T1.1 · `_correr` acepta una ruta de archivo opcional para stdout/stderr
- **Qué**: nuevo parámetro `archivo_terminal: Path | None = None` en
  `_correr`. Si se pasa: `Popen(..., stdout=open(archivo_terminal, "w",
  encoding="utf-8", errors="replace"), stderr=subprocess.STDOUT, ...)` —
  un solo archivo combinado (más simple que separar stdout/stderr, y el
  panel de terminal no necesita distinguirlos). `communicate(timeout=
  timeout)` devuelve `(None, None)` en ese caso — leer el archivo después
  de que el proceso termina para armar `CompletedProcess.stdout` (compat
  total con `run_backend`/`run_chat`, que no pasan el parámetro).
- **Riesgo (Premisa 5)**: el manejo de `except subprocess.TimeoutExpired`
  cambia — con archivo, leer lo escrito hasta el corte en vez de lo que
  `communicate()` alcanzó a devolver del pipe.
- **Test**: (a) un proceso que escribe progresivamente (`time.sleep`
  entre prints) y se lee el archivo A MITAD de la ejecución — confirma
  contenido parcial antes de que el proceso termine; (b) correr en
  Windows y en el contenedor Linux de CI — el mecanismo de archivo es
  idéntico en ambos, pero verificar explícitamente evita sorpresas de
  encoding/newlines.

### T1.2 · `run_backend` reenvía el parámetro; `loop.ejecutar_una` arma la ruta
- **Qué**: `run_backend` gana el mismo parámetro opcional y lo reenvía a
  `_correr` sin usarlo (Premisa 4). `loop.ejecutar_una` arma
  `workspaces_root(board) / task_id / "terminal.log"` (Premisa 6) y lo
  pasa. Solo para nodos `orquester-external:*`. `run_chat` no pasa nada
  (default `None`, sin cambio de comportamiento).
- **Test**: un nodo `orquester-external:*` corrido de punta a punta deja
  `terminal.log` en su carpeta de workspace con el contenido esperado; un
  `chat_backend` sigue funcionando sin tocar el parámetro.

### T1.3 · Endpoint de polling con filtro por chunk
- **Qué**: `GET /api/nodo/terminal?board=&task=&offset=` — lee el archivo
  desde `offset`, aplica `limpiar_salida` al pedazo nuevo MÁS un
  solapamiento fijo hacia atrás (los últimos N bytes ya servidos se
  releen junto con lo nuevo, se descarta el solapamiento antes de
  mandar), devuelve `{texto, offset_nuevo}`. N: 256 bytes (mayor que
  cualquier patrón de credencial razonable del filtro existente).
- **Test**: una credencial que cae exactamente en el borde de dos
  lecturas de a K bytes — confirmar que el filtro la agarra igual con el
  solapamiento.

### T1.4 · Panel de terminal en la UI
- **Qué**: en la pestaña "Nodo" o "Traza", un `<pre>` que hace polling a
  T1.3 cada ~1s (más agresivo que el `refrescarEstado()` de 2.5s general
  — la razón de ser de esta feature es sentirse "en vivo") mientras
  `estado === "running"`, se detiene solo al cambiar de estado.
- **Test**: Playwright, mockeado — confirma que el polling arranca en
  `running` y se detiene al pasar a `done`/`blocked`.

## 5. Qué NO resuelve este plan

- Streaming real por threads (opción A) — ver Premisa 2.
- Terminal en vivo para nodos `hermes` nativos.
- Verificación empírica del buffering de `agy` con una corrida real —
  riesgo residual aceptado, ver Premisa 7.
- Lecciones por repositorio — ver
  `docs/PLAN-2026-09-01-lecciones-por-repositorio.md`.
