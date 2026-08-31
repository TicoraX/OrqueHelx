# Plan: tipar `BackendError` y afinar el backoff — 2026-08-31 (segunda tanda)

Origen: pedido del usuario de un plan nuevo para seguir mejorando el sistema,
tras cerrar el plan de uso diario (D1/D3/D2b, PR #3) y el spike D0. Construye
directo sobre D3: el propio comentario `ponytail:` que dejé en
`dispatcher/loop.py:66-68` señala esto como el próximo paso.

## 0. Premisas (revisadas tras dual review — corregidas, no como se escribieron primero)

1. **Corrección de cita (hallazgo crítico de la revisión CEO, confirmado):**
   "T2.3 · Fallback de runtime por 429" y "T2.4 · Enrutador económico" NO
   están en `IDEAS.md` — viven en `docs/PLAN-2026-08-22.md:204,209`, un plan
   anterior. Los confundí al escribir la primera versión de este documento.
   No cambia la decisión de fondo (el fallback por 429 sigue siendo una idea
   real, esté citada donde esté), pero la premisa original citaba mal la
   fuente y queda corregida acá.
2. **Un 429 NO se puede detectar de forma confiable hoy.**
   `dispatcher/backends.py:638-639`: `run_backend` deliberadamente NO mira
   `proc.returncode` ("`agy -p` sale 0 aunque falle, SS4.1, verificado"). La
   única señal disponible sería sniffear texto de `stderr`/`stdout` — y este
   proyecto **ya se quemó dos veces** con clasificación de errores por
   substring del mensaje (`ARQUITECTURA.md` §11, documentado también en el
   docstring de `BackendError`). Construir el fallback por 429 ahora sería
   repetir ese patrón a sabiendas. Sigue fuera de alcance.
3. **Nuevo, de la revisión CEO (hallazgo crítico #2): el heurístico de
   dirección del backoff que proponía la primera versión puede estar
   INVERTIDO para el caso que importa.** Un 429 real probablemente no cae en
   "timeout" (el proceso no se cuelga, responde rápido) sino en el `except
   Exception` genérico de `backends.py:647-654` — el mismo bucket que un
   fallo de parseo normal. La primera versión de este plan le asignaba
   backoff MÁS CORTO a ese bucket ("probablemente un bug de formato, vale
   reintentar rápido") — que es lo opuesto de lo correcto si en realidad es
   un rate limit. **Esta premisa cambia el alcance de E2**: no se decide una
   dirección de backoff sin datos reales. Ver sección 5, E2 reducido a solo
   observacional.
4. **Nuevo, de la revisión de ingeniería: `BLOCK_RECURRENCE_LIMIT=2` recorta
   el alcance práctico de cualquier backoff diferenciado.** Con
   `MAX_INTENTOS=2` igual al límite de recurrencia del kanban, el SEGUNDO
   fallo con el mismo `block_kind` rutea directo a `triage` (`kanban_db.py:
   6284-6303`) — nunca llega a una segunda ventana de `reintentar()`. Un
   backoff diferenciado por causa solo puede afectar la decisión sobre el
   PRIMER reintento. Confirma que actuar sobre la dirección sin evidencia
   (premisa 3) es un riesgo que no vale la pena correr por un efecto tan
   acotado.

## 1. Lo que ya existe y no hay que reconstruir

| Pieza | Dónde | Nota |
|---|---|---|
| `BackendError`/`ErrorPermanente` | `backends.py:146-162` | ya distingue reintentable de no — el docstring YA explica por qué el tipo va en quien lo levanta, no en un parser de texto downstream |
| 7 sitios que levantan `ErrorPermanente` en `backends.py` | `388, 442, 444, 519, 554, 581, 612` | cada uno sabe POR QUÉ es permanente; ninguno necesita `causa` fina |
| **2 sitios que levantan `BackendError` genérico mal clasificados** | `loop.py:215` (`carril()`), `loop.py:222` (`_runtime_de()`) | **bug real, no documentado antes de esta revisión — ver E0** |
| `runs[-1].summary` ya cargado | `loop.py:435` (D3) | `reintentar()` ya lee `Run.summary` para el backoff — leer `causa:` desde ahí no agrega query |
| Timeout de proceso | `backends.py:588` (`except subprocess.TimeoutExpired`) | caso estructuralmente distinguible — hoy se pierde esa información al envolver en `BackendError` genérico |

## 2. Alcance de este plan

**Adentro:**
- **E0**: corregir `loop.py:215/222` — dos sitios que levantan `BackendError`
  genérico para errores estructuralmente permanentes, hoy reintentados sin
  sentido. Bug real, lo encontró la revisión de ingeniería.
- **E1**: `BackendError` gana un atributo `causa` (string corto:
  `"timeout" | "configuracion" | "parseo" | "desconocido"`), seteado en cada
  sitio que YA sabe por qué falló, y se persiste como prefijo del `reason`
  que ya viaja a `Run.summary`. **Solo observacional esta pasada** — visible
  en la traza del nodo, no cambia ningún comportamiento de retry todavía.

**Afuera, a propósito:**
- **E2 (backoff diferenciado por causa)** — cortado tras la revisión dual.
  Ver Premisas 3 y 4.
- **Fallback de runtime por 429** (de `docs/PLAN-2026-08-22.md`, ver
  Premisa 1) — ver Premisa 2. Queda documentado como bloqueado, no como
  hecho a medias.
- **Refactor de la tabla de rutas de `ui/server.py`** (34 endpoints, dos
  cadenas de `if`, deuda que ya está anotada en `ESTADO.md`) — es deuda de
  mantenibilidad real pero ortogonal a esto (no toca error handling ni
  backoff). Se deja para su propio plan si el usuario lo pide; mezclarlo acá
  infla el diff sin compartir riesgo con E1/E2.
- **Enrutador económico** (T2.4 de IDEAS.md) — depende de que exista una
  política, y IDEAS.md ya avisa del riesgo de que sea "una adivinanza
  disfrazada". No se toca.

## 3. Diagrama: qué toca cada tarea

```
              ┌────────────────────────┐
              │   backends.py           │
              │  BackendError.causa     │  <- E1: timeout/parseo
              │  (nuevo atributo)        │     E0: loop.py:215/222
              └───────────┬──────────────┘     -> ErrorPermanente
                          │
                          │ la excepcion viaja hasta ejecutar_una()
                          ▼
              ┌────────────────────────┐
              │   loop.py                │
              │  ejecutar_una() :377     │  <- E1: causa:X| como
              │  reason = causa:X|msg    │     prefijo del reason
              │  -> block_task(reason)   │     que ya viaja a
              │                          │     Run.summary
              │  reintentar() :435       │  <- E1: parsea causa
              │  ya carga runs[-1] --    │     desde runs[-1]
              │  lee, NO actua sobre     │     .summary, la deja
              │  ella (E2 cortado)       │     visible, no la usa
              └────────────────────────┘

  Sin tocar: ui/server.py, ui/index.html, compiler/, apps/api/, block_kind
  (VALID_BLOCK_KINDS es un set cerrado, no admite valores compuestos).
  `_traza` en server.py ya expone `Run.summary` -- causa se ve gratis en
  la traza del nodo sin tocar la UI.
```

## 4. Dónde vive `causa` — resuelto por la revisión de ingeniería

La hipótesis original ("probablemente el `result`/motivo que ya se guarda al
bloquear") era **falsa**: `Task.result` solo se escribe en `complete_task`,
nunca en `block_task` (`kanban_db.py:1073`). Verificado contra el código
real:

- El texto de bloqueo (`reason`) se persiste como `Run.summary`, vía
  `_end_run(..., summary=reason)` (`kanban_db.py:6249,6307,6361`) — una fila
  de `task_runs`, no un campo de `Task`.
- **`reintentar()` YA carga esos runs** (`loop.py:435`, para contar
  intentos y leer `ended_at` del backoff de D3) — parsear `causa:` desde
  `runs[-1].summary` ahí mismo **no agrega ninguna query nueva**.
- Dos alternativas descartadas, con motivo:
  - `block_kind` — no sirve: `VALID_BLOCK_KINDS` es un set cerrado
    (`kanban_db.py:125`) y `block_task` rechaza cualquier valor que no
    matchee exacto. No admite un kind compuesto tipo `"transient:timeout"`.
  - `Task.last_failure_error` — más atractivo a primera vista (campo directo
    de `Task`, cero query extra) pero es **de otro subsistema**: lo escribe
    el mecanismo nativo de crash/quota-respawn de Hermes
    (`kanban_db.py:8931,8944,9152`) y `check_respawn_guard` lo lee con sus
    propias regexes de cuota. Escribir ahí arriesga colisionar con ese
    parser ajeno y disparar comportamiento de Hermes no relacionado.
- **Punto de cableado exacto donde `causa` se pierde si no se toca a mano**:
  `loop.py:377`, `msg = limpiar_salida(str(e))[:2000]` — `str(e)` de un
  `RuntimeError` es solo el mensaje, no lleva el atributo `.causa` aparte.
  Hace falta algo como `f"causa:{e.causa}|{msg}"` explícito ANTES de pasarlo
  a `block_task(..., reason=...)`.

## 5. Tareas

### E0 · Bug real encontrado por la revisión (nuevo, no estaba en la v1 del plan)
- **Qué**: `loop.py:215` (`carril()`) y `loop.py:222` (`_runtime_de()`)
  levantan `BackendError` genérico — no `ErrorPermanente` — para un runtime
  desconocido o un `assignee` mal formado. Con `permanente=False` por
  default, hoy se clasifican `transient` y **se reintentan sin sentido**: un
  runtime que no existe no se va a arreglar solo en el segundo intento.
  Cambiar a `raise ErrorPermanente(..., causa="configuracion")` (E1 define
  `causa` primero, este bug se corrige con el mismo cambio).
- **Por qué entra a este plan**: mismo archivo, mismo tipo de excepción, lo
  encontró la misma revisión — separarlo en otro plan es más trabajo que
  arreglarlo acá.
- **Riesgo**: bajo — achica el universo de "transient" a lo que
  genuinamente es transitorio, no lo agranda.
- **Test**: un caso en `tests/test_concurrencia_reintentos.py` — un
  `assignee` mal formado o runtime desconocido bloquea como `capability`,
  no `transient`, y no se reintenta.

### E1 · `causa` en `BackendError`, persistida, SOLO observacional en esta pasada
- **Qué**:
  1. Agregar `causa: str = "desconocido"` como atributo de instancia en
     `BackendError`, seteado en los sitios que ya distinguen el motivo:
     `backends.py:588` (`TimeoutExpired`) → `"timeout"`; `backends.py:
     652-654` (parseo/validación) → `"parseo"`; `loop.py:215/222` (E0) →
     `"configuracion"`.
  2. Cablear el punto exacto donde se pierde (`loop.py:377`): el `reason`
     que viaja a `block_task` pasa a ser `f"causa:{causa}|{msg}"` cuando la
     excepción trae `.causa` (con `getattr(e, "causa", None)` para no
     romper si en algún punto se levanta un `BackendError` sin el atributo).
  3. `reintentar()` parsea `causa:` desde `runs[-1].summary` (ya cargado,
     sin query nueva) y la deja disponible — **pero no la usa todavía para
     nada** (ver E2).
- **Por qué solo observacional**: la Premisa 3 (heurístico de dirección
  posiblemente invertido) y la Premisa 4 (`BLOCK_RECURRENCE_LIMIT` recorta
  el efecto a un solo reintento) hacen que actuar sobre `causa` sin datos
  reales sea más riesgo que beneficio. Exponerla es barato y de valor
  inmediato (se ve en la traza del nodo, `_traza` en `server.py` ya expone
  los `summary` de cada run); decidir qué hacer con ella espera a tener
  fallos reales para mirar.
- **Riesgo**: bajo. `causa` es un prefijo nuevo en un string que ya pasa por
  `limpiar_salida()` y se trunca a 2000 chars — el prefijo va al PRINCIPIO,
  antes del truncamiento, así que nunca se corta a la mitad.
- **Test**: extender `test_concurrencia_reintentos.py` — un timeout deja
  `causa="timeout"` legible en `runs[-1].summary` después de bloquear, un
  fallo de parseo deja `"parseo"`, y el truncamiento a 2000 chars no corrompe
  el prefijo (mensaje de error largo a propósito en el test).

### E2 · Backoff diferenciado por causa — CORTADO de esta pasada
No se implementa. Motivo: Premisas 3 y 4. Sin datos reales de qué pinta
tiene un 429 en cada CLI (`claude`/`opencode`/`agy`), fijar una dirección de
backoff por `causa` puede empeorar exactamente el caso que se quiere
mejorar, y el efecto práctico está acotado a un solo reintento por el
`BLOCK_RECURRENCE_LIMIT`. Queda para cuando E1 (observacional) junte
suficientes fallos reales como para decidir con evidencia, no con intuición.

## 6. Qué NO resuelve este plan

- Fallback de runtime por 429 — bloqueado por la Premisa 2, no por falta de
  tiempo. Necesita investigación empírica de la señal real de cada CLI
  (`claude`, `opencode`, `agy`) contra un rate limit de verdad, que no se
  puede fabricar desde acá.
- Backoff diferenciado por causa (E2) — cortado, ver sección 5. Espera a
  tener datos reales.
- Refactor de rutas de `ui/server.py` — deuda real, documentada, fuera de
  alcance de este plan por ser ortogonal (confirmado por la revisión CEO:
  "no es timidez, es disciplina correcta").
- Enrutador económico (de `docs/PLAN-2026-08-22.md`, no de `IDEAS.md` — ver
  Premisa 1) — sin política definida, riesgo de heurística disfrazada.

## 7. Registro de decisiones (post revisión dual)

| # | Decisión | Origen | Resolución |
|---|---|---|---|
| 1 | Corregir la cita: T2.3/T2.4 son de `docs/PLAN-2026-08-22.md`, no de `IDEAS.md` | CEO (crítico) | Auto-decidido — corrección de hecho |
| 2 | `causa` vive en `Run.summary` (prefijo en `reason`), no en `Task.result` ni `block_kind` ni `last_failure_error` | Eng (crítico + alto) | Auto-decidido — la única opción viable, verificada línea por línea |
| 3 | Agregar E0 (bug real: `loop.py:215/222` mal clasificados como transient) | Eng (alto) | Auto-decidido — mismo archivo, mismo cambio, no vale separarlo |
| 4 | Cortar E2 (backoff diferenciado) de esta pasada, dejar `causa` solo observacional | CEO (crítico) + Eng (medio, `BLOCK_RECURRENCE_LIMIT`) | Auto-decidido — dos revisiones independientes coinciden en que actuar sin datos es más riesgo que beneficio |
| 5 | Cablear `causa` explícitamente en `loop.py:377` (`f"causa:{causa}|{msg}"`) | Eng (medio) | Auto-decidido — es el punto exacto donde se pierde si no se toca |
| **6** | **`causa` como atributo string simple vs. subclases de `BackendError` (`BackendErrorTimeout`, etc., siguiendo el patrón de `ErrorPermanente`)** | **CEO (medio)** | **Sin decidir — ver pregunta al usuario abajo** |

**Estado: E0 y E1 (observacional) listos para implementar tras la decisión 6.
E2 no se implementa en esta pasada.**
