"""Servidor MCP que expone un grafo del Studio como una tool.

JSON-RPC 2.0 por stdio, delimitado por lineas. Sin SDK: el subconjunto de MCP
que hace falta para publicar tools son tres metodos.

    python mcp_exporter/mcp_server.py ui/grafos/mi-flujo.json

Para Claude Desktop / Cursor, en su config de MCP:

    {"mcpServers": {"mi-flujo": {
        "command": "python",
        "args": ["<ruta-al-repo>/mcp_exporter/mcp_server.py",
                 "<ruta-al-repo>/ui/grafos/mi-flujo.json"]}}}
"""
import json, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exportar

# Version del protocolo que hablamos si el cliente no pide una. Cuando la pide,
# se le devuelve la suya: MCP negocia por eco, no por maximo comun.
PROTOCOLO = "2025-06-18"


def _nombre_tool(grafo, ruta: Path) -> str:
    crudo = grafo.get("board") or ruta.stem
    # Los nombres de tool son identificadores, no texto libre.
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in crudo)[:64]


def _descripcion(grafo) -> str:
    pasos = " -> ".join(
        f"{n.get('titulo', '')[:48]} [{n.get('runtime', 'hermes')}]"
        for n in (grafo.get("nodos") or [])[:6])
    n = len(grafo.get("nodos") or [])
    return (f"Ejecuta el flujo '{grafo.get('board', 'sin nombre')}' "
            f"({n} nodo{'s' if n != 1 else ''}). Pasos: {pasos}")


def responder(id_, resultado=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg["error" if error else "result"] = error or resultado
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(ruta_grafo: str) -> None:
    ruta = Path(ruta_grafo)
    grafo = json.loads(ruta.read_text(encoding="utf-8"))
    tool = _nombre_tool(grafo, ruta)

    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            msg = json.loads(linea)
        except json.JSONDecodeError:
            continue                         # basura en el pipe: ignorar
        metodo, id_ = msg.get("method"), msg.get("id")

        # Las notificaciones (sin `id`) no llevan respuesta. Contestarlas es un
        # error de protocolo que algunos clientes tratan como fatal.
        if id_ is None:
            continue

        try:
            if metodo == "initialize":
                pedida = (msg.get("params") or {}).get("protocolVersion")
                responder(id_, {
                    "protocolVersion": pedida or PROTOCOLO,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": f"orquester-{tool}", "version": "0.1.0"},
                })
            elif metodo == "tools/list":
                responder(id_, {"tools": [{
                    "name": tool,
                    "description": _descripcion(grafo),
                    "inputSchema": exportar.esquema_entrada(grafo),
                }]})
            elif metodo == "tools/call":
                params = msg.get("params") or {}
                if params.get("name") != tool:
                    responder(id_, error={"code": -32602,
                                          "message": f"tool desconocida: {params.get('name')}"})
                    continue
                salida = exportar.ejecutar(grafo, params.get("arguments") or {})
                responder(id_, {
                    "content": [{"type": "text",
                                 "text": json.dumps(salida, ensure_ascii=False, indent=2)}],
                    "isError": not salida.get("ok", False),
                })
            elif metodo == "ping":
                responder(id_, {})
            else:
                responder(id_, error={"code": -32601, "message": f"metodo no soportado: {metodo}"})
        except Exception as e:
            # Un fallo ejecutando el flujo es un error de la tool, no del
            # transporte: se responde y el servidor sigue vivo.
            print(traceback.format_exc(), file=sys.stderr)
            responder(id_, error={"code": -32603, "message": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: mcp_server.py <ruta-al-grafo.json>")
    main(sys.argv[1])
