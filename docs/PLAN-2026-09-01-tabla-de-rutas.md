# Plan: tabla de despacho para las rutas de `ui/server.py` — 2026-09-01

Origen: deuda anotada en `ESTADO.md` desde el principio de esta sesión de
trabajo ("tabla de rutas en `ui/server.py`: 34 endpoints en dos cadenas de
`if`"), pospuesta explícitamente en los planes de "visor de diff" y
"BackendError tipada" ("su propio plan, no mezclado con esta feature"). El
usuario pidió armarlo ahora.

## 0. Premisas (investigadas antes de escribir el plan, no delegadas)

1. **Corregido por la revisión de ingeniería: el número real es 56, no 55
   (y no 34).** `grep -c` da 23 en `_get` (GET, no 22 — conté mal la primera
   vez) y 33 en `do_POST`. La cifra de `ESTADO.md` estaba desactualizada —
   el proyecto siguió agregando rutas (D1, D2b, el visor de diff) mientras la
   deuda esperaba. **El criterio de "listo" en R3 no es el número 56 escrito
   acá — es `len(_RUTAS_GET) + len(_RUTAS_POST)` corrido de verdad contra el
   `del_servidor` real, para no repetir este mismo error de conteo dos veces.**
2. **Corregido: son 10 ramas gordas, no 12.** `/api/grafo` GET
   (`server.py:2158-2165`, 8 líneas) y `/api/grafo` POST (`server.py:
   2448-2451`, 4 líneas) las medí mal en la primera versión de este plan —
   ya son delgadas por la propia definición de abajo (1-9 líneas), no
   necesitan extracción. Las 10 gordas reales: `/api/reporte/descargar`
   (24), `/api/plantilla` (20), `/api/chat` (20), `/api/correr` (18), `/api/
   generar-grafo` (15), `/api/parametros` (13), `/api/mcp` (11), `/api/
   guardar-plantilla` (11), `/api/reintentar-nodo` (10), `/api/optimizar-goal`
   (10). Las 46 restantes son de 1 a 9 líneas, casi todas un solo `return`.
3. **Hallazgo de la revisión de ingeniería, crítico para R1/R2: dos ramas
   NO usan `self._responder` y no deben empezar a hacerlo.**
   - `/api/eventos` (`server.py:2088-2089`) delega a `_stream_eventos`
     (`server.py:2051-2083`) — streaming SSE de verdad, `send_response`/
     `send_header`/`end_headers()` propios y un loop de `wfile.write` con
     `time.sleep(0.5)` por 60 iteraciones.
   - `/api/reporte/descargar` (`server.py:2126-2149`) arma headers a mano
     (`Content-Disposition`, `Content-Length`) y escribe bytes directo con
     `wfile.write`, sin pasar por `_responder`.
   Las dos calzan igual en la firma uniforme `(self, params) -> None` (ya
   reciben `self` y ya derivan todo de `params`) — el riesgo no es la firma,
   es que alguien "normalice" el cuerpo a un `return self._responder(...)`
   porque parece la convención del resto, rompiendo streaming y descargas.
   **`/api/reporte/descargar` además no tiene NINGÚN test en todo `tests/`**
   (solo figura en `AFUERA` de `test_contrato_ui.py` con motivo "descarga
   binaria") — es la rama de mayor riesgo de transcripción manual y la única
   sin red de seguridad. Ver R0 nueva, abajo.
4. **Hallazgo crítico, investigado antes de comprometer alcance**: `tests/
   test_contrato_ui.py:442-446` YA tiene un auto-chequeo que lee el código
   FUENTE de `server.py` con una regex — `r'(?:ruta|self\.path) == "(/api/
   [^"]+)"'` — para no perder rutas silenciosamente ("se escribió después de
   que una comparación a mano encontrara SEIS rutas que no estaban ni en la
   tabla ni en AFUERA"). **Un dict de despacho hace que ese patrón deje de
   existir en el texto fuente: el chequeo encontraría 0 (o muy pocas) rutas
   y el propio `assert len(del_servidor) >= 40` fallaría, cortando la
   protección que este test existe para dar.** No es opcional arreglarlo
   después — es parte de ESTE plan. La corrección real es una mejora, no un
   parche: introspeccionar los dicts de verdad (`set(_RUTAS_GET) | set(_RUTAS_POST)`)
   en vez de regexear texto — más robusto, no una copia con otra sintaxis.
   **Corregido por la revisión de ingeniería**: 4 paths existen en AMBOS
   dicts (`/api/grafo`, `/api/exportar-dataset`, `/api/workspace/analizar`,
   `/api/reporte-corrida` — GET y POST hacen cosas distintas con el mismo
   path). El `set(...) | set(...)` del plan ya los deduplica bien (el
   `del_servidor` viejo tampoco distinguía método), pero una implementación
   ingenua que comparara cada dict por separado generaría 4 "huérfanas"
   falsas — R3 tiene que decirlo explícito, no asumirlo obvio.
5. **La lógica compartida ANTES del despacho no se toca.** En `do_GET`: el
   chequeo de host (`_host_ok`) y la allowlist de rutas sin token (`ruta not
   in ("/", "/api/capacidades")`, `server.py:2032`) corren ANTES de mirar
   qué ruta es — el refactor cambia solo QUÉ pasa después de saber la ruta,
   no la decisión de si hace falta token. En `do_POST`: el parseo de
   `Content-Length`/JSON (`server.py:2177-2193`, con sus guardas de negativo
   y de tope de 8MB) también corre antes de cualquier `if self.path ==` —
   se mantiene igual, los handlers reciben `cuerpo` ya parseado. **Precisión
   de la revisión de ingeniería**: el `try/except` de `compilador.
   ErrorDeGrafo`/`ValueError`/`Exception` que hoy envuelve TODA la cadena de
   ifs (`server.py:2452-2463`) tiene que quedar envolviendo la LLAMADA al
   despachador (`manejador(self, cuerpo)`), no replicado por entrada del
   dict — algunas ramas ya tienen su propio try/except interno (`/api/
   exportar-mermaid`, `/api/generar-grafo`), otras dependen enteramente del
   externo (`/api/validar`, `/api/compilar`, `/api/mcp`, `/api/grafo/
   borrar`) y ese comportamiento no cambia.
6. **Confirmado por la revisión de ingeniería, sin sorpresas**: ninguna de
   las 10 ramas gordas reales captura variables locales de `do_POST`/`_get`
   por closure (a diferencia del plan del visor de diff, donde `_git`/
   `_sin_filtros` sí lo hacían) — "extraer verbatim" es seguro tal cual.

## 1. Lo que ya existe y no hay que reconstruir

| Pieza | Dónde | Nota |
|---|---|---|
| 43 ramas "delgadas" (1-9 líneas) | `_get`/`do_POST` | se convierten en una entrada de dict directa, sin extraer nada nuevo |
| 12 ramas "gordas" (10-37 líneas) | listadas en Premisa 2 | se mueven a una función propia, cuerpo verbatim, sin reescribir lógica |
| Guarda de host + allowlist sin token | `do_GET:2029-2033` | no se toca |
| Parseo de `Content-Length`/JSON + tope 8MB | `do_POST:2172-2193` | no se toca |
| Auto-chequeo de rutas huérfanas/fantasma | `test_contrato_ui.py:434-462` | se actualiza para introspeccionar los dicts, no el texto fuente |

## 2. Alcance de este plan

**Adentro**: convertir TANTO `_get` como `do_POST` a despacho por dict, en
un solo plan coordinado — no partido en dos PRs. Motivo: el auto-chequeo de
rutas es una sola aserción sobre el archivo entero
(`len(del_servidor) >= 40`); convertir solo una mitad deja un estado
intermedio donde el chequeo se rompe igual (quedarían ~22 o ~33 rutas en el
patrón viejo, ninguna de las dos cifras por sí sola es representativa), así
que no hay ahorro real en partirlo — el trabajo de arreglar el test hay que
hacerlo una vez, para las dos mitades juntas.

**Afuera, a propósito**:
- Reescribir la lógica de negocio de las 12 ramas gordas — se MUEVEN, no se
  cambian. Si algo ahí parece mejorable, es su propio hallazgo, no este plan.
- Cualquier cambio de contrato (nombres de rutas, códigos de estado,
  formas de respuesta) — este plan es refactor puro, cero rutas nuevas,
  cero rutas movidas de sitio en la URL.
- Optimizar el despacho por rendimiento — un dict de 55 entradas contra un
  if-chain de 55 comparaciones de string no tiene diferencia medible a esta
  escala; el valor es navegabilidad y auditabilidad, no velocidad.

## 3. Diagrama: qué toca cada tarea

```
   ANTES (do_GET/_get, do_POST — cada uno un if-chain secuencial)
   ┌────────────────────────────────────────────┐
   │ if ruta == "/api/estado": ...                │
   │ if ruta == "/api/eventos": ...                │  55 ramas, 43 de 1-9
   │ if ruta == "/api/grafo":  (36 lineas inline)  │  lineas + 12 de
   │ ...                                            │  10-37 lineas
   └────────────────────────────────────────────┘

   DESPUES
   ┌──────────────────┐     ┌───────────────────────┐
   │ 12 funciones       │     │ _RUTAS_GET = {          │
   │ nuevas, una por     │◄────│   "/api/estado": ...,  │
   │ rama gorda           │     │   "/api/grafo": _get_grafo,
   │ (cuerpo verbatim)    │     │   ...                    │
   └──────────────────┘     │ }                         │
                              │ _RUTAS_POST = { ... }     │
                              └──────────┬────────────────┘
                                         │
                              ┌──────────▼────────────────┐
                              │ _get()/do_POST() pasan a   │
                              │ ser: lookup + llamar,       │
                              │ sin cambiar la guarda de    │
                              │ host/token/Content-Length   │
                              └────────────────────────────┘

   test_contrato_ui.py: introspecciona _RUTAS_GET/_RUTAS_POST
   directo (set(dict) | set(dict)), no regex sobre el texto fuente.
```

## 4. Tareas

### R0 · Test para `/api/reporte/descargar` antes de tocarla (nuevo, de la revisión CEO)
- **Qué**: esa ruta no tiene NINGÚN test hoy (Premisa 3) y es la de mayor
  riesgo de transcripción manual (headers a mano, bytes directos, no pasa
  por `_responder`). Un test mínimo que pegue contra el endpoint real y
  verifique status 200, `Content-Disposition` presente, y que el cuerpo
  no esté vacío — antes de mover una sola línea de esa función.
- **Por qué entra al plan**: sin esto, la afirmación de la sección 6 ("cada
  ruta ya tiene cobertura existente") era falsa para la rama que más lo
  necesita. Se cierra acá, no se declara aparte.

### R1 · Extraer las 10 ramas gordas a funciones propias
- **Qué**: cada una de las 10 ramas de la Premisa 2 se mueve, cuerpo
  verbatim (mismos comentarios, misma lógica), a una función con nombre:
  prefijo `_get_`/`_post_` + el último segmento de la ruta (ej. `/api/chat`
  → `_post_chat`, `/api/reintentar-nodo` → `_post_reintentar_nodo`) —
  decidido acá, no queda para la implementación.
- **Nota obligatoria (Premisa 3)**: `_get_eventos` (delega a
  `_stream_eventos`) y `_get_reporte_descargar` NO llaman a `self.
  _responder` — siguen escribiendo la respuesta HTTP a mano (streaming SSE
  y headers de descarga respectivamente). No "normalizarlas" a
  `_responder` durante la extracción: es exactamente el tipo de cambio de
  comportamiento que este plan promete NO hacer (sección 5).
- **Riesgo**: bajo si es copy-move literal; el riesgo real es un error de
  transcripción (una línea que se pierde al mover) — confirmado por la
  revisión que ninguna de las 10 depende de closures, así que "verbatim"
  es seguro. Mitigado además por R3 abajo y, para la más riesgosa
  (`/api/reporte/descargar`), por R0.
- **Test**: cada test que ya ejercita esa ruta (varios ya existen —
  `test_contrato_ui.py`, `test_modo_app.py`, etc., más el nuevo de R0)
  tiene que seguir en verde SIN cambios, antes y después del movimiento.

### R2 · Construir `_RUTAS_GET`/`_RUTAS_POST` y reemplazar el despacho
- **Qué**: diccionarios a nivel de módulo que mapean cada una de las 56
  rutas a su función — las 10 recién extraídas y las 46 delgadas
  convertidas directo. `_get`/`do_POST` pasan a ser: guarda existente (sin
  tocar) → `manejador = _RUTAS_X.get(ruta)` → `manejador(self, params_o_cuerpo)`
  si existe, `404` si no.
- **Firma, decidida acá (no "a confirmar" — ya verificada contra una
  delgada y una gorda real por la revisión de ingeniería)**: `(self,
  params) -> None` para `_RUTAS_GET`, `(self, cuerpo) -> None` para
  `_RUTAS_POST`. Ninguna de las 56 ramas necesita wrapper especial.
- **El `try/except` externo de `do_POST`** (`compilador.ErrorDeGrafo`/
  `ValueError`/`Exception`, hoy envolviendo toda la cadena de ifs) pasa a
  envolver la LLAMADA al despachador (`manejador(self, cuerpo)`), no cada
  entrada del dict — las ramas que ya tienen su propio try/except interno
  (`/api/exportar-mermaid`, `/api/generar-grafo`) lo conservan sin cambios.
- **Test**: extender `test_contrato_ui.py` para golpear las 56 rutas (ya
  golpea la mayoría vía `TABLA`) y confirmar código de estado + forma
  IDÉNTICA a antes del refactor — no una aserción nueva, la MISMA que ya
  existe, corrida contra el código nuevo.

### R3 · Actualizar el auto-chequeo de rutas huérfanas/fantasma
- **Qué**: `test_contrato_ui.py:442-446` deja de regexear el texto fuente
  y pasa a leer `srv._RUTAS_GET`/`srv._RUTAS_POST` directo — el módulo
  `server.py` YA se importa in-process más abajo en el mismo archivo
  (`import server as srv`, para stubear `run_chat`), así que mover ese
  import antes del auto-chequeo es trivial y no arranca nada (`server.py`
  tiene guarda `if __name__ == "__main__":`). El comentario que explica
  POR QUÉ existe este chequeo ("se escribió después de que una comparación
  a mano encontrara seis rutas perdidas") se mantiene — la razón de ser no
  cambia, solo la fuente de verdad (dict real en vez de texto).
- **Deduplicación explícita (Premisa 4)**: 4 paths existen en AMBOS dicts
  (`/api/grafo`, `/api/exportar-dataset`, `/api/workspace/analizar`,
  `/api/reporte-corrida`) — el chequeo usa `set(_RUTAS_GET) | set(
  _RUTAS_POST)`, igual que el regex viejo ya ignoraba el método. Compararlos
  por separado generaría 4 "huérfanas" falsas.
- **Riesgo**: si esto se hace MAL o se hace DESPUÉS de R1/R2 en vez de en
  el mismo commit, hay una ventana donde el test está roto y no protege
  nada — no es una tarea "aparte", es la que cierra el plan.
- **Test**: el propio `test_contrato_ui.py` corriendo completo es el test
  de esta tarea — si el auto-chequeo encuentra las 56 rutas del dict real
  (contadas en código, no escritas a mano) y sigue sin huérfanas/fantasmas,
  está resuelto.

### R4 · Anotar (no arreglar) inconsistencias de guarda vistas de paso (nuevo, de la revisión CEO)
- **Qué**: al leer las 56 ramas línea por línea para R1, es el momento más
  barato para notar si la allowlist sin token (`ruta not in ("/", "/api/
  capacidades")`, Premisa 5) sigue siendo exhaustiva y correcta, o si algo
  cambió desde que se escribió. Se anota como hallazgo en el PR, NO se
  corrige acá — corregir un problema de auth de paso en un PR que promete
  "cero cambios de comportamiento" sería mezclar alcance.
- **Costo**: cero código, una observación si aparece algo.

## 5. Qué NO resuelve este plan

- No mejora ni simplifica la lógica de negocio de ninguna de las 10 ramas
  gordas — se mueven, no se tocan.
- No agrega ni quita ninguna ruta.
- No es un rediseño de la API — mismos paths, mismos métodos, mismos
  contratos.
- No corrige nada de auth de paso — R4 solo anota, ver arriba.

## 6. Riesgo general, revert, y por qué vale la pena de todos modos

Este es, por lejos, el plan de mayor superficie de las cuatro pasadas
(D1-D3-D2b, `BackendError`, visor de diff, y este): toca 56 puntos de un
archivo central en vez de agregar 1-3 piezas nuevas y acotadas. El riesgo
no es conceptual (es mecánico, confirmado sin closures por la revisión de
ingeniería) sino de VOLUMEN: mover 56 cosas sin romper ninguna. Mitigación
principal: cada ruta que ya tenía cobertura de test existente la conserva
sin cambios (con la excepción cerrada en R0); el plan no depende de tests
nuevos para detectar una regresión en la mayoría de los casos.

**Revert**: al ser un solo PR coordinado (sección 2), un `git revert` del
commit de merge deshace las 56 rutas de una — no hay estado a medio migrar
que revertir a mano. El costo del escenario de arrepentimiento es bajo:
una rota se nota en CI (los tests existentes fallan por esa ruta puntual),
y el camino de vuelta es un comando, no una reconstrucción.

**Por qué partir en dos PRs no compensa** (considerado y descartado, no
solo por default): un adaptador que soportara AMBAS fuentes (regex sobre
texto para lo no migrado + dict para lo migrado) durante una migración en
dos tandas es técnicamente posible — el test ya importa `server.py`
in-process — pero agrega una rama de compatibilidad transitoria a un
refactor de un día para ahorrar revisar un PR más grande. No se justifica
para este volumen.

## 7. Registro de decisiones (post revisión dual)

| # | Decisión | Origen | Resolución |
|---|---|---|---|
| 1 | Corregir conteo: 56 rutas, 10 gordas (no 55/12) | Eng (alto) | Auto-decidido — recuento verificado línea por línea |
| 2 | `/api/eventos` y `/api/reporte/descargar` NO se normalizan a `_responder` | Eng (crítico) | Auto-decidido — cambiar su comportamiento violaría la sección 5 del propio plan |
| 3 | Agregar R0: test para `/api/reporte/descargar` antes de tocarla | CEO (crítico) | Auto-decidido — la mitigación principal del plan era falsa para esa ruta |
| 4 | Fijar la firma `(self, params)`/`(self, cuerpo)` ahora, no "a confirmar" | CEO (medio) + Eng (confirmación) | Auto-decidido — ya verificada contra casos reales, no hace falta dejarla abierta |
| 5 | Deduplicar `_RUTAS_GET`/`_RUTAS_POST` explícitamente en R3 (4 paths compartidos) | Eng (medio) | Auto-decidido |
| 6 | `try/except` externo envuelve la llamada al despachador, no cada entrada | Eng (bajo) | Auto-decidido |
| 7 | Agregar R4: anotar (no arreglar) inconsistencias de guarda vistas de paso | CEO (medio) | Auto-decidido — costo cero, no mezcla alcance |
| 8 | Ejecutar ahora, no esperar a la conexión con la API multiusuario | CEO (alto) | **Decidido por el usuario**: la investigación ya está hecha (56 rutas contadas, excepciones identificadas, firma fijada) — dejarlo para después la tira, y "conectar la API" no tiene fecha en `ESTADO.md`, podría quedar pospuesto indefinidamente |

**Estado: R0→R1→R2→R3→R4 implementados y verificados.** 56 rutas reales
(23 GET + 33 POST, 52 únicas con los 4 paths compartidos deduplicados),
`_RUTAS_GET`/`_RUTAS_POST` construidas y confirmadas contra el código real
al importar el módulo. `test_contrato_ui.py` introspecciona los dicts
(119 claves contrastadas, igual que antes del refactor). R4: releída la
allowlist de token durante la extracción, sin encontrar inconsistencias —
sigue siendo solo `"/"` y `/api/capacidades`.

**Hallazgo colateral, no relacionado con este plan, arreglado igual**: al
correr la suite completa (disciplina de este refactor: "cada ruta ya
ejercitada sigue en verde") apareció `tests/test_consumo.py` roto — un bug
preexistente del backoff de D3 (PR #3), no de este refactor: el test
llamaba `loop.reintentar()` inmediatamente después de un fallo sin
desactivar `BACKOFF_ACTIVO`, así que nunca reabría la card. Mismo arreglo
que ya se le había aplicado a `test_concurrencia_reintentos.py` en el plan
de `BackendError` (PR #4): `loop.BACKOFF_ACTIVO = False` antes de la
secuencia fallo→reintento→cierre que el test mecánicamente necesita
inmediata.

## 8. Sobre la pregunta 8: lo que hay que saber para decidir

La revisión CEO señaló que este es el primer plan de la serie sin valor de
usuario directo, justo después de tres que sí lo tenían, y que
`ESTADO.md` ya anota "conectar el Studio a la API multiusuario" como
próximo paso — un trabajo que **va a tocar `server.py` extensamente** (es
la pieza que hoy le habla directo al motor Python; conectarla a la API
significa que el Studio deje de hacerlo). Si ese trabajo es inminente,
hacer el refactor de rutas de paso ahí es más barato en horas totales que
una pasada dedicada ahora. Si no hay fecha cercana para ese trabajo, la
deuda sigue creciendo con cada plan que toque `server.py` mientras tanto
(van tres: D1/D2b, causa tipada, visor de diff) y conviene cerrarla ahora
que ya está investigada y lista para ejecutar.
