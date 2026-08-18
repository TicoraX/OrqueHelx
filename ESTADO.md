# Estado de ORQUESTER

> Generado por `contexto/estado.py`. **No editar a mano**: se sobrescribe.
> Un traspaso escrito a mano queda viejo y nadie se entera.

**Fase:** con pendientes

## Dónde está el código

- Commit `732e98a` — fix: el check de portabilidad cazo una ruta absoluta en el test nuevo
- Remoto: https://github.com/TicoraX/orquester.git
- Árbol limpio: **no** · sin pushear: 0

## Runtime prestado

- Pin de Hermes: `b7f628025905` · el clon está en el pin: sí
- CLI instalado: v0.20.1
- Binarios: claude ✓, opencode ✓, agy ✓, hermes ✓, node ✓, docker ✓
- Postgres del plano de control: **no**

## Verificación

- `ARQUITECTURA.md` §10: **64 afirmaciones**, 1 pendiente(s)
- 16 tests: api_rbac, auth_studio, capacidades, chat, compilador, concurrencia_reintentos, consumo, contract, dag_heterogeneo, dag_rombo, disposicion, dos_dispatchers, mcp_export, pin_hermes, rebanada_vertical, ui_expansion

## Decisiones tomadas (no re-litigar sin motivo nuevo)

- **Motor prestado, sin fork** (§1) — Hermes se pinea por versión; el clon está en .gitignore y su working tree se verifica limpio.
- **El kanban es el único scheduler** (§12) — ORQUESTER ejecuta nodos externos pero no programa nada: es worker, no motor.
- **El dispatcher externo va en Python** (§12) — Usa kanban_db como librería. Reescribir el protocolo de claim en TS es el riesgo que §10 documenta.
- **El runtime del nodo va en el assignee** (§12) — `orquester-external:<rt>`. NO en `skills` (es contexto, no selector) ni en `metadata` (no existe al crear).
- **Dos bases, dos dueños** (§10.2) — Postgres: diseño y gobierno. kanban SQLite: ejecución. No es duplicación.
- **Las versiones de grafo son inmutables** (§10.2) — Editar crea la siguiente. Una corrida vieja tiene que explicarse con el grafo que se ejecutó.
- **Nada de proveedores external_process** (§11) — Con `copilot` todo agente hijo se cuelga para siempre en `Initializing agent...`.

## Bloqueos

- hay cambios sin commitear

## Qué sigue

- `docker compose up -d db` si vas a usar multiusuario
- §10 tiene 1 afirmación(es) pendiente(s)
- UI: el panel lateral ya no entra en una pantalla — la traza queda abajo de todo justo cuando se la mira (mover Estado/Traza arriba, o pestañas)
- conectar el Studio a la API multiusuario (hoy le habla directo al motor)
- medición empírica del cumplimiento del output_schema (diferida a propósito)

## Cómo retomar

```bash
uv run --python 3.11 --with langgraph --with langgraph-checkpoint-sqlite \
  python contexto/estado.py            # regenerar este archivo
sh tests/linux.sh                      # la suite en Linux
cat TUTORIAL.md                        # cómo se usa el producto
```

El historial de este contexto está en los checkpoints de LangGraph:
`python contexto/estado.py historial`.
