# Test: Nodo Claude Code sobre el board Hermes

Tarea 3 del plan de verificación ORQUESTER. Objetivo: probar que una card del
kanban de Hermes puede delegar su trabajo a Claude Code (vía la skill bundleada
`claude-code`) y devolver un resultado bajo el mismo contrato que un nodo
nativo.

Entorno: cwd `A:\Proyectos\orquester`, hermes en
`C:\Users\santi\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`,
claude v2.1.233 en el PATH. Board `orquester-test` (ya existía, creado en la
Tarea 2). Proveedor Hermes sin tocar: `nous` / `upstage/solar-pro4:free`.

---

## Paso 1: CLI crudo de Claude Code, sin Hermes en el medio

Objetivo: separar fallas del CLI de Claude Code de fallas del cableado de
Hermes.

### Intento 1 — comillas dobles inline en Bash (git bash), sin permisos explícitos

```
cd "A:\Proyectos\orquester"
claude -p "Conta cuantos archivos .md hay en este directorio y responde con el contrato" --output-format json --json-schema "$(cat /tmp/schema.json)" --max-turns 3
```

Salida cruda (resumen — el JSON completo incluye metadata de uso/costo):

```json
{"is_error":true,...,"terminal_reason":"max_turns","subtype":"error_max_turns",
"errors":["Reached maximum number of turns (3)"],
"permission_denials":[
  {"tool_name":"PowerShell","tool_input":{"command":"(Get-ChildItem -Path \"A:\\Proyectos\\orquester\" -Filter *.md).Count; ...","description":"Count markdown files"}},
  {"tool_name":"PowerShell","tool_input":{"command":"(Get-ChildItem -Path . -Filter *.md -File).Name","description":"List top-level markdown files"}}
]}
```

**Veredicto: FALLA.** No es un problema de escapado del schema (el schema se
parseó bien) — el subproceso `claude -p` intentó usar la herramienta
`PowerShell` para contar archivos y quedó denegada por el sandbox de
permisos del propio Claude Code anidado (no vino de Hermes, vino de mi propio
entorno de ejecución). Se agotaron los 3 turnos esperando aprobación que
nunca llega en modo no interactivo.

### Intento 2 — mismo comando, `--allowedTools "Bash"`, `--max-turns 5`

Resultado: mismo patrón, pero ahora denegado específicamente el tool
`PowerShell` (Bash sí estaba permitido, pero el subproceso insiste en usar
PowerShell para `Get-ChildItem` en Windows). Se agotan igualmente los turnos.

**Veredicto: FALLA** (mismo motivo — falta permitir `PowerShell` también).

### Intento 3 — `--allowedTools "Bash PowerShell"`, `--max-turns 6`

```
claude -p "Conta cuantos archivos .md hay en este directorio (no recursivo) y responde con el contrato" --output-format json --json-schema "$(cat /tmp/schema.json)" --max-turns 6 --allowedTools "Bash PowerShell"
```

Salida cruda:

```json
{"is_error":false,"duration_api_ms":6428,"num_turns":3,"terminal_reason":"completed",
"subtype":"success",
"result":"{\"status\":\"success\",\"summary\":\"3 archivos .md en A:\\\\Proyectos\\\\orquester (no recursivo): ARQUITECTURA.md, ARQUITECTURA.v1.md, IDEAS.md\"}",
"structured_output":{"status":"success","summary":"3 archivos .md en A:\\Proyectos\\orquester (no recursivo): ARQUITECTURA.md, ARQUITECTURA.v1.md, IDEAS.md"}}
```

**Veredicto: PASA.** El JSON de salida valida contra el schema pedido
(`status` ∈ {success,failure}, `summary` string, ambos presentes).

### Escapado del schema — qué funcionó

- **Bash (git bash)**: guardar el schema en un archivo (`/tmp/schema.json`) y
  pasarlo como `--json-schema "$(cat /tmp/schema.json)"`. El inline con
  backslash-escaping de comillas (`'{\"type\":...}'`) tal como lo sugiere el
  brief para PowerShell no aplica igual en bash; la forma por archivo es la
  que funcionó sin fricción.
- **PowerShell (verificado aparte, ver abajo)**: guardar el schema en un
  archivo temporal y pasarlo con `Get-Content -Raw $schemaPath` como valor
  del parámetro — evita por completo el problema de escapado de comillas
  anidadas de PowerShell:

```powershell
$schemaPath = "$env:TEMP\schema_test.json"
'{"type":"object","properties":{"status":{"type":"string","enum":["success","failure"]},"summary":{"type":"string"}},"required":["status","summary"]}' | Set-Content -Path $schemaPath -NoNewline
$schemaContent = Get-Content -Raw $schemaPath
claude -p "..." --output-format json --json-schema $schemaContent --max-turns 6 --allowedTools "Bash PowerShell"
```

Salida cruda (resumen):

```json
{"is_error":false,"terminal_reason":"completed","subtype":"success",
"result":"{\"status\":\"success\",\"summary\":\"3 archivos .md en el nivel raíz de A:\\\\Proyectos\\\\ORQUESTER (no recursivo): ARQUITECTURA.md, ARQUITECTURA.v1.md, IDEAS.md. Verificado con Get-ChildItem -File -Filter *.md sin -Recurse.\"}",
"structured_output":{"status":"success","summary":"3 archivos .md en el nivel raíz de A:\\Proyectos\\ORQUESTER (no recursivo): ARQUITECTURA.md, ARQUITECTURA.v1.md, IDEAS.md. Verificado con Get-ChildItem -File -Filter *.md sin -Recurse."}}
```

**Veredicto: PASA.** Recomendación para la Tarea 4 (mismo truco que pide el
brief): **schema en archivo temporal + lectura por referencia**, nunca
inline con comillas anidadas — en ambos shells.

**Nota lateral, no bloqueante para esta tarea**: el `permission_denials` del
Intento 1/2 es un artefacto de que corro `claude -p` dentro de mi propio
Claude Code (sandbox anidado), no del proceso `claude` en sí. Cuando Hermes
invoca `claude -p` desde su propio worker (Paso 3), no hay ningún Claude Code
padre de por medio, así que este problema específico no debería reaparecer
ahí — y de hecho no reapareció (ver Paso 3).

---

## Paso 2: Card con la skill `claude-code` precargada

### Flag real de `--skills`

El brief asumía `--skills` (plural). El flag real, confirmado con
`hermes kanban create --help`, es `--skill` (singular, repetible):

```
--skill SKILLS        Skill to force-load into the worker (repeatable). The
                       kanban lifecycle is already injected automatically.
                       Example: --skill translation --skill github-code-
                       review
```

### Título como argumento posicional

Otro detalle no documentado en el brief: `title` es un **argumento
posicional**, no un flag `--title`. `--title "..."` da
`unrecognized arguments: --title`.

### Comando que funcionó

```powershell
$h = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
& $h kanban --board orquester-test create "Via claude-code: contar archivos .md y devolver el contrato" --assignee default --skill claude-code --json
```

Salida cruda:

```json
{
  "id": "t_54ddac57",
  "title": "Via claude-code: contar archivos .md y devolver el contrato",
  "assignee": "default",
  "status": "ready",
  "skills": ["claude-code"],
  ...
}
```

**Veredicto: PASA.** Card `t_54ddac57` creada con `skills: ["claude-code"]`.

---

## Paso 3: Despachar y verificar

```powershell
& $h kanban --board orquester-test dispatch
```

Salida cruda:

```
Reclaimed:    0
Crashed:      0
Timed out:    0
Stale:        0
Auto-blocked: 0
Promoted:     0
Spawned:      1
  - t_54ddac57  ->  default  @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_54ddac57
```

Poleo del board cada 15s con `hermes kanban --board orquester-test list`
(sin usar `tail`, tal como indica el protocolo de la tarea):

- t=0s: `running`
- t=15s: `running`
- t=30s: `done`

Cierra en ~30-46s, bien dentro del margen de "cortar a los 5 minutos sin
señales de avance".

### `kanban show` — eventos de la card

```
Task t_54ddac57: Via claude-code: contar archivos .md y devolver el contrato
  status:    done
  workspace: scratch @ C:\...\boards\orquester-test\workspaces\t_54ddac57
  skills:    claude-code
  started:   2026-08-15 00:55
  completed: 2026-08-15 00:56

Latest summary:
Conteó archivos .md vía claude-code: 0 archivos encontrados (workspace vacío).

Events (5):
  [00:55] created {..., 'skills': ['claude-code'], ...}
  [00:55] [run 4] claimed {'lock': 'DESKTOP-S1JMVFK:19804', 'run_id': 4}
  [00:55] [run 4] spawned {'pid': 10644}
  [00:55] [run 4] heartbeat
  [00:56] [run 4] completed {'result_len': 0, 'summary': 'Conteó archivos .md vía claude-code: 0 archivos encontrados (workspace vacío).'}

Runs (1):
  #4   completed    @default  46s  2026-08-15 00:55
        → Conteó archivos .md vía claude-code: 0 archivos encontrados (workspace vacío).
```

Los eventos por sí solos **no** prueban invocación real — solo confirman que
la skill estaba cargada y que el summary *dice* haber usado claude-code. Para
la prueba real hace falta el log de la corrida.

### Log crudo de la corrida — `logs/t_54ddac57.log`

Ruta completa:
`C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\logs\t_54ddac57.log`

Contenido completo (42 líneas, colores ANSI removidos para legibilidad):

```
Query: work kanban task t_54ddac57
Initializing agent...
────────────────────────────────────────

⚠ Deprecated .env settings detected:
  ⚠ TERMINAL_CWD=C:\...\workspaces\t_54ddac57 found in .env — this is deprecated.

  ┊ 📋 preparing kanban_show…
  ┊ ⚡ kanban_sh   0.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         ls -la + 1 command  1.1s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
El workspace está vacío. Voy a usar claude-code para contar los archivos .md como pide la tarea.
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ 💻 preparing terminal…
  ┊ 💻 $         claude -p 'count all .md files in the current directory and return just the count as a number, with a short summary' --allowedTools 'Read,Bash' --max-turns 5  13.1s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
Contrato:

- Archivos .md encontrados: 0
- Directorio escaneado: C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_54ddac57
- Resultado: el workspace está vacío, no hay archivos markdown.
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ ✔ preparing kanban_complete…
  ┊ ⚡ kanban_co   0.1s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
tarea completada: 0 archivos .md en el workspace. contrato devuelto.
╰──────────────────────────────────────────────────────────────────────────────╯

Session:        20260815_005535_297bed
Title:          Work kanban task t_54ddac57
Duration:       50s
Messages:       10 (1 user, 8 tool calls)
```

**Veredicto: PASA — hay evidencia positiva de invocación real.** La línea

```
┊ 💻 $         claude -p 'count all .md files in the current directory and return just the count as a number, with a short summary' --allowedTools 'Read,Bash' --max-turns 5  13.1s
```

es la traza de la herramienta `terminal` del worker ejecutando el binario
`claude` con `-p` y flags propios (`--allowedTools`, `--max-turns`), con
**13.1s de duración real** — consistente con una llamada real a la API de
Anthropic, no con un valor fabricado o instantáneo. El comando fue generado
por el propio worker (no viene precocinado en la skill ni en la card), lo
que descarta que sea un texto estático de plantilla.

El resultado "0 archivos .md" es **correcto**, no una falla oculta: verifiqué
por separado que el workspace scratch de la card
(`.../workspaces/t_54ddac57`) está vacío (`ls -la` → `total 0`, 0 entradas
además de `.`/`..`). El worker delegó a Claude Code, Claude Code escaneó el
directorio real que se le pasó como cwd y reportó correctamente que no hay
`.md` ahí.

### Hallazgo secundario (no bloqueante, documentado tal como pide el brief)

El worker **no** propagó el contrato JSON Schema de Claude Code
(`--output-format json --json-schema ...`, probado y validado en el Paso 1).
Invocó `claude -p` en modo texto plano y fue el propio Hermes quien tradujo
la respuesta de Claude Code al formato "Contrato:" / summary de la card. Es
decir: la skill `claude-code` demostradamente invoca el binario real, pero
la responsabilidad de emparejar el `AgentAdapterOutput` con el JSON Schema
del contrato recae en el prompt de la skill (cómo instruye al worker a
invocar `claude`), no en un mecanismo automático que le imponga
`--json-schema` al subproceso. Para las Tareas 4 y 5, si se quiere contrato
estructurado real end-to-end, hay que verificar si la skill de cada backend
empuja su propio flag de schema (Claude sí lo soporta vía `--json-schema`;
falta confirmar OpenCode/Antigravity) o si conviene forzarlo desde el
`--goal`/prompt de la card.

---

## Paso 4: Respuesta a la pregunta central

**¿El worker de Hermes invoca de verdad al binario `claude`, o resuelve la
tarea por su cuenta ignorando la skill?**

**Invoca de verdad al binario `claude`.** Evidencia:

1. El log crudo de la corrida (`logs/t_54ddac57.log`) muestra la línea de
   herramienta `terminal` ejecutando textualmente
   `claude -p '...' --allowedTools 'Read,Bash' --max-turns 5` con una
   duración medida de 13.1s — tiempo compatible con una llamada real a la
   API, no con una respuesta local instantánea.
2. El comando invocado fue generado dinámicamente por el modelo del worker
   (prompt en inglés, flags específicos), no es texto fijo de la skill ni de
   la card — descarta que sea una plantilla estática mostrada sin ejecutar
   nada.
3. El resultado reportado ("0 archivos .md, workspace vacío") es
   verificable independientemente: confirmé por fuera que el workspace
   scratch de la card está efectivamente vacío. Si el worker hubiera
   inventado el resultado sin invocar nada, no tenía por qué acertar el
   estado real de un directorio que nunca antes había existido con ese
   nombre.
4. El propagado de `--skill claude-code` en la creación de la card
   (`hermes kanban create --skill claude-code`) apareció reflejado en el
   evento `created` de la card (`'skills': ['claude-code']`) y en el log de
   la corrida ("Voy a usar claude-code para contar los archivos .md") —
   cadena causal completa desde la card hasta la invocación real.

Esta pregunta queda respondida en positivo con evidencia directa de log, no
solo por el estado `done` de la card (que, como advierte el brief, no
prueba nada por sí sola).

**Limitación honesta:** el log solo captura la línea de comando y su
duración, no el stdout/stderr crudo de `claude -p` (Hermes no lo vuelca al
log de la card). No pude confirmar el output textual exacto que devolvió el
subproceso `claude`, solo que se ejecutó y cuánto tardó. Es evidencia fuerte
pero indirecta sobre el contenido de la respuesta — no una prueba
criptográfica de que el texto "0 archivos .md" salió literalmente de
`claude` y no fue reinterpretado por el worker en el camino. Esto es una
hipótesis razonable respaldada por la duración de 13.1s y la coherencia del
resultado con el estado real del filesystem, no un hecho verificado al 100%.

---

## Resumen de veredictos

| Paso | Qué probaba | Veredicto |
|---|---|---|
| 1 | CLI crudo de Claude Code con `--json-schema` | PASA (tras corregir permisos de sandbox anidado, no relacionado con Hermes) |
| 2 | Crear card con skill `claude-code` precargada | PASA (flag real: `--skill`, título posicional) |
| 3 | Dispatch + verificación de invocación real | PASA — hay evidencia positiva en el log de la corrida |
| 4 (pregunta central) | ¿Invocó `claude` de verdad? | **Sí**, con evidencia de log (comando + duración real) |

## Desvíos documentados respecto al brief

- `--skills` (plural, como asumía el brief) no existe; es `--skill`
  (singular, repetible).
- `title` es posicional en `kanban create`, no `--title`.
- El truco de escapado de schema que funcionó en ambos shells fue
  **archivo temporal + lectura por referencia** (`$(cat file)` en bash,
  `Get-Content -Raw` en PowerShell), no el inline con backslash-escaping
  sugerido en el brief.
