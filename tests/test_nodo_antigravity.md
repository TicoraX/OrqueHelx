# Test: Nodo Antigravity (`agy`) sobre el board

Fecha: 2026-08-15. cwd: `A:\Proyectos\orquester`.
Binario: `C:\Users\santi\AppData\Local\agy\bin\agy.exe` v1.1.13 (no está en PATH; se prepende).
Hermes: `C:\Users\santi\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe`.
Board: `orquester-test`. Proveedor Hermes: `nous` / `upstage/solar-pro4:free`.

Ground truth del directorio (medido antes de correr nada):

```
PS> (Get-ChildItem A:\Proyectos\orquester -Filter *.md -File).Name
ARQUITECTURA.md
ARQUITECTURA.v1.md
IDEAS.md

$ cd "A:/Proyectos/orquester" && find . -name "*.md" -type f 2>/dev/null | wc -l
1562
```

Nivel raíz = **3**. Recursivo = **1562**.

---

## Paso 0: superficie real del CLI v1.1.13

```powershell
$env:PATH = "$env:LOCALAPPDATA\agy\bin;$env:PATH"
agy --version
agy models
```

```
1.1.13
---
Fetching available models...
gemini-3.7-flash-high	Gemini 3.7 Flash (High)
gemini-3.7-flash-medium	Gemini 3.7 Flash (Medium)
gemini-3.7-flash-low	Gemini 3.7 Flash (Low)
gemini-3.6-flash-high	Gemini 3.6 Flash (High)
gemini-3.6-flash-medium	Gemini 3.6 Flash (Medium)
gemini-3.6-flash-low	Gemini 3.6 Flash (Low)
gemini-3.5-flash-high	Gemini 3.5 Flash (High)
gemini-3.5-flash-medium	Gemini 3.5 Flash (Medium)
gemini-3.5-flash-low	Gemini 3.5 Flash (Low)
gemini-3.1-pro-high	Gemini 3.1 Pro (High)
gemini-3.1-pro-low	Gemini 3.1 Pro (Low)
claude-sonnet-4-6	Claude Sonnet 4.6 (Thinking)
claude-opus-4-6-thinking	Claude Opus 4.6 (Thinking)
gpt-oss-120b-medium	GPT-OSS 120B (Medium)
```

`agy models` emite **dos columnas separadas por tab**: slug y display string. La
skill de Hermes documenta el display string como valor de `--model`
(`--model 'Gemini 3.1 Pro (High)'`); el slug es la columna izquierda. No se
ejercitó `--model` en esta tarea (se usó el default, `Gemini 3.7 Flash (High)`
según el log del CLI), así que **cuál de los dos acepta `--model` queda sin
verificar**. Los slugs del brief (`gemini-3.1-pro-high`, `claude-sonnet-4-6`)
existen y son correctos como slugs.

```powershell
agy help
```

```
Usage of agy.exe:
  --add-dir                       Add a directory to the workspace (repeatable) (default [])
  --agent                         Agent for the current CLI session
  -c                              Short alias for --continue
  --continue                      Continue the most recent conversation
  --conversation                  Resume a previous conversation by ID
  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting
  --disable-slash-commands        Disable slash command and skill expansion in print mode
  --effort                        Reasoning effort for the current CLI session (low|medium|high)
  -i                              Short alias for --prompt-interactive
  --json-schema                   Optional JSON schema string or path to a schema file to enforce structured output (for stream-json, only applicable to the final result)
  --log-file                      Override CLI log file path
  --mode                          Set the agent execution mode for this session (accept-edits, plan)
  --model                         Model for the current CLI session
  --new-project                   Create a new project for this session
  --output-format                 Output format for print mode (text, json, stream-json) (default text)
  -p                              Short alias for --print
  --print                         Run a single prompt non-interactively and print the response
  --print-timeout                 Timeout for print mode wait (default 5m0s)
  --project                       Project ID for the current CLI session
  --prompt                        Alias for --print
  --prompt-interactive            Run an initial prompt interactively and continue the session
  --sandbox                       Run in a sandbox with terminal restrictions enabled

Available subcommands:
  agent           List available agents
  agents          List available agents
  changelog       Show changelog and release notes
  help            Show help for subcommands
  install         Configure environment paths and shell settings
  models          List available models
  plugin          Manage plugins (install, uninstall, list, enable, disable)
  plugins         Alias for plugin
  update          Update CLI
```

**Veredicto: la skill `antigravity-cli` v0.2.0 está desactualizada.** Afirma
(líneas 106-113 y 220-221 del SKILL.md) que "`agy -p` returns **plain text** —
there is **no `--output-format json`** and no result envelope with `session_id`
/ cost / turn count". El help de v1.1.13 lista `--output-format (text, json,
stream-json)` y `--json-schema`. El Paso 1b confirma además que el envelope
existe y trae conversation_id, duration, num_turns y usage.

---

## Paso 1a: CLI crudo, texto plano

Primer intento, tal cual el brief:

```powershell
cd A:\Proyectos\orquester
$env:PATH = "$env:LOCALAPPDATA\agy\bin;$env:PATH"
agy -p "Conta cuantos archivos .md hay en este directorio y responde solo con el numero" --print-timeout 3m
```

```
jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied. Add an allow-rule under permissions.allow in settings.json (e.g. read_file(<target>)). Alternatively, re-run with --dangerously-skip-permissions to auto-approve all tools.
EXIT=0 ELAPSED=8.3237946s
```

**Hallazgo no previsto por el brief: `agy -p` en headless deniega toda
herramienta por defecto.** No hay `settings.json` preexistente. Exit code
**0** pese al fallo — `agy -p` no señaliza el error por exit status, así que un
wrapper que confíe en `$LASTEXITCODE` va a leer un fallo como éxito.

El log del CLI da la cadena de permiso exacta:

```
C:\Users\santi\.gemini\antigravity-cli\log\cli-20260815_121717.log
...
I0815 12:17:25.862359     311 tool_confirmation_manager.go:188] Print mode: soft-denying tool confirmation "ListDir" at step 3
I0815 12:17:25.862359    1033 server.go:2076] Tool confirmation for conversation cc86c500-... step 3 (type=*gemini_coder_go_proto.Step_ListDirectory approved=false)
E0815 12:17:25.863400    1092 permission_manager.go:966] permission check failed: permission check failed for read_file "A:\\Proyectos": user denied permission for read_file(A:\Proyectos)
```

Nota: pidió `read_file(A:\Proyectos)`, el **padre** del cwd, no el cwd.

Se creó el archivo de settings (no existía) con un allow acotado de sólo lectura
al proyecto, en vez de `--dangerously-skip-permissions`:

```json
// C:\Users\santi\.gemini\antigravity-cli\settings.json
{
  "permissions": {
    "allow": [
      "read_file(A:\\Proyectos\\orquester)"
    ]
  }
}
```

Rerun con la ruta explícita en el prompt (para no depender de la resolución de
"este directorio", que había apuntado al padre):

```powershell
agy -p "Lista los archivos del directorio A:\Proyectos\orquester (solo el nivel raiz, no recursivo), conta cuantos terminan en .md y responde solo con ese numero" --print-timeout 3m
```

```
3
EXIT=0 ELAPSED=11.5089929s
```

**Veredicto Paso 1a: PASA.** Salida `3`, coincide con el ground truth del nivel
raíz. Texto plano puro, sin envelope, sin prefijos. 11.5s.

---

## Paso 1b: contrato forzado con `--json-schema`

Schema usado (`AgentAdapterOutput`), en archivo temporal
`tests/_agy_schema.json` (borrado tras la corrida):

```json
{"type":"object","properties":{"status":{"type":"string","enum":["success","failure"]},"summary":{"type":"string"}},"required":["status","summary"]}
```

```powershell
agy -p "Conta cuantos archivos .md hay en el nivel raiz del directorio A:\Proyectos\orquester" --output-format json --json-schema A:\Proyectos\orquester\tests\_agy_schema.json --print-timeout 3m
```

Salida cruda (una sola línea, reformateada abajo para lectura; el original es
un único objeto JSON en una línea):

```
{"conversation_id":"077bb8e3-2b66-4ee7-a816-88b37807dae9","status":"SUCCESS","response":"Hay **3** archivos `.md` en el nivel raíz de `A:\\Proyectos\\orquester`:\n\n- [ARQUITECTURA.md](file:///A:/Proyectos/orquester/ARQUITECTURA.md)\n- [ARQUITECTURA.v1.md](file:///A:/Proyectos/orquester/ARQUITECTURA.v1.md)\n- [IDEAS.md](file:///A:/Proyectos/orquester/IDEAS.md)\n{\"status\":\"success\",\"summary\":\"Se contabilizaron 3 archivos .md en el nivel raíz del directorio A:\\\\Proyectos\\\\orquester: ARQUITECTURA.md, ARQUITECTURA.v1.md e IDEAS.md.\"}\n","duration_seconds":9.2211305,"num_turns":2,"structured_output":{"status":"success","summary":"Se contabilizaron 3 archivos .md en el nivel raíz del directorio A:\\Proyectos\\orquester: ARQUITECTURA.md, ARQUITECTURA.v1.md e IDEAS.md."},"json_schema":{"type":"object","properties":{"status":{"type":"string","enum":["success","failure"]},"summary":{"type":"string"}},"required":["status","summary"]},"usage":{"input_tokens":34427,"output_tokens":676,"thinking_tokens":452,"cache_read_tokens":48941,"total_tokens":35103}}
EXIT=0 ELAPSED=12.6999916s
```

Estructura del envelope, indentada:

```json
{
  "conversation_id": "077bb8e3-2b66-4ee7-a816-88b37807dae9",
  "status": "SUCCESS",
  "response": "<prosa markdown + el objeto del contrato concatenado al final>",
  "duration_seconds": 9.2211305,
  "num_turns": 2,
  "structured_output": {
    "status": "success",
    "summary": "Se contabilizaron 3 archivos .md en el nivel raíz ..."
  },
  "json_schema": { "...el schema que se pasó, ecoado..." },
  "usage": {
    "input_tokens": 34427, "output_tokens": 676, "thinking_tokens": 452,
    "cache_read_tokens": 48941, "total_tokens": 35103
  }
}
```

**Veredicto Paso 1b: PASA.** Respuestas a lo que el brief pedía medir:

- **¿`--json-schema` funciona de verdad?** Sí. `structured_output` cumple el
  schema exactamente: `status` dentro del enum, `summary` presente, sin claves
  extra. No hay que parsear prosa: el objeto viene ya separado.
- **¿objeto único o JSONL tipo OpenCode?** **Objeto único**, un solo JSON en
  stdout. `--output-format stream-json` existiría para el caso JSONL, y el
  propio help aclara que ahí el schema aplica "only to the final result".
- **Envelope:** existe y es rico — `conversation_id` (equivalente a session_id),
  `duration_seconds`, `num_turns`, `usage` con desglose de tokens y cache.

**Esto invierte la premisa del plan.** Antigravity no es "el caso duro de texto
plano": de los tres backends externos es **el más fácil de contractualizar**.
Es el único que acepta el JSON Schema del contrato *como parámetro del CLI*, en
vez de tener que meterlo como texto en el prompt.

Caveat honesto: el campo `response` **no** respeta el contrato — trae prosa
markdown con el objeto pegado al final. El schema sólo gobierna
`structured_output`. Un adapter debe leer `structured_output`, nunca `response`.

---

## Paso 2: instalar la skill opcional

```powershell
$h = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\hermes.exe"
& $h skills install antigravity-cli
```

```
Resolving 'antigravity-cli'...
Resolved to: official/autonomous-ai-agents/antigravity-cli

Fetching: official/autonomous-ai-agents/antigravity-cli
Quarantined to .hub\quarantine\antigravity-cli
Running security scan...
Scan: antigravity-cli (official/builtin)  Verdict: DANGEROUS
  CRITICAL supply_chain   references\cli-docs.md:9       "- macOS/Linux: `curl
-fsSL https://antigravity.google/cli/in"

Decision: ALLOWED — Allowed (builtin source, dangerous verdict)
...
Install 'antigravity-cli'?
Confirm [y/N]: Installation cancelled.
```

El subcomando existe tal cual lo escribe el brief, pero **pide confirmación
interactiva**; con stdin no interactivo cancela. `& $h skills install --help`
da el flag:

```
usage: hermes skills install [-h] [--category CATEGORY] [--name NAME]
                             [--force] [--yes]
                             identifier
  --yes, -y            Skip confirmation prompt (needed in TUI mode)
```

```powershell
& $h skills install antigravity-cli --yes
```

```
Resolving 'antigravity-cli'...
Resolved to: official/autonomous-ai-agents/antigravity-cli

Fetching: official/autonomous-ai-agents/antigravity-cli
Quarantined to .hub\quarantine\antigravity-cli
Running security scan...
Scan: antigravity-cli (official/builtin)  Verdict: DANGEROUS
  CRITICAL supply_chain   references\cli-docs.md:9       "- macOS/Linux: `curl
-fsSL https://antigravity.google/cli/in"

Decision: ALLOWED — Allowed (builtin source, dangerous verdict)
Scan provenance: cached; scanner skills-guard-v1; hash
sha256:7c5c9609254e7d2f077791f1a51340f4ade522ca9459a2fc06b38f8fbd457e90
Source: official/autonomous-ai-agents\antigravity-cli; scanned
2026-08-15T17:21:18.404905+00:00; rules: curl_pipe_shell
Installed: antigravity-cli
Files: SKILL.md, references\cli-docs.md
```

**Veredicto Paso 2: PASA con corrección de sintaxis.** Forma real:
`hermes skills install <nombre> --yes`. Sin `--yes` no instala en modo no
interactivo. Nota de gobierno: el scanner marca la skill **DANGEROUS**
(`curl_pipe_shell` en la doc de instalación) y la deja pasar por ser builtin —
un bypass basado en procedencia, no en contenido.

---

## Paso 3: la card

```powershell
& $h kanban --board orquester-test create "Via antigravity: contar archivos .md en A:\Proyectos\orquester y devolver el contrato" --assignee default --skill antigravity-cli
```

```
Created t_8160e4fd  (ready, assignee=default)

⚠  No gateway is running — the task will sit in 'ready' until you start it. Run:
    hermes gateway start
The gateway hosts an embedded dispatcher (tick interval 60s by default); your task will be picked up on the next tick after the gateway comes up.
```

```powershell
& $h kanban --board orquester-test dispatch
```

```
Reclaimed:    0
Crashed:      0
Timed out:    0
Stale:        0
Auto-blocked: 0
Promoted:     0
Spawned:      1
  - t_8160e4fd  ->  default  @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_8160e4fd
EXIT=0 ELAPSED=1.6944391s
```

```powershell
& $h kanban --board orquester-test show t_8160e4fd
```

```
Task t_8160e4fd: Via antigravity: contar archivos .md en A:\Proyectos\orquester y devolver el contrato
  status:    done
  assignee:  default
  workspace: scratch @ C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_8160e4fd
  skills:    antigravity-cli
  max-retries: 2 (default)
  created:   2026-08-15 12:21 by user
  started:   2026-08-15 12:22
  completed: 2026-08-15 12:25

Latest summary:
Via antigravity (agy 1.1.13): contado 1562 archivos .md en A:/Proyectos/orquester. Contrato JSON devuelto por stdout con total_md_files=1562 y la lista completa de archivos. Primer intento falló por permission denial (command); resuelto con --dangerously-skip-permissions.

Events (8):
  [2026-08-15 12:21] created {'assignee': 'default', 'status': 'ready', 'parents': [], 'tenant': None, 'workspace_kind': 'scratch', 'workspace_path': None, 'branch_name': None, 'project_id': None, 'skills': ['antigravity-cli'], 'goal_mode': None, 'model_override': None, 'provider_override': None}
  [2026-08-15 12:22] [run 6] claimed {'lock': 'DESKTOP-S1JMVFK:29916', 'expires': 1786815420, 'run_id': 6}
  [2026-08-15 12:22] [run 6] spawned {'pid': 13816}
  [2026-08-15 12:22] [run 6] heartbeat
  [2026-08-15 12:23] [run 6] heartbeat
  [2026-08-15 12:24] [run 6] heartbeat
  [2026-08-15 12:25] [run 6] heartbeat
  [2026-08-15 12:25] [run 6] completed {'result_len': 0, 'summary': 'Via antigravity (agy 1.1.13): contado 1562 archivos .md en A:/Proyectos/orquester. Contrato JSON devuelto por stdout con total_md_files=1562 y la lista completa de archivos. Primer intento falló por permission denial (command); resuelto con --dangerously-skip-permissions.'}

Runs (1):
  #6   completed    @default  203s  2026-08-15 12:22
        → Via antigravity (agy 1.1.13): contado 1562 archivos .md en A:/Proyectos/orquester. Contrato JSON devuelto por stdout con total_md_files=1562 y la lista completa
```

### La evidencia load-bearing

`C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\logs\t_8160e4fd.log`
(52 líneas; se pega el bloque de tool calls y el cierre, sin códigos ANSI):

```
Query: work kanban task t_8160e4fd
Initializing agent...
────────────────────────────────────────

⚠ Deprecated .env settings detected:
  ⚠ TERMINAL_CWD=C:\Users\santi\AppData\Local\hermes\kanban\boards\orquester-test\workspaces\t_8160e4fd found in .env — this is deprecated.
  Move to config.yaml instead:  terminal:\n    cwd: /your/project/path
  Then remove the old entries from ~/AppData\Local\hermes/.env

  ┊ 📋 preparing kanban_show…
  ┊ ⚡ kanban_sh   0.0s
  ┊ 💻 preparing terminal…
  ┊ 💻 preparing terminal…
  ┊ 💻 $         ls -la "A:/Proyectos/orquester" + 1 command  1.2s
  ┊ 💻 $         command -v agy + 1 command  0.4s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         find "A:/Proyectos/orquester" -name "*.md" -type f 2>/dev/null  0.6s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         agy -p 'Count all .md files under A:/Proyectos/orquester and return a JSON contract with keys: total_md_files (integer), files (array of relative paths from A:/Proyectos/orquester). Do NOT include any other text.' --print-timeout 20m  10.8s
  ┊ 💻 preparing terminal…
  ┊ 💻 $         agy -p 'Count all .md files under A:/Proyectos/orquester and return a JSON contract with keys: total_md_files (integer), files (array of relative paths from A:/Proyectos/orquester). Do NOT include any other text.' --dangerously-skip-permissions --print-timeout 20m  124.8s
  (tip) That tool ran for a while. Use /verbose to cycle tool-progress display modes (all -> new -> off -> verbose). This tip only shows once.

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
Antigravity (agy 1.1.13) ejecutó la cuenta. Resultado:

total_md_files: 1562
(files: array de 1562 rutas relativas — se devolvió completa en el contrato JSON por stdout)

Resumen ejecutivo:
- Directorio: A:/Proyectos/orquester
- Agente: antigravity (agy) v1.1.13
- Primer intento falló por permissions.allow (jet ski: command permission denied en headless). Solucionado con --dangerously-skip-permissions.
- Contrato JSON devuelto por stdout con total_md_files=1562 y la lista completa de archivos .md.

Marcando tarea como completada.
╰──────────────────────────────────────────────────────────────────────────────╯
  ┊ ✔ preparing kanban_complete…
  ┊ ⚡ kanban_co   0.0s

╭─ ⚕ Hermes ───────────────────────────────────────────────────────────────────╮
tarea completada. antigravity (agy 1.1.13) contó 1562 archivos .md en A:\Proyectos\orquester y devolvió el contrato JSON con el campo total_md_files=1562 y la lista completa de archivos.
╰──────────────────────────────────────────────────────────────────────────────╯

Resume this session with:
  hermes --resume 20260815_122202_4a2daf
  hermes -c "Work on kanban task t_8160e4fd"

Session:        20260815_122202_4a2daf
Title:          Work on kanban task t_8160e4fd
Duration:       3m 27s
Messages:       15 (1 user, 13 tool calls)
```

**Veredicto Paso 3: PASA.** La línea de 124.8s es la prueba: el worker de Hermes
invocó el binario `agy` de verdad, con su propio prompt, y esperó su salida.
Tercer backend externo confirmado, mismo patrón que Tareas 3 y 4.

### PATH heredado por el worker: sin problema

`command -v agy` (0.4s, línea 15 del log) resolvió. El worker heredó el PATH
inyectado en la sesión que corrió `dispatch`. **No hubo falla de PATH.** Queda
sin verificar si funcionaría desde un shell sin la inyección — el dispatch se
lanzó siempre con `$env:PATH = "$env:LOCALAPPDATA\agy\bin;$env:PATH"` por
delante.

---

## Hallazgos que contradicen o completan lo que el brief daba por sentado

1. **`agy -p` en headless deniega toda herramienta por defecto** y no había
   `settings.json`. El brief no lo anticipaba. Dos salidas: allow-rule acotada
   en `settings.json` (lo que se hizo acá), o
   `--dangerously-skip-permissions` (lo que eligió el worker).

2. **Exit code 0 en fallo.** `agy -p` devolvió `EXIT=0` con la salida
   `jetski: no output produced — ...`. Un `IAgentAdapter` que use el exit status
   como señal de éxito va a marcar success sobre un fallo total. Hay que
   validar el contenido, o usar `--output-format json` y leer
   `status`/`structured_output`.

3. **El worker NO usó `--output-format json` ni `--json-schema`.** Armó el
   contrato como texto dentro del prompt (`return a JSON contract with keys:
   ...`), exactamente el patrón de las Tareas 3 y 4. Causa directa: la skill
   instalada le dice explícitamente que esos flags **no existen**. Es decir, la
   skill desactualizada no es sólo documentación vieja: **degrada activamente el
   comportamiento del worker**, empujándolo al camino frágil de parsear prosa
   cuando el CLI ofrece structured output nativo. Corolario para ORQUESTER: el
   adapter de Antigravity debe pasar el `output_schema` de la card por
   `--json-schema` y leer `structured_output`, y hay que corregir el SKILL.md.

4. **La resolución de "este directorio" no es el cwd.** El primer intento pidió
   permiso sobre `A:\Proyectos` (el padre). Pasar la ruta absoluta en el prompt,
   o usar `--add-dir`.

5. **Ambigüedad del goal, otra vez.** El título de la card decía "contar
   archivos .md en `A:\Proyectos\orquester`" sin decir si recursivo. El worker
   eligió recursivo y contó 1562, que es correcto para esa lectura
   (`find . -name "*.md" | wc -l` = 1562). El nivel raíz es 3. El número no
   está mal; el goal estaba subespecificado. Misma lección que la Tarea 4.

6. **Contaminación de la evidencia numérica.** El worker corrió su propio
   `find ... -name "*.md"` (línea 17 del log) **antes** de llamar a `agy`. El
   1562 pudo salir de ahí. Lo que prueba la invocación de `agy` es la línea de
   124.8s, no el número.

7. **`hermes skills install` necesita `--yes`** en modo no interactivo.

8. **El scanner de skills marca `antigravity-cli` como DANGEROUS** y la instala
   igual por ser builtin. Vale registrarlo como nota de gobierno.

---

## Criterio de aceptación

- **(a) ¿El worker invocó `agy` de verdad?** **Sí.** Log `t_8160e4fd.log`,
  dos invocaciones (10.8s denegada, 124.8s exitosa) con el comando completo.
- **(b) ¿`--json-schema` de `agy` v1.1.13 existe y funciona?** **Sí, y bien.**
  Objeto JSON único con `structured_output` que cumple el schema al pie de la
  letra. Antigravity resulta el backend externo **más fácil** de contractualizar
  de los tres, no el más duro.

## Estado modificado fuera del repo

- Creado `C:\Users\santi\.gemini\antigravity-cli\settings.json` (no existía) con
  un único allow de sólo lectura sobre `A:\Proyectos\orquester`.
- Instalada la skill `antigravity-cli` en
  `C:\Users\santi\AppData\Local\hermes\skills\antigravity-cli\`.
- Card `t_8160e4fd` en el board `orquester-test`, estado `done`. Su workspace
  scratch fue borrado al completar, como documenta el brief.
