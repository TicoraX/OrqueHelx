"""Exportador MCP: parametrizacion y protocolo.

Parte A: parametros y sustitucion, en proceso.
Parte B: el servidor real por stdio, hablando JSON-RPC como lo haria Claude
         Desktop. Con --e2e ademas ejecuta el flujo contra opencode.

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_mcp_export.py
    uv run --python 3.11 --with jsonschema python ..\\tests\\test_mcp_export.py --e2e
"""
import json, subprocess, sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "mcp_exporter"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
import exportar as ex

GRAFO = {
    "board": "revision-de-pr",
    "nodos": [
        {"id": "a", "titulo": "Revisa el PR {{numero}} del repo {{repo}}", "runtime": "opencode"},
        {"id": "b", "titulo": "Resumi la revision para {{repo}}", "runtime": "opencode"},
    ],
    "aristas": [["a", "b"]],
}

# --- 1. Parametros: en orden, sin repetir ---
assert ex.parametros(GRAFO) == ["numero", "repo"], ex.parametros(GRAFO)
assert ex.parametros({"nodos": [{"id": "x", "titulo": "sin marcadores"}]}) == []
print("1. parametros detectados en orden y sin repetir: OK")

# --- 2. El schema sale de los marcadores ---
esq = ex.esquema_entrada(GRAFO)
assert set(esq["properties"]) == {"numero", "repo"}
assert set(esq["required"]) == {"numero", "repo"}
assert esq["additionalProperties"] is False
print("2. inputSchema derivado del grafo: OK")

# --- 3. Sustitucion: no muta, y no interpreta el valor como regex ---
sub = ex.sustituir(GRAFO, {"numero": "42", "repo": r"org/re\g<1>po"})
assert sub["nodos"][0]["titulo"] == r"Revisa el PR 42 del repo org/re\g<1>po", sub["nodos"][0]
assert GRAFO["nodos"][0]["titulo"] == "Revisa el PR {{numero}} del repo {{repo}}", "muto el original"
try:
    ex.sustituir(GRAFO, {"numero": "42"})
    raise SystemExit("FALLA: acepto una llamada sin todos los parametros")
except ValueError as e:
    assert "repo" in str(e), e
print("3. sustitucion segura, sin mutar y exigiendo todos los parametros: OK")

# --- 4. El servidor MCP por stdio ---
ruta = Path(tempfile.mkdtemp()) / "revision-de-pr.json"
ruta.write_text(json.dumps(GRAFO), encoding="utf-8")
proc = subprocess.Popen(
    [sys.executable, str(RAIZ / "mcp_exporter" / "mcp_server.py"), str(ruta)],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", bufsize=1)

def pedir(metodo, params=None, id_=[0]):
    id_[0] += 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": id_[0],
                                 "method": metodo, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())

try:
    r = pedir("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    assert r["result"]["protocolVersion"] == "2025-06-18", r
    assert "tools" in r["result"]["capabilities"], r
    print(f"4. initialize -> {r['result']['serverInfo']['name']}: OK")

    # Una notificacion NO lleva respuesta: si el server contesta, el proximo
    # readline devuelve el mensaje equivocado y todo se desalinea.
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    r = pedir("tools/list")
    tools = r["result"]["tools"]
    assert len(tools) == 1 and tools[0]["name"] == "revision-de-pr", tools
    assert set(tools[0]["inputSchema"]["properties"]) == {"numero", "repo"}
    print(f"5. tools/list -> '{tools[0]['name']}' con {len(tools[0]['inputSchema']['properties'])} parametros: OK")
    print("   (la notificacion no rompio la alineacion del stream)")

    r = pedir("tools/call", {"name": "no-existe", "arguments": {}})
    assert "error" in r and r["error"]["code"] == -32602, r
    print("6. una tool desconocida devuelve error y el server sigue vivo: OK")

    if "--e2e" in sys.argv:
        # El grafo de arriba sirve para probar parametros, pero es mal fixture
        # de ejecucion: "revisa el PR 7" es un goal vago y un agente con
        # herramientas se va a explorar el repo por minutos. Aca se prueba el
        # mecanismo, no la latencia de un modelo sin rumbo.
        ruta.write_text(json.dumps({
            "board": "revision-de-pr",
            "nodos": [
                {"id": "a", "titulo": "Responde unicamente con el numero {{numero}}. Nada mas.",
                 "runtime": "opencode"},
                {"id": "b", "titulo": "Tu padre te paso un numero. Responde unicamente ese numero.",
                 "runtime": "opencode"},
            ],
            "aristas": [["a", "b"]],
        }), encoding="utf-8")
        # El server relee el grafo en cada llamada, asi que no hace falta
        # reiniciarlo... salvo que NO lo relea: lo carga una vez al arrancar.
        # Por eso el fixture se escribe antes de este bloque en la practica;
        # aca se levanta un server nuevo para el e2e.
        proc.stdin.close(); proc.wait(timeout=10)
        proc = subprocess.Popen(
            [sys.executable, str(RAIZ / "mcp_exporter" / "mcp_server.py"), str(ruta)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1)
        pedir("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        r = pedir("tools/call", {"name": "revision-de-pr",
                                 "arguments": {"numero": "7"}})
        salida = json.loads(r["result"]["content"][0]["text"])
        print(f"7. tools/call -> ok={salida['ok']} board={salida['board']}")
        for nid, n in salida["nodos"].items():
            print(f"   {nid}: {n['estado']} | {n['resumen'][:50]}")
        assert salida["ok"], salida
        assert salida["resultado"], "el flujo debe devolver el resultado de sus hojas"
finally:
    proc.stdin.close()
    proc.wait(timeout=10)

print("\nOK: el flujo se publica como servidor MCP.")
