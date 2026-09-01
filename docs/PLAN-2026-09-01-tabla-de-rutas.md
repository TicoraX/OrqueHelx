# Plan: tabla de despacho para las rutas de `ui/server.py` — 2026-09-01

Origen: deuda anotada en `ESTADO.md` desde el principio de esta sesión de
trabajo ("tabla de rutas en `ui/server.py`: 34 endpoints en dos cadenas de
`if`"), pospuesta explícitamente en los planes de "visor de diff" y
"BackendError tipada" ("su propio plan, no mezclado con esta feature"). El
usuario pidió armarlo ahora.

## 0. Premisas (investigadas antes de escribir el plan, no delegadas)

1. **El número real es 55, no 34** (`grep -c 'if self\.path ==\|if ruta =='` sobre
   `server.py`): 22 en `_get` (GET, `server.py:2034-2166`) y 33 en `do_POST`
   (`server.py:2194-2465`). La cifra de `ESTADO.md` estaba desactualizada —
   el proyecto siguió agregando rutas (D1, D2b, el visor de diff) mientras la
   deuda esperaba.
2. **No es un problema de "tabla ausente" solamente: 12 de las 55 ramas
   tienen lógica real inline, no solo un `return self._responder(...)`
   delegando a una función que ya existe.** Medido por líneas por rama: `/api/
   grafo` (POST, 37 líneas), `/api/grafo` (GET, 36), `/api/reporte/descargar`
   (24), `/api/plantilla` (20), `/api/chat` (20), `/api/correr` (18), `/api/
   generar-grafo` (15), `/api/parametros` (13), `/api/mcp` (11), `/api/
   guardar-plantilla` (11), `/api/reintentar-nodo` (10), `/api/optimizar-goal`
   (10). Las 43 restantes son de 1 a 9 líneas, casi todas un solo `return`.
   Un dict de `ruta: función_ya_existente` no alcanza para esas 12: hay que
   extraerles el cuerpo a una función propia primero — mecánico, pero real.
3. **Hallazgo crítico, investigado antes de comprometer alcance**: `tests/
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
4. **La lógica compartida ANTES del despacho no se toca.** En `do_GET`: el
   chequeo de host (`_host_ok`) y la allowlist de rutas sin token (`ruta not
   in ("/", "/api/capacidades")`, `server.py:2032`) corren ANTES de mirar
   qué ruta es — el refactor cambia solo QUÉ pasa después de saber la ruta,
   no la decisión de si hace falta token. En `do_POST`: el parseo de
   `Content-Length`/JSON (`server.py:2177-2193`, con sus guardas de negativo
   y de tope de 8MB) también corre antes de cualquier `if self.path ==` —
   se mantiene igual, los handlers reciben `cuerpo` ya parseado.

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

### R1 · Extraer las 12 ramas gordas a funciones propias
- **Qué**: cada una de las 12 ramas de la Premisa 2 se mueve, cuerpo
  verbatim (mismos comentarios, misma lógica), a una función con nombre
  (`_post_chat`, `_get_grafo`, `_post_grafo`, etc. — convención a confirmar
  en la revisión: prefijo `_get_`/`_post_` + el último segmento de la ruta).
- **Riesgo**: bajo si es copy-move literal; el riesgo real es un error de
  transcripción (una línea que se pierde al mover). Mitigado por R3 abajo.
- **Test**: cada test que ya ejercita esa ruta (varios ya existen —
  `test_contrato_ui.py`, `test_modo_app.py`, etc.) tiene que seguir en
  verde SIN cambios, antes y después del movimiento.

### R2 · Construir `_RUTAS_GET`/`_RUTAS_POST` y reemplazar el despacho
- **Qué**: diccionarios a nivel de módulo o de clase (a confirmar en la
  revisión) que mapean cada una de las 55 rutas a su función — las 12
  recién extraídas y las 43 delgadas convertidas directo. `_get`/`do_POST`
  pasan a ser: guarda existente (sin tocar) → `manejador = _RUTAS_X.get(ruta)`
  → `manejador(...)` si existe, `404` si no.
- **Riesgo real, a resolver en la revisión**: las ramas delgadas de HOY
  tienen firmas distintas entre sí (algunas usan `params.get(...)`, otras
  arman el cuerpo de la respuesta con lógica de una línea antes de llamar
  `self._responder`) — hay que decidir una firma uniforme para las
  funciones del dict (`(self, params)` para GET, `(self, cuerpo)` para
  POST) y envolver las que hoy no calzan exacto.
- **Test**: extender `test_contrato_ui.py` para golpear las 55 rutas (ya
  golpea la mayoría vía `TABLA`) y confirmar código de estado + forma
  IDÉNTICA a antes del refactor — no una aserción nueva, la MISMA que ya
  existe, corrida contra el código nuevo.

### R3 · Actualizar el auto-chequeo de rutas huérfanas/fantasma
- **Qué**: `test_contrato_ui.py:442-446` deja de regexear el texto fuente
  y pasa a leer `srv._RUTAS_GET`/`srv._RUTAS_POST` directo (import y
  introspección, no `re.findall` sobre `Path.read_text()`). El comentario
  que explica POR QUÉ existe este chequeo ("se escribió después de que una
  comparación a mano encontrara seis rutas perdidas") se mantiene — la
  razón de ser no cambia, solo la fuente de verdad (dict real en vez de
  texto).
- **Riesgo**: si esto se hace MAL o se hace DESPUÉS de R1/R2 en vez de en
  el mismo commit, hay una ventana donde el test está roto y no protege
  nada — no es una tarea "aparte", es la que cierra el plan.
- **Test**: el propio `test_contrato_ui.py` corriendo completo es el test
  de esta tarea — si el auto-chequeo encuentra las 55 rutas del dict real
  y sigue sin huérfanas/fantasmas, está resuelto.

## 5. Qué NO resuelve este plan

- No mejora ni simplifica la lógica de negocio de ninguna de las 12 ramas
  gordas — se mueven, no se tocan.
- No agrega ni quita ninguna ruta.
- No es un rediseño de la API — mismos paths, mismos métodos, mismos
  contratos.

## 6. Riesgo general y por qué vale la pena de todos modos

Este es, por lejos, el plan de mayor superficie de las cuatro pasadas
(D1-D3-D2b, `BackendError`, visor de diff, y este): toca 55 puntos de un
archivo central en vez de agregar 1-3 piezas nuevas y acotadas. El riesgo
no es conceptual (es mecánico, no hay lógica nueva) sino de VOLUMEN: mover
55 cosas sin romper ninguna. Mitigación principal: cada ruta ya tiene
cobertura de test existente (`test_contrato_ui.py` las contrasta casi
todas) — el plan no depende de tests nuevos para detectar una regresión,
depende de que los que ya existen seguir pasando exactamente igual antes y
después.
