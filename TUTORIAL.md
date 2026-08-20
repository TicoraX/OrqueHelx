# ORQUESTER — cómo se usa

Diseñás un flujo, cada nodo elige quién lo ejecuta, y el resultado sale por
MCP para invocarlo desde tu editor. Este tutorial va de cero a un flujo real.

---

## 0. Qué necesitás

| Pieza | Para qué | Cómo verificar |
|---|---|---|
| Python 3.11 + `uv` | Todo corre acá | `uv --version` |
| Hermes Agent | El scheduler del DAG | `hermes --version` |
| `claude` | Nodos `runtime: claude-code` | `claude --version` |
| `opencode` | Nodos `runtime: opencode` | `opencode --version` |
| `agy` | Nodos `runtime: antigravity` | `agy --version` |

**Los CLIs corren con tu propia sesión y tu propia suscripción.** ORQUESTER no
administra API keys ni las ve: lanza procesos que ya están autenticados. Si un
nodo funciona cuando lo corrés a mano, funciona dentro de un flujo.

No hace falta tener los tres. Un flujo que solo usa `opencode` solo necesita
`opencode`.

---

## 1. Levantar el Studio

```bash
cd <ruta-al-repo>
sh arrancar.sh              # motor + Studio
sh arrancar.sh --multi      # + Postgres + API multiusuario
sh arrancar.sh --parar      # baja lo que levantó
```

El script imprime la URL con el token. Es idempotente: si ya estaba corriendo,
lo dice y no duplica nada. Los logs quedan en `.logs/`.

El token se genera una vez y vive en `.orquester-token` (ignorado por git). En
un archivo y no en el script porque un secreto hardcodeado en el repo termina
publicado.

A mano, si preferís:

```bash
uv run --python 3.11 --with jsonschema python ui/server.py
```

El servidor imprime la URL con un token:

```
Studio en http://127.0.0.1:8765/?token=xK3v…
```

Abrila tal cual. El token se guarda en la sesión del navegador y desaparece de
la barra de direcciones, así no queda en el historial ni en una captura.

**Todo `/api/*` exige ese token.** Sin él no se puede consultar estado, compilar
ni ejecutar. Es la única barrera entre alguien y una shell en tu máquina, porque
esto lanza agentes con `Bash`.

| Variable | Para qué |
|---|---|
| `ORQUESTER_TOKEN` | Fija el token en vez de generar uno nuevo en cada arranque |
| `ORQUESTER_HOST` | `0.0.0.0` lo abre a la red. Por defecto solo localhost |

Si lo exponés a la red, el token deja de ser una formalidad. Pensalo dos veces.

---

## 2. Diseñar el flujo

En el canvas:

| Gesto | Qué hace |
|---|---|
| Doble clic en vacío | Nodo nuevo |
| Arrastrar un nodo | Moverlo |
| **Shift + arrastrar** de un nodo a otro | Dependencia (el segundo espera al primero) |
| Clic en una flecha | Borrarla |
| Clic en un nodo | Lo selecciona y abre su traza |

Con el nodo seleccionado, el panel derecho tiene dos campos que importan:

**Título** — es el *goal* del nodo, el prompt que recibe el agente. No es una
etiqueta: es el trabajo.

**Ejecutor** — quién lo corre. `Hermes (nativo)`, `Claude Code`, `OpenCode` o
`Antigravity`.

**Modelo** — opcional, **por nodo**. Vacío = el que use ese CLI por defecto.
Cada uno lo quiere en su forma, y el Studio te muestra cuál al lado del campo:

| Ejecutor | Forma | Cómo listarlos |
|---|---|---|
| Claude Code | nombre (`claude-sonnet-4-6`) | — |
| OpenCode | `proveedor/modelo` (`deepseek/deepseek-chat`) | — |
| Antigravity | slug (`gemini-3.1-pro-high`) | `agy models` |
| Hermes | el del proveedor configurado | `hermes model` |

Sirve para lo que uno espera: el nodo que piensa va con un modelo caro, el que
solo reformatea va con uno barato. En una prueba real el mismo nodo tardó 9,8s
con `gemini-3.7-flash-low` y 18,6s con `gemini-3.1-pro-high`.

### Escribir un buen goal

Es lo que más define si el flujo sirve. Lo aprendido a los golpes:

- **Sé específico o el agente improvisa.** "Revisá el PR 7" hizo que OpenCode
  se pusiera a explorar el repo durante diez minutos. "Contá los archivos .md
  del directorio actual y devolvé el número" tarda nueve segundos.
- **Decí explícitamente qué NO hacer.** Un nodo con el workspace vacío se
  inventó cuatro archivos de prueba para tener algo que contar. Si no querés
  que fabrique su input, escribilo.
- **Acotá el formato de salida.** "Máximo 5 hallazgos, cada uno con
  archivo:línea" da algo usable; "revisá el código" da tres párrafos.
- **Los hijos reciben el resumen de sus padres automáticamente.** No repitas el
  contexto: escribí "tu padre te pasó X" y ya lo tiene.

---

## 3. Validar, compilar, ejecutar

Los tres botones, en orden:

**Validar** — rechaza ciclos, aristas colgadas y runtimes desconocidos **sin
tocar nada**. El error nombra el nodo culpable.

**Compilar** — crea las cards en el board. A partir de acá el DAG existe: el
kanban de Hermes resuelve las dependencias.

**Ejecutar** — arranca el dispatcher. Los nodos se pintan solos cada 2.5s:

| Color | Estado |
|---|---|
| Gris | espera a sus padres |
| Ámbar | listo, sin arrancar |
| Azul | corriendo |
| Verde | terminado |
| Rojo | falló |
| Naranja | `triage` — se agotaron los reintentos, decidí vos |

> **Nodos `runtime: hermes`:** los ejecuta el dispatcher de Hermes, no el de
> ORQUESTER (`ARQUITECTURA.md` §12). El botón Ejecutar tickea **los dos**, así
> que un flujo mixto no necesita otra terminal. Si el binario de Hermes no está
> en el `PATH`, configurá `ORQUESTER_HERMES_BIN`.

### Consumo

Debajo del estado aparece cuánto gastó el flujo: tokens totales, entrada,
salida, cuánto vino de cache, y el **equivalente API** en dólares.

Ese número es lo que ese trabajo *habría costado* pagando por token. Si lo
corriste con tus suscripciones, tu costo marginal fue cero — la diferencia es
justamente el argumento de `IDEAS.md` §1.

Los backends que corren por suscripción y no informan medidor (`agy`) se
cuentan aparte y **no** entran como costo cero: un promedio que los incluyera
como gratis mentiría.

---

## 4. Cuando algo falla

Clic en el nodo. La traza muestra cada intento con su error, y la secuencia de
eventos del kanban.

Un fallo **no** cierra el nodo: lo bloquea, y sus hijos se quedan esperando. Un
hijo nunca arranca sobre el mensaje de error de su padre.

Hay dos clases de fallo:

- **`transient`** — se reintenta solo, hasta 2 veces.
- **`capability`** — no se reintenta nunca. Es "falta algo": binario ausente,
  runtime desconocido. Reintentarlo solo gasta cuota.

Si los reintentos se agotan, el nodo va a `triage`. Ahí decide un humano.

---

## 5. Publicarlo como MCP

Poné `{{marcadores}}` en los goals y se vuelven los parámetros de la tool. **No
se declaran aparte:** escribir el goal ya es declarar la interfaz.

```
Revisá el código de {{ruta}} en los últimos {{commits}} commits
```

Botón **Exportar como MCP**. Te da el snippet para tu cliente:

```json
{"mcpServers": {"revision-repo": {
    "command": "python",
    "args": ["<ruta-al-repo>/mcp_exporter/mcp_server.py",
             "<ruta-al-repo>/ui/grafos/revision-repo.json"]}}}
```

Desde Claude Desktop o Cursor, la tool aparece con sus parámetros. Al llamarla,
el flujo corre en un board propio y devuelve el resultado de sus **hojas** (los
nodos de los que nadie depende).

Dos llamadas concurrentes no se pisan: cada una usa su board.

---

## 5b. Plantillas: por dónde empezar

No arranques con un lienzo en blanco. El Studio trae un catálogo en la pestaña
**Diseño**:

| plantilla | qué hace |
| --- | --- |
| `revision-de-repo` | revisa el diff, corre los tests y emite veredicto de merge |
| `segunda-opinion` | la misma pregunta a dos ejecutores y un tercero que los compara |
| `triage-de-bug` | reproducir → causa raíz → arreglo, en cadena y sin saltear pasos |
| `documentar-cambios` | del diff salen la entrada de changelog y qué doc quedó vieja |
| `explicar-un-repo` | mapa, puntos de entrada y riesgos de un repo que no conocés |

Cada una trae **cinco nodos ejecutables, una nota y sus reglas**. Se abren con
el botón **Plantillas** de arriba a la derecha.

**Las plantillas viven en `plantillas/` y son de solo lectura.** "Usar esta" la
copia a `ui/grafos/` con el nombre que le des; a partir de ahí es tuya y la
editás sin miedo. Por eso son dos carpetas: un `git pull` que mejore una
plantilla no te pisa lo que hayas armado encima.

Cada tarjeta muestra cuántos nodos tiene, qué ejecutores usa y qué parámetros
va a pedir. Si te falta un binario, lo dice **antes** de correr, en vez de
descubrirlo a los 600 segundos.

### Notas y reglas

Dos cosas que no se ejecutan pero cambian cómo sale el trabajo:

- **Nota**: un bloque de explicación en el lienzo. No se compila, no genera
  card y no puede tener dependencias (el compilador lo rechaza en vez de
  ignorarlo, porque `a → nota → b` se vería conectado y `b` arrancaría sin
  esperar a `a`). Se crea con el selector **Tipo** en la pestaña Nodo.
- **Reglas del flujo**: un texto que va a **todos** los nodos del grafo. Viaja
  en el `body` de la card, no pegado al goal, así el título sigue siendo
  legible en el lienzo y en el kanban. Es donde van las barandas: "no instales
  dependencias", "no modifiques archivos", "respondé en español".

Para borrar uno de tus grafos, elegilo en "Abrir un grafo guardado" y dale a
**Borrar**. Las plantillas no se pueden borrar desde la UI: para eso está git.

---

## 5c. El chat: una tarea suelta, sin dibujar un grafo

Pestaña **Chat**. Elegís ejecutor, opcionalmente un workspace, y hablás. El
agente **recuerda los turnos anteriores**: la conversación la guarda el propio
CLI y el Studio solo lleva el id de sesión.

- Una sesión **por ejecutor**: cambiar de Claude Code a OpenCode es entrar a
  otra conversación, no continuar la misma.
- **Nueva** olvida el id y limpia el hilo. No borra nada del lado del CLI: esa
  conversación sigue existiendo allá.
- Cada turno muestra cuánto tardó y cuánto consumió. **No son gratis**: un
  turno corto sobre un repo puede costar decenas de miles de tokens, porque el
  agente carga contexto.
- El chat corre con **las mismas barandas que un nodo**: las herramientas del
  carril y la denylist de flags de bypass.

---

## 5d. Esfuerzo y tope de gasto

**Esfuerzo por nodo** (pestaña Nodo). Cada CLI lo llama distinto y acepta
niveles distintos — la lista sale de la tabla de capacidades, no de un texto
escrito a mano:

| ejecutor | flag | niveles |
| --- | --- | --- |
| `claude-code` | `--effort` | low, medium, high, xhigh, max |
| `antigravity` | `--effort` | low, medium, high |
| `opencode` | `--variant` | minimal, high, max |
| `hermes` | `reasoning_effort` de la card | minimal … ultra |

Un nivel que el ejecutor no acepta lo **rechaza el compilador**, antes de crear
ninguna card: pedir `max` en `antigravity` y que corriera en el default sería
pagar por trabajo que no pediste.

**Tope de gasto** (pestaña Diseño, en US$, para todo el flujo). Dos niveles:

- `claude-code` tiene tope **nativo** (`--max-budget-usd`) y recibe **lo que
  queda** del presupuesto, no el total: corta a mitad de una invocación.
- En los demás lo aplica el dispatcher **entre nodos**: una vez superado el
  tope no arranca ninguno nuevo. Los que ya estaban corriendo terminan, así que
  el gasto final puede pasarse por lo que cuesten esos. Matarlos dejaría la
  card reclamada y el proceso huérfano.

Los backends por suscripción (`antigravity`) no informan medidor y suman cero:
**el tope solo puede frenar lo que se puede medir.**

---

## 6. El flujo de ejemplo: revisión de repo

`ui/grafos/revision-repo.json`. Tres nodos, dos en paralelo:

```
  codigo (claude-code)      tests (opencode)
   revisa el diff            corre la suite
          \                      /
           \                    /
            veredicto (antigravity)
             ¿se puede mergear?
```

Los dos revisores no dependen entre sí, así que **arrancan juntos**. El
veredicto espera a los dos.

Es el caso donde el producto se justifica: tres ejecutores distintos, con
paralelismo y join real. Un flujo lineal de dos nodos lo hace mejor un script.

### Correrlo

Desde el Studio: abrilo, poné los parámetros y dale Ejecutar. O sin UI:

```bash
cd hermes-agent
uv run --python 3.11 --with jsonschema python -c "
import sys, json; sys.path.insert(0, r'<ruta-al-repo>/mcp_exporter')
import exportar as ex
g = json.load(open(r'<ruta-al-repo>/ui/grafos/revision-repo.json', encoding='utf-8'))
print(ex.ejecutar(g, {'ruta': '<ruta-al-repo>', 'commits': '3'}))"
```

### El campo `workspace`

Cada nodo lo lleva apuntando a `{{ruta}}`. Es **dónde corre el agente**. Sin
eso, Hermes le da un directorio scratch vacío y el agente no ve tu repo.

Ojo: el scratch se **borra** al completar la card. Un nodo cuyo entregable sean
archivos necesita `workspace` explícito.

---

## 7. Permisos

Los nodos externos corren con `Read`, `Grep`, `Glob` y `Bash`: alcanza para
inspeccionar un repo y correr tests.

Los flags de bypass de permisos (`--dangerously-skip-permissions` y
equivalentes) están en una **denylist** y el dispatcher rechaza cualquier
invocación que los lleve. Esto no es paranoia de manual: un worker de Hermes,
al chocar contra una barrera de permisos, se la desactivó solo agregando el
flag por su cuenta.

---

## 8. Problemas conocidos

**Un nodo tarda muchísimo.** Casi siempre el goal es vago y el agente se puso a
explorar. Acotalo. Comparalo corriendo el mismo prompt a mano.

**El nodo `hermes` nunca arranca.** El dispatcher de Hermes no está corriendo
(sección 3). Y no uses proveedores de tipo `external_process` como `copilot`:
todo agente hijo se cuelga en `Initializing agent...` para siempre.

**Un nodo cierra sin invocar el CLI.** No pasa con el dispatcher de ORQUESTER,
que invoca el binario él mismo. Sí pasa si intentás usar el campo `skills` de
Hermes para elegir ejecutor: `skills` es contexto, no selector.

**Fallos que no se reintentan.** Es a propósito si son `capability`. Mirá la
traza del nodo.

---

## 8.1 Multiusuario (opcional)

El Studio con token alcanza para vos solo. Si querés equipo —usuarios, roles,
versionado y auditoría— hay una API aparte.

```bash
docker compose up -d db                    # Postgres en el 5433
cd apps/api && npm install
npx prisma db push && npm run build
PORT=3000 ORQUESTER_ENGINE_URL=http://127.0.0.1:8765   ORQUESTER_TOKEN=<el-token-del-studio> npm start
```

El motor (Python) tiene que estar corriendo: NestJS no ejecuta nada, lo llama.

### Roles

| Rol | Puede |
|---|---|
| `VIEWER` | Ver grafos, versiones, corridas y auditoría |
| `EDITOR` | Todo lo anterior + crear, versionar y **ejecutar** |
| `OWNER` | Todo lo anterior + administrar miembros |

Ejecutar exige `EDITOR` a propósito: un `VIEWER` mira, no gasta cuota ni corre
shell en el servidor.

### Versiones

Editar un grafo **no lo pisa**: crea la versión siguiente. Ejecutar toma la
última salvo que pidas otra por `versionId`. Así una corrida vieja se puede
explicar con el grafo que de verdad se ejecutó.

### Qué guarda cada base

Postgres guarda **quién y qué diseñó**; el kanban de Hermes guarda **qué está
corriendo**. Un `Run` en Postgres es el puntero al board más quién lo lanzó.
No hay duplicación: el kanban no es nuestro (`ARQUITECTURA.md` §10.2).

---

## 9. Los tests

Todos, en un comando, desde la raíz del repo:

```bash
uv run --python 3.11 --with jsonschema --with pyyaml python tests/correr.py
```

**Las dos `--with` no son opcionales.** `jsonschema` y `pyyaml` son
dependencias duras: sin ellas cuatro tests fallan con errores que parecen del
código y no lo son. `tests/correr.py` lo detecta antes de correr nada y te da
el comando; si igual corrés un test suelto con el Python del PATH, es lo que te
va a pasar.

El runner distingue **OMITIDO** de **OK**: `test_api_rbac` sale con código 0
cuando se saltea por falta de Postgres, así que mirar solo el exit code cuenta
como verde algo que no corrió. Para que corra de verdad:

```bash
docker compose up -d db
cd apps/api && npm run build
```

Uno solo, si estás iterando:

```bash
uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_compilador.py
```

Los que aceptan `--e2e` además ejecutan agentes de verdad y tardan minutos:
`test_rebanada_vertical.py` y `test_mcp_export.py`.

### En Linux

```bash
sh tests/linux.sh          # en Windows: MSYS_NO_PATHCONV=1 sh tests/linux.sh
```

Corre la suite dentro de un contenedor `python:3.11-slim`, más la resolución de
binarios con un ejecutable plano — el único camino que la lógica de shims de
Windows (`.cmd`, `.ps1`) no ejercita.

Los e2e con agentes reales solo corren donde `claude`, `opencode` y `agy` estén
instalados y autenticados.

---

## 9.1 ¿Corre en otra máquina?

Sí. Ninguna ruta está escrita a mano: todo se deriva de `Path(__file__)`.
Verificado copiando el repo a otro directorio y corriendo la suite ahí.

`tests/test_pin_hermes.py` incluye un check que falla si vuelve a colarse una
ruta absoluta en el código.

Lo único que sí es específico de tu máquina son los **agentes**: `claude`,
`opencode` y `agy` tienen que estar instalados y autenticados con tu sesión.
Eso es a propósito — es el punto del producto (`IDEAS.md` §1).

---

## 10. Actualizar Hermes

Hermes no es nuestro: vive en `hermes-agent/`, ignorado por git, con su propio
remoto a `NousResearch/hermes-agent`. Se actualiza normal:

```bash
git -C hermes-agent pull
```

Antes y después, corré el check del pin:

```bash
cd hermes-agent
uv run --python 3.11 python ../tests/test_pin_hermes.py
```

Te dice si el clon se movió del commit contra el que está verificada la tabla
de `ARQUITECTURA.md` §10, y si el CLI instalado quedó en otra versión. **Las
dos copias escriben el mismo `kanban.db`**, así que conviene moverlas juntas.

Si actualizás a propósito: corré la suite entera, y si pasa, actualizá
`HERMES_PIN` con el commit nuevo. Si algo falla, ahí tenés el diff de upstream
para saber qué cambió.

---

## Dónde está cada cosa

| Ruta | Qué es |
|---|---|
| `ui/` | Studio: servidor y canvas |
| `compiler/` | Grafo → cards del kanban |
| `dispatcher/` | Ejecuta los nodos con agente externo |
| `mcp_exporter/` | Publica un flujo como servidor MCP |
| `hermes-agent/` | Runtime prestado (MIT, sin fork) |
| `ARQUITECTURA.md` | Las decisiones y su evidencia |
| `IDEAS.md` | Qué es el producto y por qué |
