# Dispatcher externo de ORQUESTER

Implementa ARQUITECTURA.md §12. Reclama las cards del carril
`orquester-external` y las ejecuta invocando el binario del agente externo.

**No programa nada.** El kanban de Hermes resuelve las dependencias y promueve
a `ready`; esto solo levanta trabajo ya programado.

## Por qué Python y no TypeScript

Todo el loop son llamadas a `kanban_db.py`: `claim_task`, `build_worker_context`,
`heartbeat_claim`, `complete_task`. Reescribir eso en TS significa reimplementar
el protocolo de claim contra la misma SQLite — el `BEGIN IMMEDIATE`, el TTL, el
invariante de padres. La fase de verificación encontró seis supuestos razonables
sobre Hermes que resultaron falsos; un claim protocol reescrito a mano sería el
séptimo, y ese corrompe estado.

NestJS lo arranca y lo supervisa como proceso.

## Uso

```bash
uv run --python 3.11 --with jsonschema python loop.py <board>
```

## Check

```bash
cd hermes-agent
uv run --python 3.11 --with jsonschema python ../tests/test_rebanada_vertical.py        # offline
uv run --python 3.11 --with jsonschema python ../tests/test_rebanada_vertical.py --e2e  # con opencode real
```
