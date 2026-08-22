"""Exportar un grafo del Studio como tool de MCP.

El otro diferenciador de `IDEAS.md` §1: el MCP que trae Hermes expone
conversaciones de mensajería, no ejecución de flujos. Quien quiera invocar un
pipeline desde Claude Desktop o Cursor hoy no tiene cómo.

Parametrización: los `{{marcadores}}` que aparezcan en el título o el cuerpo de
un nodo se vuelven los parámetros de la tool. No hay que declararlos aparte —
escribir el goal ya es declarar la interfaz.
"""
import os, re, shutil, subprocess, sys, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher

_MARCADOR = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def parametros(grafo: dict) -> list[str]:
    """Nombres de los marcadores del grafo, en orden de aparición.

    Se recorre **todo** valor de texto del nodo, no una lista de campos. Cuando
    solo se miraban `titulo` y `cuerpo`, un `{{marcador}}` en `workspace` pasaba
    de largo y llegaba literal al disco: el agente arrancaba con un cwd llamado
    `{{ruta}}` y el proceso ni siquiera lanzaba.
    """
    vistos = []
    for n in grafo.get("nodos") or []:
        for valor in n.values():
            if isinstance(valor, str):
                for nombre in _MARCADOR.findall(valor):
                    if nombre not in vistos:
                        vistos.append(nombre)
    return vistos


def esquema_entrada(grafo: dict) -> dict:
    """JSON Schema de la tool, derivado de los marcadores."""
    props = {p: {"type": "string", "description": f"Valor para {{{{{p}}}}}"}
             for p in parametros(grafo)}
    return {"type": "object", "properties": props,
            "required": list(props), "additionalProperties": False}


def sustituir(grafo: dict, valores: dict) -> dict:
    """Grafo nuevo con los marcadores reemplazados. No muta el original."""
    faltan = [p for p in parametros(grafo) if p not in valores]
    if faltan:
        raise ValueError(f"faltan parametros: {faltan}")

    def _sub(txt):
        # Reemplazo por callback: si un valor trae `\g<1>` o similar, `re.sub`
        # con string lo interpretaria como referencia de grupo.
        return _MARCADOR.sub(lambda m: str(valores.get(m.group(1), m.group(0))), txt or "")

    resultado = {**grafo,
            "nodos": [{clave: _sub(valor) if isinstance(valor, str) else valor
                       for clave, valor in n.items()}
                      for n in grafo.get("nodos") or []]}
    # El `workspace` es el `cwd` del agente, y aca es donde un valor de AFUERA
    # entra al grafo: por el servidor MCP lo elige un IDE ajeno, no el autor.
    # Un `..` en el medio saca al agente del arbol que el grafo declaraba.
    #
    # Se saco de esta guarda un `not Path(ws).resolve().is_absolute()` que
    # estaba muerto: `resolve()` SIEMPRE devuelve una ruta absoluta, asi que esa
    # mitad de la condicion era `not True` en los tres casos posibles
    # (verificado con una ruta absoluta, una con '..' y una relativa).
    for n in resultado.get("nodos") or []:
        ws = n.get("workspace") or ""
        if ws and ".." in Path(ws).parts:
            raise ValueError(
                f"workspace invalido tras sustituir: {ws!r} sale del arbol con '..'")
    return resultado


def _hermes_bin() -> str | None:
    """El binario de Hermes, si esta. Solo hace falta con nodos `runtime: hermes`."""
    return (os.environ.get("ORQUESTER_HERMES_BIN")
            or shutil.which("hermes")
            or next((str(p) for p in [
                Path(os.environ.get("LOCALAPPDATA", "")) /
                "hermes/hermes-agent/venv/Scripts/hermes.exe"] if p.is_file()), None))


def ejecutar(grafo: dict, valores: dict, *, timeout: int = 900) -> dict:
    """Compilar el grafo con sus parametros, ejecutarlo y devolver las hojas.

    Cada invocacion usa un board propio: dos llamadas concurrentes a la misma
    tool no pueden pisarse las cards.
    """
    concreto = sustituir(grafo, valores)
    board = f"mcp-{concreto.get('board', 'flujo')}-{int(time.time() * 1000) % 10_000_000}"
    ids = compilador.compilar(concreto, board=board)

    # Los nodos `runtime: hermes` los ejecuta el dispatcher de Hermes, no el
    # nuestro (§12). Si el grafo los usa y el binario no esta, se avisa en vez
    # de colgarse esperando una card que nadie va a levantar.
    usa_hermes = any(n.get("runtime", "hermes") == "hermes" for n in concreto["nodos"])
    hermes = _hermes_bin() if usa_hermes else None
    if usa_hermes and not hermes:
        return {"error": "el grafo tiene nodos `runtime: hermes` y no se encontro el "
                         "binario de Hermes. Configura ORQUESTER_HERMES_BIN.",
                "board": board}

    conn = k.connect(board=board)
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if hermes:
            # `stdin=DEVNULL` por el mismo motivo que en `backends.run_backend`:
            # cuando el padre es el servidor MCP, el stdin heredado es el canal
            # JSON-RPC. Y aca es peor: esta dentro del bucle de polling, asi que
            # se repetiria en cada vuelta.
            subprocess.run([hermes, "kanban", "--board", board, "dispatch"],
                           capture_output=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        dispatcher.tick(conn, board=board)
        tareas = [k.get_task(conn, t) for t in ids.values()]
        if all(t.status in ("done", "blocked", "triage", "failed", "cancelled")
               for t in tareas):
            break
        time.sleep(3)

    tareas = {nid: k.get_task(conn, tid) for nid, tid in ids.items()}
    # Las hojas son la salida del flujo: los nodos de los que nadie depende.
    con_hijos = {p for p, _ in (concreto.get("aristas") or [])}
    hojas = [nid for nid in ids if nid not in con_hijos]
    return {
        "board": board,
        "ok": all(t.status == "done" for t in tareas.values()),
        "resultado": {nid: (tareas[nid].result or "") for nid in hojas},
        "nodos": {nid: {"estado": t.status, "resumen": (t.result or "")[:600]}
                  for nid, t in tareas.items()},
    }
