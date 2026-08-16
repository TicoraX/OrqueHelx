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

## Concurrencia y reintentos

- `MAX_PARALELO` (3): cuántos nodos del carril corren a la vez. El techo lo pone
  el rate limit del proveedor de cada CLI, no la máquina.
- `MAX_INTENTOS` (2): reintentos de un fallo **transitorio**. Los fallos
  permanentes (binario ausente, runtime desconocido, flag de bypass) se bloquean
  como `capability` y no se reintentan nunca.
- El conteo de intentos sale de `list_runs`, así que sobrevive a un reinicio del
  dispatcher.
- Cuando los reintentos se agotan, Hermes enruta la card a `triage` por su
  propio cortacircuitos. Queda esperando decisión humana, no girando.
