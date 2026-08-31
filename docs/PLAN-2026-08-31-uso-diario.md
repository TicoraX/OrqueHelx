# Plan: ORQUESTER apto para uso diario — 2026-08-31

Origen: crítica pesimista pedida por el usuario sobre el estado del Studio y el
flujo operativo. Cruzado contra `IDEAS.md` (2026-08-22) para no repetir
decisiones ya tomadas ni backlog ya escrito.

## 0. Premisas (a confirmar antes de tocar código)

1. **"Uso diario" significa: una persona corre un flujo, lo deja andando y
   vuelve más tarde sin tener el Studio abierto todo el tiempo.** Si la
   premisa real es otra (ej. "uso diario" = un solo operador siempre mirando
   la pantalla), el orden de este plan cambia — D2 y D3 pierden prioridad.
2. **El motor multiusuario (`apps/api`, NestJS+Postgres) ya es la dirección
   elegida para gobierno de equipo** (IDEAS.md §1, "Gobierno de equipo").
   Este plan no reabre esa decisión, solo pregunta cuánto cuesta conectarlo
   al Studio hoy.
3. **No se reabre nada marcado "descartado" en IDEAS.md** (OTel, dashboard de
   ahorro, calculadora predictiva) — motivo ya escrito y sigue vigente.

## 1. Lo que ya existe y no hay que reconstruir

| Necesidad | Ya existe en | Falta |
|---|---|---|
| Canal de eventos en vivo | `/api/eventos` (SSE, `ui/server.py`) | consumidor en la UI (T2.2 de IDEAS.md, hallazgo lateral) |
| Reintento manual de nodo | `_reintentar_nodo` (`server.py:862-881`) | nada — es el botón, ya funciona |
| **Reintento automático de nodo** | **`loop.reintentar()` (`dispatcher/loop.py:404-431`), corre en cada `tick()`, tope `MAX_INTENTOS=2` (`loop.py:58`), probado en `tests/test_concurrencia_reintentos.py:71-121`** | **backoff — hoy reabre inmediatamente en el próximo tick, sin espera** |
| Filtro de credenciales en salida | `limpiar_salida()` (`dispatcher/loop.py`) | aplicarlo *en* el stream, no solo al resumen final (advertencia ya escrita en IDEAS.md T2.2) |
| Auth/RBAC real | `apps/api` (NestJS, Postgres, VIEWER/EDITOR/OWNER) | el Studio no le habla — corre standalone contra el motor Python |
| Selector de tipo de nodo `espera`/validador | motor y CLI (`--validar`, `esperar_segundos`) | UI: `ui/index.html` no ofrece `espera` en el selector (IDEAS.md §6) |

## 2. Alcance de este plan (y lo que queda afuera)

**Adentro:** D1–D3 (Tier 1, uso diario inmediato). D4–D6 quedan documentados
como Tier 2 con su costo real, no se implementan en esta pasada salvo que el
usuario lo pida explícitamente después de ver el costo.

**Afuera, a propósito:**
- Reescribir el modelo de auth del Studio de cero (eso es D4, y es un cambio
  de arquitectura, no una feature — se documenta, no se hace de pasada).
- Todo lo que IDEAS.md ya descartó.
- Tier 3 de IDEAS.md (ramas condicionales, fan-out dinámico, sub-grafos) —
  no tiene relación con "uso diario", es superficie de producto nueva.

## 3. Diagrama: qué toca cada tarea

```
                    ┌─────────────────────┐
                    │   ui/server.py      │
                    │  (Studio, 1 usuario)│
                    └──────────┬──────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   D1: selector          D2: consumidor         D3: retry
   tipo=espera            SSE en UI              automático
   + checkbox              (toast/sonido          en dispatcher
   validador                al terminar)           /loop.py
        │                      │                      │
        ▼                      ▼                      ▼
  ui/index.html          ui/index.html          dispatcher/loop.py
  (form nodo)          (nuevo listener JS)      (tick, ya conoce
                                                  estado del nodo)
        │                      │                      │
        └──────────────────────┴──────────────────────┘
                               │
                    Sin tocar: apps/api, kanban_db,
                    compilador (D1-D3 no cambian el
                    contrato de datos, solo exponen
                    lo que ya existe)
```

Ningún ítem de D1–D3 toca `compiler/`, `apps/api/` ni el esquema de
`kanban_db` — por eso van en Tier 1: bajo riesgo, sin migración.

## 4. Tareas — Tier 1 (esta pasada)

### D1 · Exponer `espera` y el validador en el Studio
- **Qué**: agregar `espera` al `<select id="tipo">` de `ui/index.html` con
  su campo `esperar_segundos` (mismo molde que `#boxGate`, ya existe).
  Agregar checkbox de validador en el panel de ejecución que mande
  `validador` a `/api/correr` (el motor ya lo acepta).
- **Por qué Tier 1**: el motor y el CLI ya soportan esto. Es cablear, no
  construir. IDEAS.md ya lo dejó como "lo primero que hay que mirar".
- **Riesgo**: ninguno nuevo — reusa `#boxGate` como plantilla.
- **Test**: extender `test_contrato_ui.py` para verificar que `espera` y
  `validador` viajan en el payload de `/api/correr` cuando se marcan en la UI
  (Playwright, ya es el mecanismo usado para UI).

### D2 · Notificación de fin de corrida — REDISEÑADA tras revisión
**Corrección de hecho (revisión de ingeniería):** la afirmación original de
que `/api/eventos` "no lleva contenido de nodo" es falsa. `_estado()`
(`server.py:468-482`) arma `resumen = (t.result or "")[:400]` y ese resumen
viaja en cada evento SSE. La razón real por la que hoy no hay fuga de
credenciales es otra: `limpiar_salida()` corre en **escritura**
(`loop.py:364`, antes de `complete_task`), no en el stream. `/api/eventos`
no es el vector de riesgo; un futuro call site que escriba `result` sin
pasar por `limpiar_salida()` sí lo es. Documentar esa garantía correcta en
el código, no la incorrecta.

**Corrección de alcance (revisión CEO):** la Premisa 1 exige enterarse
"sin tener el Studio abierto". Un listener SSE en la pestaña no cumple eso
— muere si se cierra el navegador. D2 se parte en dos:

- **D2a (barato, Tier 1)**: listener SSE en `ui/index.html` sobre
  `/api/eventos` → `Notification` API si la pestaña sigue abierta en
  background. Vale igual, pero ya no es "la" solución, es el caso cómodo.
- **D2b (Tier 1, nuevo, decidido)**: webhook HTTP genérico configurable (URL
  en config, `POST` con estado — sirve para Slack/Discord/ntfy.sh/receptor
  propio sin dependencia nueva por servicio) disparado desde
  `dispatcher/loop.py` en las mismas transiciones de estado que ya dispara
  eventos SSE (`done`/`blocked`/`review`) — sin backend nuevo de verdad,
  reusa el punto donde el loop ya sabe que el estado cambió. Es la pieza que
  cumple la Premisa 1 de verdad.
- **Test D2a**: mockear `window.Notification` en el test de Playwright
  existente y verificar la llamada — no queda "manual", corre en CI.
- **Test D2b**: unit test sobre el disparo del webhook con un servidor HTTP
  de prueba (`http.server` local o mock), verificar que se llama en las
  transiciones correctas y no en otras.
- **Riesgo nuevo que D2b introduce**: `/api/eventos` bajo D2a multiplica su
  uso (antes nadie lo consumía desde la UI) — revisar el costo de varias
  pestañas abiertas a la vez sobre `_stream_eventos` (`server.py:1941-1973`,
  sin tope de conexiones concurrentes por board) antes de dar D2a por
  terminado.

### D3 · Backoff en el retry automático — REDUCIDA tras revisión
**Corrección de hecho (revisión de ingeniería):** el retry automático **ya
existe** — `loop.reintentar()` (`loop.py:404-431`) reintenta todo
`BackendError` con `permanente=False` en cada `tick()`, tope
`MAX_INTENTOS=2` (`loop.py:58`), probado (`test_concurrencia_reintentos.py`
líneas 71-121). D3 no construye retry automático de cero; **le agrega
backoff** — hoy reabre inmediatamente en el siguiente tick, sin espera.
- **Qué**: en `loop.reintentar()`, esperar antes de reabrir (ej. backoff
  exponencial acotado por `MAX_INTENTOS` que ya existe).
- **Riesgo pre-existente, no nuevo de D3**: un timeout de proceso colgado
  por un bug real (loop infinito del agente) hoy se reintenta igual que un
  timeout de red transitorio — ya es así en producción, D3 no lo empeora ni
  lo arregla. Separar "timeout por bug" de "timeout transitorio" requiere
  que `BackendError` tenga tipo (T2.3 de IDEAS.md) — queda fuera de D3.
- **Riesgo nuevo, real**: `_reintentar_nodo` (botón manual, `server.py:
  862-881`) y `loop.reintentar()` (automático) no comparten lock — si el
  dispatcher corre en un hilo y el usuario clickea retry sobre la misma
  card `transient` al mismo tiempo, ambos caminos pueden llamar
  `unblock_task` en paralelo. A diferencia de `_LOCK_ARRANQUE` (que sí
  existe para el arranque), acá no hay guard compartido. **Hay que agregar
  ese lock como parte de D3**, no es opcional — es el hallazgo de mayor
  severidad real que dejó la revisión de ingeniería.
- **Kill switch**: variable de entorno (ej. `ORQUESTER_RETRY_BACKOFF=0`)
  para desactivar el backoff en caliente sin revertir código, dado que
  D3 toca el dispatcher (mayor riesgo declarado del plan).
- **Antes de escribir código**: una consulta al historial del kanban —
  ¿qué fracción de los `blocked` pasados fueron timeout de proceso vs. otras
  causas? Si es una fracción chica, el orden de prioridad de D3 baja.
- **Test**: extender `tests/test_concurrencia_reintentos.py` con: (a) el
  backoff efectivamente espera entre reintentos, (b) el lock nuevo evita la
  carrera manual/automático sobre la misma card.

## 5. Tier 2 — documentado, no implementado en esta pasada

### D0 · Spike: correr `apps/api` una vez (media tarde, antes de costear D4)
La revisión CEO encontró que la Premisa 2 ("el motor multiusuario ya es la
dirección elegida") da por sentada una infraestructura que `ESTADO.md`
confirma que **nunca corrió** ("Postgres del plano de control: no"). Costear
D4 sin haberlo levantado una vez es una estimación a ciegas. `docker compose
up -d db` + correr un flujo mínimo contra `apps/api` — no implementa nada,
solo informa el costo real de D4 antes de decidir cuándo hacerlo.

### D4 · Conectar el Studio a la API multiusuario real
- **Costo real**: no es un cable, es un cambio de arquitectura — el Studio
  hoy asume un solo proceso motor + un solo token (`ui/server.py:198`).
  Conectarlo a `apps/api` implica: el Studio deja de hablarle al motor
  directo, pasa a ser cliente de la API NestJS, y el motor Python pasa a ser
  invocado *por* esa API, no por el Studio. Es rehacer la topología, no
  agregar un endpoint.
- **Por qué no ahora**: es exactamente lo que ESTADO.md ya tiene anotado como
  pendiente ("conectar el Studio a la API multiusuario") sin fecha — reflejo
  de que nadie lo dimensionó todavía. Este plan lo deja con su costo escrito
  para que la decisión de cuándo hacerlo sea informada, no una sorpresa a
  mitad de sprint.

### D5 · Terminal en vivo (streaming real)
Ya documentado en IDEAS.md T2.2 con la advertencia de seguridad del filtro
en el stream. No se toca en esta pasada — depende del registro de procesos
que D3 también toca; hacerlos en paralelo duplica el riesgo de tocar
`_correr` dos veces sin coordinación.

### D6 · Fallback de runtime por 429
Bloqueado por lo mismo que D3 recorta: `BackendError` sin tipo. Cuando se
haga, D3 y D6 comparten esa pieza — un solo trabajo, no dos.

## 6. Registro de fallos conocidos (qué puede salir mal)

| Riesgo | Dónde | Mitigación en este plan |
|---|---|---|
| Loop de reintentos infinito | D3 | tope duro + backoff, ya quemado una vez (§11 ARQUITECTURA.md) |
| Filtro de credenciales saltado en stream | D2 | verificar que `/api/eventos` no lleva contenido de nodo antes de escribir código |
| UI ofrece `espera` en boards viejos que no lo soportan | D1 | el motor ya acepta `esperar_segundos`, no hay boards viejos rotos — el `<select>` solo agrega opción |
| Retry automático oculta un fallo real de config | D3 (ya existente) | tope `MAX_INTENTOS=2` ya está; sin cambios en D3 |
| Carrera entre retry manual y automático sobre la misma card | D3 | agregar lock compartido — hallazgo de la revisión de ingeniería, no estaba en la versión original del plan |
| `resumen` (contenido de nodo) viaja en el SSE, no solo estado | D2 | ya mitigado en escritura (`limpiar_salida`, `loop.py:364`) — corregir la documentación, no el código |

## 7. Orden de ataque (revisado)

**D1 → D3 → D2**, no el orden original. Con el alcance corregido, D3 pasó a
ser "agregar backoff + un lock a código que ya funciona" — más chico y más
seguro que D2, que ahora incluye un webhook nuevo y toca permisos del
navegador (`Notification` API). D1 sigue primero (sin dependencias, más
barato). D2 va último porque D2b (webhook) es la pieza nueva de mayor
superficie de todo el Tier 1.

## 9. Registro de decisiones (post revisión dual: voz CEO + voz Eng)

| # | Decisión | Origen | Cómo se resolvió |
|---|---|---|---|
| 1 | Corregir tabla §1: retry automático ya existe | Eng (crítico) | Auto-decidido — corrección de hecho, no hay alternativa |
| 2 | Reducir D3 a "backoff + lock", no "construir retry" | Eng (crítico) | Auto-decidido — corrección de hecho |
| 3 | Corregir justificación de seguridad de D2 sobre `/api/eventos` | Eng (alto) | Auto-decidido — corrección de hecho |
| 4 | Partir D2 en D2a (SSE, cómodo) + D2b (webhook, cumple la premisa) | CEO (crítico) | Auto-decidido — sin D2b, D2 no resuelve el problema que lo originó |
| 5 | Agregar D0 (spike de `apps/api`) antes de costear D4 | CEO (alto) | Auto-decidido — costo de D4 hoy es una estimación a ciegas |
| 6 | Reordenar Tier 1 a D1→D3→D2 | Eng (bajo) | Auto-decidido — D3 se achicó, D2 creció |
| 7 | Mockear `Notification` API en vez de test manual | Eng (medio) | Auto-decidido — más barato que "manual" y sí corre en CI |
| 8 | Agregar lock compartido retry manual/automático | Eng (medio) | Auto-decidido — bug real, no cosmético |
| 9 | D1-D3 primero, D4 después. D0 (spike de `apps/api`) no bloquea — se puede correr aparte cuando haya tiempo. | CEO (alto) | **Decidido por el usuario**: el operador único sigue siendo el uso real hoy; D4 sin nadie más esperando sería trabajo especulativo |
| 10 | D2b usa webhook HTTP genérico configurable (no Slack específico, no email) | Plan original, sin especificar | **Decidido por el usuario**: cubre Slack/Discord/ntfy.sh/receptor propio sin dependencia nueva ni credencial SMTP |

**Estado: plan aprobado para Tier 1 (D1, D3, D2 en ese orden).** D0/D4 quedan
documentados en Tier 2, sin fecha, corren cuando haya tiempo aparte.

## 8. Qué NO resuelve este plan

De la crítica pesimista original, quedan sin resolver a propósito:
- El tope de gasto que no aplica fuera de claude-code (requiere trabajo en
  cada CLI, no es "uso diario", es "confiabilidad del budget", otro plan).
- Observabilidad tipo Prometheus/OTel — decisión ya tomada en IDEAS.md
  §2.4, no se reabre.
- El layout O(n²) del canvas — solo importa a cientos de nodos, no es el
  cuello de botella de uso diario con los grafos actuales (9 plantillas, 4
  grafos guardados, ninguno grande).
