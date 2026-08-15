# Sub-agentes Heterogéneos — Plan de Verificación

> **Para workers agénticos:** SUB-SKILL REQUERIDA: usar `superpowers:subagent-driven-development` (recomendado) o `superpowers:executing-plans` para ejecutar tarea por tarea. Los pasos usan checkbox (`- [ ]`).

**Goal:** Probar end-to-end que un DAG compilado a kanban ejecuta nodos heterogéneos (Hermes nativo, Claude Code, OpenCode, Antigravity) y que los cuatro devuelven el mismo `AgentAdapterOutput` validado.

**Architecture:** Hermes es el runtime. Cada nodo del DAG es una tarea kanban asignada a un perfil Hermes; el perfil lleva precargada la skill del agente externo y lo invoca por `terminal`. El contrato `output_schema` normaliza las cuatro salidas. Nada de esto requiere código de ORQUESTER todavía: el objetivo es validar los supuestos de `ARQUITECTURA.md` antes de escribir el compilador.

**Tech Stack:** Hermes Agent (Python 3.11, uv), SQLite, Claude Code CLI, OpenCode CLI, Antigravity CLI (`agy`), OpenCode Zen como proveedor de inferencia.

**Spec:** `ARQUITECTURA.md` (v2), §3, §4, §4.1, §5, §10. Filas "Pendiente" de la tabla §10 son el alcance exacto.

## Global Constraints

- **Python 3.11** para todo lo de Hermes. El sistema tiene 3.14; usar `uv run --python 3.11`.
- **`jsonschema` es dependencia dura.** Sin él, `validate_output` devuelve `(True, [])` y los guardrails quedan inertes en silencio (`delegation_output_schema.py:157`).
- **`MAX_SCHEMA_RETRIES = 1`**, no configurable. No diseñar nada que asuma más reintentos de schema.
- **`.env` solo para secretos** (API keys, tokens). Todo ajuste de comportamiento va en `config.yaml`. No inventar variables `HERMES_*`.
- **SQLite 3.51.3+** en despliegue. El 3.50.4 local tiene el bug WAL-reset y Hermes cae a `journal_mode=DELETE`.
- **`agy` y `opencode` NUNCA son cards del board.** La card es un perfil Hermes que los envuelve como método de ejecución. Restricción explícita del skill `antigravity-cli` ("Orchestration boundary").
- **`create_task(initial_status=...)` solo acepta `blocked` o `running`.** El estado inicial lo decide Hermes según los padres.
- Todos los tests nuevos van en `A:\Proyectos\orquester\tests\`, junto a `test_contract.py` y `test_dag_rombo.py`.

---

## Tarea 0: Preparación del entorno

**Quién:** pasos marcados **[TÚ]** los haces vos; los **[YO]** los ejecuto yo.

**Files:**
- Crear: `A:\Proyectos\orquester\.env` (solo secretos)
- Crear: `~\.hermes\config.yaml` (comportamiento)

- [ ] **Paso 1 [TÚ]: Conseguir la key de OpenCode Zen**

Entrá a https://opencode.ai/zen, generá una API key y pegámela. Alternativa si ya usás Zen desde la app de OpenCode: la key está en la config de la app, buscala ahí.

- [ ] **Paso 2 [YO]: Instalar Hermes**

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

Esperado: instala uv, Python 3.11, Node.js, ripgrep, ffmpeg y un Git Bash portátil bajo `%LOCALAPPDATA%\hermes`.

Nota: si el antivirus pone en cuarentena `%LOCALAPPDATA%\hermes\bin\uv.exe`, es un falso positivo conocido (Rust sin firmar). Está documentado en el README de Hermes.

- [ ] **Paso 3 [YO]: Instalar el CLI de OpenCode**

```powershell
npm i -g opencode-ai@latest
opencode --version
```

Esperado: imprime versión. Esto es distinto de la app `OpenCode.exe` que ya tenés instalada: la app es Electron y no sirve para orquestación headless.

- [ ] **Paso 4 [TÚ]: Autenticar OpenCode**

```powershell
opencode auth login
opencode auth list
```

Esperado: `auth list` muestra al menos un proveedor. Abre navegador, por eso lo hacés vos.

- [ ] **Paso 5 [YO]: Conseguir el CLI de Antigravity (`agy`)**

Estado comprobado: el IDE instalado solo expone `C:\Users\santi\AppData\Local\Programs\Antigravity IDE\_\bin\antigravity-ide.cmd`, que es el lanzador del IDE (equivalente a `code.cmd`), **no** `agy`. `agy install` existe pero es el auto-instalador del propio wrapper: requiere tener `agy` ya presente.

Orden de intentos:
1. Abrir el IDE y buscar en la paleta de comandos una acción tipo "Install CLI command in PATH" (patrón heredado de VS Code).
2. Revisar la doc oficial de Antigravity por un instalador standalone de `agy`.
3. Buscar si viene por npm o por un installer script.

Verificar:

```powershell
agy --version
agy models
```

Esperado: `agy models` lista los strings exactos de modelo (ej. `'Gemini 3.1 Pro (High)'`, `'Claude Opus 4.6 (Thinking)'`). Guardar uno para la Tarea 6.

**Condición de parada:** si tras los tres intentos no hay `agy` distribuible, parar y reportar. La Tarea 5 queda fuera del alcance y la Tarea 6 corre con tres backends en vez de cuatro (D pasa a `claude-code`). El resto del plan no depende de esto.

- [ ] **Paso 6 [TÚ]: Autenticar Antigravity**

```powershell
agy
```

Antigravity maneja su auth por keyring del SO / login de navegador. Seguí el flujo y salí con Ctrl+C.

- [ ] **Paso 7 [YO]: Configurar el proveedor de Hermes**

```powershell
hermes auth
```

Elegir `opencode-zen`, pegar la key del Paso 1. Verificar que quedó en `.env` y no en `config.yaml` (es un secreto).

- [ ] **Paso 8 [YO]: Smoke test del runtime**

```powershell
hermes chat -q "Responde exactamente: LISTO" -Q
```

Esperado: la salida contiene `LISTO`, sin banner ni spinner. **Este paso cierra la fila "E2E con LLM real" de §10.**

- [ ] **Paso 9 [YO]: Poner el proyecto bajo git**

```powershell
cd A:\Proyectos\orquester
git init
git add -A
git commit -m "chore: specs v2, tests de contrato y DAG, plan de verificacion"
```

`orquester/` no es repo hoy. Sin git no hay commits por tarea ni forma de revertir. `hermes-agent/` ya trae su propio `.git`; agregarlo a `.gitignore` para no anidar repos.

---

## Tarea 1: `delegate_task` con `output_schema` contra un LLM real

Cierra la brecha entre `test_contract.py` (que probó el validador aislado) y el comportamiento real del modelo.

**Files:**
- Crear: `tests/test_delegate_e2e.md` (guion de prueba + salida observada)

**Interfaces:**
- Consume: entorno de la Tarea 0 (Hermes autenticado)
- Produce: confirmación de que un hijo real respeta el `OUTPUT CONTRACT` y de que un fallo dispara exactamente 1 reintento

- [ ] **Paso 1: Lanzar una delegación con schema**

```powershell
hermes chat -Q -q "Usa delegate_task con este output_schema exacto: {\"type\":\"object\",\"properties\":{\"status\":{\"type\":\"string\",\"enum\":[\"success\",\"failure\"]},\"summary\":{\"type\":\"string\"}},\"required\":[\"status\",\"summary\"],\"additionalProperties\":false} y goal: 'Conta cuantos archivos .md hay en el directorio actual y responde con el contrato'. Devolveme el JSON del hijo tal cual."
```

Esperado: un objeto JSON con `status` y `summary`, sin campos extra.

- [ ] **Paso 2: Verificar el rechazo y el reintento único**

Repetir con un schema que el modelo tienda a violar (agregar `required: ["status","summary","file_count"]` sin explicar `file_count`) y revisar los logs:

```powershell
hermes chat -Q -q "<mismo prompt con file_count requerido>" -v
```

Esperado: aparece un turno de reintento con los errores verbatim, y **solo uno**. Si aparecen dos o más, `MAX_SCHEMA_RETRIES` no está gobernando el path real y hay que investigar antes de seguir.

- [ ] **Paso 3: Registrar la salida observada**

Guardar prompt, salida y conteo de reintentos en `tests/test_delegate_e2e.md`.

- [ ] **Paso 4: Commit**

```powershell
git add tests/test_delegate_e2e.md
git commit -m "test: e2e de delegate_task con output_schema contra LLM real"
```

---

## Tarea 2: Un worker real ejecuta una tarea del board

**Files:**
- Crear: `tests/test_kanban_worker.md`

**Interfaces:**
- Consume: Tarea 0
- Produce: board `orquester-test` con al menos una tarea en `done` ejecutada por un worker

- [ ] **Paso 1: Crear el board y una tarea**

```powershell
hermes kanban init --board orquester-test
hermes kanban create --board orquester-test --title "Escribir hola.txt con la palabra HOLA" --assignee default
hermes kanban list --board orquester-test
```

Esperado: la tarea aparece en `ready` (sin padres).

- [ ] **Paso 2: Correr una pasada del dispatcher**

```powershell
hermes kanban dispatch --board orquester-test
```

Esperado: reclama la tarea, hace spawn de un worker y la mueve a `running`.

- [ ] **Paso 3: Observar hasta el cierre**

```powershell
hermes kanban tail --board orquester-test
hermes kanban show --board orquester-test <task_id>
```

Esperado: la tarea llega a `done`, existe `hola.txt` en el workspace, y `task_runs` tiene una fila con el resultado. **Cierra la fila "Un worker real ejecuta una tarea del board" de §10.**

- [ ] **Paso 4: Commit**

```powershell
git add tests/test_kanban_worker.md
git commit -m "test: worker real ejecutando una tarea del board"
```

---

## Tarea 3: Nodo Claude Code

Claude Code es el de mejor encaje: acepta el JSON Schema en la propia invocación, así que el contrato se empuja al agente en vez de validarse después.

**Files:**
- Modificar: `~\.hermes\config.yaml` (perfil `nodo-claude`)
- Crear: `tests/test_nodo_claude.md`

**Interfaces:**
- Consume: Tarea 2 (board funcionando)
- Produce: patrón `assignee` + `skills=[...]` validado, reutilizado por las Tareas 4 y 5

- [ ] **Paso 1: Probar la invocación cruda primero**

Antes de meter Hermes en el medio, confirmar que el CLI hace lo que la doc dice:

```powershell
cd A:\Proyectos\orquester
claude -p "Conta los archivos .md en este directorio" --output-format json --json-schema '{\"type\":\"object\",\"properties\":{\"status\":{\"type\":\"string\",\"enum\":[\"success\",\"failure\"]},\"summary\":{\"type\":\"string\"}},\"required\":[\"status\",\"summary\"]}' --max-turns 3
```

Esperado: JSON que valida contra el schema. Si esto falla, el problema es del CLI y no de Hermes: arreglarlo acá antes de seguir.

- [ ] **Paso 2: Crear la tarea con la skill precargada**

```powershell
hermes kanban create --board orquester-test --title "Via claude-code: contar archivos .md y devolver el contrato" --assignee default --skills claude-code
```

- [ ] **Paso 3: Despachar y verificar**

```powershell
hermes kanban dispatch --board orquester-test
hermes kanban tail --board orquester-test
```

Esperado: el worker carga la skill `claude-code`, invoca `claude -p` por `terminal`, y la tarea cierra con un summary que cumple el contrato.

- [ ] **Paso 4: Registrar y commitear**

```powershell
git add tests/test_nodo_claude.md
git commit -m "test: nodo claude-code sobre el board"
```

---

## Tarea 4: Nodo OpenCode

**Files:**
- Crear: `tests/test_nodo_opencode.md`

**Interfaces:**
- Consume: Tarea 3 (patrón validado)
- Produce: confirmación de que `--format json` alcanza para el handoff

- [ ] **Paso 1: Invocación cruda**

```powershell
cd A:\Proyectos\orquester
opencode run "Conta los archivos .md en este directorio y responde en una linea" --format json
```

Esperado: eventos JSON. OpenCode **no** tiene `--json-schema`, así que el contrato lo impone Hermes.

- [ ] **Paso 2: Tarea con la skill**

```powershell
hermes kanban create --board orquester-test --title "Via opencode: contar archivos .md y devolver el contrato" --assignee default --skills opencode
hermes kanban dispatch --board orquester-test
hermes kanban tail --board orquester-test
```

Esperado: cierra en `done` con summary conforme al contrato, aunque OpenCode no lo haya generado estructurado.

- [ ] **Paso 3: Commit**

```powershell
git add tests/test_nodo_opencode.md
git commit -m "test: nodo opencode sobre el board"
```

---

## Tarea 5: Nodo Antigravity

**Files:**
- Crear: `tests/test_nodo_antigravity.md`

**Interfaces:**
- Consume: Tarea 4
- Produce: el caso más duro — salida en texto plano normalizada al contrato

Recordar la restricción: la card la ejecuta un perfil Hermes que envuelve `agy`. `agy` nunca es la card.

- [ ] **Paso 1: Invocación cruda**

```powershell
cd A:\Proyectos\orquester
agy -p "Conta los archivos .md en este directorio y responde en una linea" --print-timeout 5m
```

Esperado: **texto plano**. No hay `--output-format json` ni `--max-turns`; el acotamiento es por `--print-timeout` (default 5m).

- [ ] **Paso 2: Instalar la skill opcional**

```powershell
hermes skills install antigravity-cli
```

Está en `optional-skills/`, no viene cargada por defecto como `opencode` y `claude-code`.

- [ ] **Paso 3: Tarea con la skill**

```powershell
hermes kanban create --board orquester-test --title "Via antigravity: contar archivos .md y devolver el contrato" --assignee default --skills antigravity-cli
hermes kanban dispatch --board orquester-test
hermes kanban tail --board orquester-test
```

Esperado: el worker parsea stdout plano y lo normaliza al contrato. Este es el test que prueba que la homogeneización la hace Hermes y no el agente externo.

- [ ] **Paso 4: Commit**

```powershell
git add tests/test_nodo_antigravity.md
git commit -m "test: nodo antigravity sobre el board"
```

---

## Tarea 6: DAG rombo heterogéneo end-to-end

La prueba que valida la tesis completa del producto.

**Files:**
- Crear: `tests/test_dag_heterogeneo.py`

**Interfaces:**
- Consume: Tareas 1 a 5
- Produce: evidencia para la fila final de §10

```
A  claude-code   'disena el plan de refactor'
   ├── B  opencode      'implementa el modulo'
   └── C  hermes nativo 'actualiza los docs'
        └── D  antigravity  'revisa el resultado'
```

- [ ] **Paso 1: Escribir el test que arma el grafo**

```python
# tests/test_dag_heterogeneo.py
import sys, subprocess
sys.path.insert(0, r"A:/Proyectos/orquester/hermes-agent")
import hermes_cli.kanban_db as k

BOARD = "orquester-mixto"
conn = k.connect(board=BOARD)

A = k.create_task(conn, title="Disenar el plan de refactor", skills=["claude-code"])
B = k.create_task(conn, title="Implementar el modulo", parents=[A], skills=["opencode"])
C = k.create_task(conn, title="Actualizar los docs", parents=[A])
D = k.create_task(conn, title="Revisar el resultado", parents=[B, C], skills=["antigravity-cli"])

assert k.get_task(conn, A).status == "ready"
assert all(k.get_task(conn, t).status == "todo" for t in (B, C, D))
print(f"Grafo creado en board {BOARD}: A={A} B={B} C={C} D={D}")
```

- [ ] **Paso 2: Correr el test de armado**

```powershell
cd A:\Proyectos\orquester\hermes-agent
uv run --python 3.11 python ..\tests\test_dag_heterogeneo.py
```

Esperado: los 3 asserts pasan y se imprimen los ids.

- [ ] **Paso 3: Ejecutar el grafo completo**

```powershell
hermes kanban daemon --board orquester-mixto
```

En otra terminal, observar:

```powershell
hermes kanban tail --board orquester-mixto
```

Esperado, en este orden: A corre y cierra; B y C arrancan **en paralelo**; D espera a los dos; D arranca solo cuando ambos cerraron.

- [ ] **Paso 4: Verificar el handoff de contexto**

```powershell
hermes kanban show --board orquester-mixto <D_id>
```

Esperado: el contexto de D contiene los summaries de B y C. Es lo mismo que `test_dag_rombo.py` probó en frío, ahora con workers reales de tres backends distintos.

- [ ] **Paso 5: Commit**

```powershell
git add tests/test_dag_heterogeneo.py
git commit -m "test: DAG rombo heterogeneo end-to-end con 4 backends"
```

---

## Tarea 7: Cerrar la tabla de verificación

**Files:**
- Modificar: `ARQUITECTURA.md` §10 y §4.1

- [ ] **Paso 1: Actualizar §10**

Mover a "Verificado (ejecutado)" las filas que las Tareas 1, 2 y 6 hayan cerrado, citando el test correspondiente. Lo que haya fallado se queda como "Pendiente" con el motivo. **No marcar nada verificado sin la salida real pegada.**

- [ ] **Paso 2: Corregir §4.1 con la restricción de Antigravity**

Agregar el límite del skill: los agentes externos son backends de ejecución, no primitivas de orquestación. La card del board siempre es un perfil Hermes; el agente externo es el método que ese perfil elige.

- [ ] **Paso 3: Commit**

```powershell
git add ARQUITECTURA.md
git commit -m "docs: cerrar tabla de verificacion con resultados ejecutados"
```

---

## Criterio de éxito del plan

El plan está completo cuando `ARQUITECTURA.md` §10 no tiene filas "Pendiente", o cuando las que quedan tienen escrito **por qué** fallaron. Un "Pendiente" sin explicación significa que el plan no terminó.

## Riesgos conocidos

| Riesgo | Señal temprana | Qué hacer |
|---|---|---|
| El CLI `agy` no se distribuye aparte del IDE | Tarea 0 Paso 5 falla | Parar y decidir: otra vía de integración, o Antigravity fuera del alcance |
| El worker de Hermes no propaga `--skills` al proceso hijo | Tarea 3 Paso 3: el worker ignora la skill | Leer el spawn del dispatcher; puede requerir configurar el perfil en vez de pasar `skills` por tarea |
| OpenCode Zen sin crédito o sin modelos | Tarea 0 Paso 8 falla con error de auth | Cambiar a `copilot-acp` o a una key de Anthropic |
| El modelo ignora el `OUTPUT CONTRACT` de forma consistente | Tarea 1 Paso 1 devuelve prosa | Subir la capacidad del modelo del supervisor; un modelo chico no conduce bien el loop |
| Rate limits al correr 3 backends en paralelo | Tarea 6 Paso 3 se traba en B o C | Bajar `delegation.max_concurrent_children` a 1 y correr en serie |
