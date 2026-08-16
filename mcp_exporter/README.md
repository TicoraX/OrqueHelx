# Exportador MCP

Publica un flujo del Studio como un servidor MCP, para invocarlo desde Claude
Desktop, Cursor o cualquier cliente MCP.

Es uno de los dos diferenciadores de `IDEAS.md` §1: el servidor MCP que trae
Hermes expone **mensajería** (conversaciones, envío de mensajes), no ejecución
de flujos. Quien quiera disparar un pipeline desde su editor no tiene cómo.

## Parámetros: se escriben en el goal

Los `{{marcadores}}` que aparezcan en el título o el cuerpo de un nodo se
vuelven los parámetros de la tool. No se declaran aparte — escribir el goal ya
es declarar la interfaz.

```json
{
  "board": "revision-de-pr",
  "nodos": [
    {"id": "a", "titulo": "Revisa el PR {{numero}} del repo {{repo}}", "runtime": "opencode"},
    {"id": "b", "titulo": "Resumi la revision para {{repo}}", "runtime": "claude-code"}
  ],
  "aristas": [["a", "b"]]
}
```

Genera una tool `revision-de-pr` con `inputSchema` de dos strings requeridos,
`numero` y `repo`.

## Conectarlo

```json
{"mcpServers": {"revision-de-pr": {
    "command": "python",
    "args": ["<ruta-al-repo>/mcp_exporter/mcp_server.py",
             "<ruta-al-repo>/ui/grafos/revision-de-pr.json"]}}}
```

## Qué devuelve

Un JSON con el `board` que se creó, si todo cerró bien, el resultado de las
**hojas** del grafo (los nodos de los que nadie depende, que son la salida del
flujo) y el estado de cada nodo.

Cada invocación usa un board propio con sufijo de tiempo: dos llamadas
concurrentes a la misma tool no se pisan las cards.

## Detalles de protocolo que importan

- JSON-RPC 2.0 por stdio, delimitado por líneas. Sin SDK: `initialize`,
  `tools/list` y `tools/call` es todo lo que hace falta para publicar tools.
- **Las notificaciones (sin `id`) no llevan respuesta.** Contestarlas desalinea
  el stream y algunos clientes lo tratan como fatal. Hay un check para eso.
- La versión de protocolo se negocia por eco: se devuelve la que pidió el
  cliente.
- Un fallo ejecutando el flujo se responde como error de la tool (`isError`),
  no del transporte: el servidor sigue vivo para la próxima llamada.

## Nodos `runtime: hermes`

Los ejecuta el dispatcher de Hermes, no el nuestro (`ARQUITECTURA.md` §12). Si
el grafo los usa, el exportador necesita el binario: lo busca en el `PATH`, en
`ORQUESTER_HERMES_BIN` y en la ruta de instalación por defecto. Si no lo
encuentra, lo dice en vez de colgarse esperando una card que nadie va a
levantar.

## Check

```bash
cd hermes-agent
uv run --python 3.11 --with jsonschema python ../tests/test_mcp_export.py        # protocolo
uv run --python 3.11 --with jsonschema python ../tests/test_mcp_export.py --e2e  # ejecuta el flujo
```
