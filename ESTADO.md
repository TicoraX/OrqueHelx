# Estado de ORQUESTER

> Generado por `contexto/estado.py`. **No editar a mano**: se sobrescribe.
> Un traspaso escrito a mano queda viejo y nadie se entera.

**Fase:** con pendientes

## Dónde está el código

- Commit `b1abbb7` — fix(runtime): el board se normaliza como en el kanban, y el arranque toma lock
- Remoto: https://github.com/TicoraX/orquester.git
- Árbol limpio: sí · sin pushear: 1

## Runtime prestado

- Pin de Hermes: `b7f628025905` · el clon está en el pin: sí
- CLI instalado: v0.20.1
- Binarios: claude ✓, opencode ✓, agy ✓, hermes ✓, node ✓, docker ✓
- Postgres del plano de control: **no**

## Verificación

- `ARQUITECTURA.md` §10: **70 afirmaciones**, 1 pendiente(s)
- 23 tests: api_rbac, auth_studio, capacidades, chat, cli, compilador, concurrencia_reintentos, consumo, contract, contrato_ui, dag_heterogeneo, dag_rombo, disposicion, dos_dispatchers, esfuerzo_presupuesto, guardarrailes, mcp_export, modo_app, parada, pin_hermes, rebanada_vertical, ui_expansion, ui_navegador

## Decisiones tomadas (no re-litigar sin motivo nuevo)

- **Motor prestado, sin fork** (§1) — Hermes se pinea por versión; el clon está en .gitignore y su working tree se verifica limpio.
- **El kanban es el único scheduler** (§12) — ORQUESTER ejecuta nodos externos pero no programa nada: es worker, no motor.
- **El dispatcher externo va en Python** (§12) — Usa kanban_db como librería. Reescribir el protocolo de claim en TS es el riesgo que §10 documenta.
- **El runtime del nodo va en el assignee** (§12) — `orquester-external:<rt>`. NO en `skills` (es contexto, no selector) ni en `metadata` (no existe al crear).
- **Dos bases, dos dueños** (§10.2) — Postgres: diseño y gobierno. kanban SQLite: ejecución. No es duplicación.
- **Las versiones de grafo son inmutables** (§10.2) — Editar crea la siguiente. Una corrida vieja tiene que explicarse con el grafo que se ejecutó.
- **Nada de proveedores external_process** (§11) — Con `copilot` todo agente hijo se cuelga para siempre en `Initializing agent...`.
- **Plantillas y grafos son carpetas distintas** (TUTORIAL §5b) — `plantillas/` va en el repo y es de solo lectura; usar una la COPIA a `ui/grafos/`. Si fueran el mismo lugar, un `git pull` que mejore una plantilla pisaría el trabajo hecho encima.
- **Lo que una plantilla requiere se DERIVA de sus nodos** (TUTORIAL §5b) — No se declara aparte: una lista escrita a mano se desincroniza el primer día que alguien cambia un ejecutor.
- **Una nota no puede tener dependencias** (compiler/compile.py) — El compilador RECHAZA la arista en vez de ignorarla: `a → nota → b` se vería conectado en el lienzo y `b` arrancaría sin esperar a `a`. Un error visible es mejor que un DAG que miente.
- **Las reglas del flujo van en el `body`, no en el título** (compiler/compile.py) — `build_worker_context` entrega el body junto al goal, y así el título sigue siendo legible en el lienzo y en el kanban.
- **El esfuerzo no necesitó campo nuevo** (§10.5) — `reasoning_effort` ya existe en la card de Hermes, igual que `model_override`. Un campo, dos consumidores: su dispatcher y el nuestro.
- **Los niveles de esfuerzo se validan en el compilador** (§10.5) — No son iguales en los tres CLIs (`max` no existe en agy). Pedir un nivel y que corra en el default es pagar por trabajo que no se pidió.
- **Un tope de gasto NUNCA se simula** (§10.5) — Solo se pasa al CLI que lo entiende (claude-code). En los demás corta el dispatcher ENTRE nodos: lo que ya arrancó termina. Un tope que se cree puesto y no lo está es peor que no tener tope.
- **El Studio no persiste conversaciones** (§10.4) — La sesión del chat la guarda el CLI; por la API viaja solo el id. Una sesión por runtime: el id de claude no significa nada para opencode.
- **El chat pasa por `loop.run_chat`** (§10.4) — Concede las herramientas del carril y hereda la denylist: un chat es un agente con shell igual que un nodo.
- **El acomodo del grafo se calcula en Python** (compiler/disposicion.py) — El orden por capas (Kahn) ya existe en el compilador. Reimplementarlo en JS sería el mismo algoritmo dos veces y la segunda copia se desincroniza.
- **El nombre de archivo va por lista blanca** (ui/server.py) — Un `board: '../../x'` escribía .json fuera de ui/grafos y leía cualquier .json del disco. Explotado antes de arreglarlo; hay un test que lo intenta.
- **La suite de Python NO verifica la UI** (tests/) — Ningún test toca `ui/index.html`: los cambios de interfaz se verifican con Playwright en el navegador. Y `test_api_rbac` sale con código 0 cuando se omite por falta de Postgres, así que un runner que mire el exit code cuenta un test omitido como pasado.

## Bloqueos

- 1 commit(s) sin pushear

## Qué sigue

- `docker compose up -d db` si vas a usar multiusuario
- §10 tiene 1 afirmación(es) pendiente(s)
- conectar el Studio a la API multiusuario (hoy le habla directo al motor)
- medición empírica del cumplimiento del output_schema (diferida a propósito)
- tabla de rutas en ui/server.py: 34 endpoints en dos cadenas de `if`

## Cómo retomar

```bash
uv run --python 3.11 --with langgraph --with langgraph-checkpoint-sqlite \
  python contexto/estado.py            # regenerar este archivo
sh tests/linux.sh                      # la suite en Linux
cat TUTORIAL.md                        # cómo se usa el producto
```

El historial de este contexto está en los checkpoints de LangGraph:
`python contexto/estado.py historial`.
