"""Studio de ORQUESTER: servidor local, sin dependencias.

`http.server` de la stdlib y un HTML de un archivo. Sin npm, sin build, sin
framework: la UI es un canvas SVG y unas pocas llamadas fetch. Cuando el
producto necesite RBAC, multiusuario y persistencia en Postgres, esto se
reemplaza por el backend NestJS de SS7 — hasta entonces es andamiaje que nadie
pidio.

    uv run --python 3.11 --with jsonschema python ui/server.py
    -> http://127.0.0.1:8765
"""
import hmac, json, os, secrets, subprocess, sys, threading, time, traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler", "mcp_exporter"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher
import exportar as mcp
import capacidades

HTML = Path(__file__).parent / "index.html"
GRAFOS = RAIZ / "ui" / "grafos"
GRAFOS.mkdir(exist_ok=True)

# board -> hilo del dispatcher. Un solo dispatcher por board a la vez: dos
# reclamarian las mismas cards y, aunque `claim_task` lo resuelve sin corromper
# nada, es trabajo duplicado sin motivo.
_corriendo: dict[str, threading.Thread] = {}

# Token de acceso. Esto ejecuta agentes con shell: sin autenticacion, exponer el
# puerto es entregar una terminal. Se genera uno por arranque salvo que se fije
# `ORQUESTER_TOKEN` (util para dejarlo estable entre reinicios).
TOKEN = os.environ.get("ORQUESTER_TOKEN") or secrets.token_urlsafe(24)


def _token_ok(handler) -> bool:
    """Comparacion en tiempo constante: un `==` filtra el token por timing."""
    dado = handler.headers.get("X-Orquester-Token") or ""
    if not dado:
        _, _, query = handler.path.partition("?")
        for par in query.split("&"):
            if par.startswith("token="):
                dado = par[6:]
                break
    return hmac.compare_digest(dado, TOKEN)


def _estado(board: str) -> dict:
    """Estado de todas las cards del board, para pintar el canvas."""
    try:
        conn = k.connect(board=board)
    except Exception as e:
        return {"error": str(e), "tareas": {}}
    tareas = {}
    for t in k.list_tasks(conn):
        tareas[t.id] = {
            "titulo": t.title,
            "estado": t.status,
            "assignee": t.assignee,
            "resumen": (getattr(t, "result", None) or "")[:400],
        }
    return {"tareas": tareas, "corriendo": board in _corriendo}


def _traza(board: str, task_id: str) -> dict:
    """Traza de un nodo: sus intentos y sus eventos.

    Es la "observabilidad como grafo" de IDEAS §1: en vez de traducir un log a
    un grafo mental, se lee la traza del nodo en el mismo dibujo donde se
    diseñó el flujo.
    """
    conn = k.connect(board=board)
    t = k.get_task(conn, task_id)
    if t is None:
        return {"error": f"no existe la card {task_id}"}
    eventos = [
        {"kind": e.kind, "cuando": e.created_at, "run": e.run_id,
         # El payload trae PID, motivo del reclaim, error... util y a veces
         # enorme: se recorta acá y no en el navegador.
         "detalle": json.dumps(e.payload, ensure_ascii=False)[:220] if e.payload else ""}
        for e in k.list_events(conn, task_id)
    ]
    intentos = [
        {"n": i + 1, "outcome": r.outcome, "resumen": (r.summary or "")[:300],
         "error": (getattr(r, "error", None) or "")[:300],
         "inicio": r.started_at, "fin": r.ended_at}
        for i, r in enumerate(k.list_runs(conn, task_id))
    ]
    return {
        "titulo": t.title, "estado": t.status, "assignee": t.assignee,
        "block_kind": t.block_kind, "resultado": (t.result or "")[:600],
        "intentos": intentos, "eventos": eventos,
    }


def _consumo(board: str) -> dict:
    """Consumo del board: por nodo y agregado.

    Se suma sobre los **runs**, no sobre las tasks: un nodo reintentado gasto en
    cada intento, y el total del flujo tiene que reflejarlo.
    """
    try:
        conn = k.connect(board=board)
    except Exception as e:
        return {"error": str(e)}
    total = {"entrada": 0, "salida": 0, "total": 0, "cache_lectura": 0,
             "costo_usd": 0.0, "intentos": 0, "con_costo": 0, "sin_costo": 0}
    por_nodo = {}
    for t in k.list_tasks(conn):
        acum = None
        for r in k.list_runs(conn, t.id):
            u = (r.metadata or {}).get("uso")
            if not u:
                continue
            acum = acum or {"entrada": 0, "salida": 0, "total": 0,
                            "cache_lectura": 0, "costo_usd": None,
                            "runtime": u.get("runtime"), "intentos": 0}
            for campo in ("entrada", "salida", "total", "cache_lectura"):
                acum[campo] += u.get(campo) or 0
                total[campo] += u.get(campo) or 0
            acum["intentos"] += 1
            total["intentos"] += 1
            if u.get("costo_usd") is not None:
                acum["costo_usd"] = (acum["costo_usd"] or 0) + u["costo_usd"]
                total["costo_usd"] += u["costo_usd"]
                total["con_costo"] += 1
            else:
                # Sin costo NO es cero: es un backend que corre por suscripcion
                # y no informa medidor. Contarlo como 0 mentiria el promedio.
                total["sin_costo"] += 1
        if acum:
            por_nodo[t.id] = acum
    return {"total": total, "por_nodo": por_nodo}


def _quedan_de_hermes(conn) -> bool:
    """Cards que espera el dispatcher de Hermes, no el nuestro."""
    propios = {dispatcher.carril(rt) for rt in dispatcher.BACKENDS}
    return any(t.status in ("todo", "ready", "running")
               for t in k.list_tasks(conn) if t.assignee not in propios)


def _arrancar(board: str) -> dict:
    if board in _corriendo and _corriendo[board].is_alive():
        return {"ok": False, "motivo": "ya hay un dispatcher corriendo en este board"}

    # Los nodos `runtime: hermes` los lanza el dispatcher de Hermes, no el
    # nuestro (§12). El exportador MCP ya lo tickeaba solo; el boton Ejecutar
    # no, y obligaba a abrir otra terminal para un flujo mixto. Misma pieza,
    # mismo comportamiento.
    hermes = mcp._hermes_bin()

    def _tick_hermes():
        if not hermes:
            return
        try:
            subprocess.run([hermes, "kanban", "--board", board, "dispatch"],
                           capture_output=True, timeout=180,
                           stdin=subprocess.DEVNULL)
        except Exception:
            traceback.print_exc()          # que no tumbe el loop del carril propio

    def _correr():
        try:
            conn = k.connect(board=board)
            while True:
                _tick_hermes()
                hechas = dispatcher.tick(conn, board=board)
                sin_trabajo = (not hechas
                               and not dispatcher._queda_trabajo(conn)
                               and not _quedan_de_hermes(conn))
                if sin_trabajo:
                    return
                time.sleep(3)
        except Exception:
            traceback.print_exc()
        finally:
            _corriendo.pop(board, None)

    h = threading.Thread(target=_correr, daemon=True)
    _corriendo[board] = h
    h.start()
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, cuerpo, tipo="application/json"):
        datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _autorizado(self) -> bool:
        if _token_ok(self):
            return True
        # 404 y no 401: un 401 confirma que aca hay algo. Ademas sin
        # `WWW-Authenticate` el navegador no muestra un popup inutil.
        self._responder(404, {"error": "no encontrado"})
        return False

    def do_GET(self):
        ruta, _, query = self.path.partition("?")
        # El HTML es una cascara estatica sin datos: se sirve sin token para que
        # recargar la pagina funcione (una navegacion no puede mandar cabeceras,
        # y el token se limpia de la URL a proposito). Todo `/api/*` si exige
        # token: ahi estan los datos y la ejecucion.
        if ruta not in ("/", "/api/capacidades") and not self._autorizado():
            return
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if ruta == "/":
            return self._responder(200, HTML.read_bytes(), "text/html; charset=utf-8")
        if ruta == "/api/estado":
            return self._responder(200, _estado(params.get("board", "orquester")))
        if ruta == "/api/capacidades":
            # Sin token: no expone nada del usuario, solo que sabe hacer este
            # motor. Un agente lo consulta antes de armar un grafo.
            return self._responder(200, capacidades.tabla())
        if ruta == "/api/consumo":
            return self._responder(200, _consumo(params.get("board", "orquester")))
        if ruta == "/api/traza":
            return self._responder(200, _traza(params.get("board", "orquester"),
                                               params.get("task", "")))
        if ruta == "/api/grafos":
            return self._responder(200, {"grafos": sorted(p.stem for p in GRAFOS.glob("*.json"))})
        if ruta == "/api/grafo":
            f = GRAFOS / f"{params.get('nombre', 'sin-nombre')}.json"
            if not f.exists():
                return self._responder(404, {"error": "no existe"})
            return self._responder(200, json.loads(f.read_text(encoding="utf-8")))
        return self._responder(404, {"error": "ruta desconocida"})

    def do_POST(self):
        if not self._autorizado():
            return
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = json.loads(self.rfile.read(largo) or b"{}")
        try:
            if self.path == "/api/validar":
                compilador.validar(cuerpo)
                return self._responder(200, {"ok": True})
            if self.path == "/api/parametros":
                # Los marcadores los detecta el exportador MCP, no una segunda
                # regex en el navegador: si se duplica, se desincroniza y el
                # Studio compila con un `{{marcador}}` que llega literal al disco.
                return self._responder(200, {"parametros": mcp.parametros(cuerpo)})
            if self.path == "/api/compilar":
                # Un grafo con marcadores no se compila crudo: `sustituir` exige
                # que esten todos y falla con el nombre del que falta.
                grafo = (mcp.sustituir(cuerpo, cuerpo.get("valores") or {})
                         if mcp.parametros(cuerpo) else cuerpo)
                ids = compilador.compilar(grafo, board=cuerpo.get("board"))
                return self._responder(200, {"ok": True, "ids": ids})
            if self.path == "/api/mcp":
                nombre = cuerpo.get("board") or "sin-nombre"
                return self._responder(200, {
                    "tool": nombre,
                    "parametros": mcp.parametros(cuerpo),
                    "config": {"mcpServers": {nombre: {
                        "command": "python",
                        "args": [str(RAIZ / "mcp_exporter" / "mcp_server.py"),
                                 str(GRAFOS / f"{nombre}.json")],
                    }}},
                })
            if self.path == "/api/correr":
                return self._responder(200, _arrancar(cuerpo.get("board", "orquester")))
            if self.path == "/api/grafo":
                nombre = cuerpo.get("board") or "sin-nombre"
                (GRAFOS / f"{nombre}.json").write_text(
                    json.dumps(cuerpo, indent=2, ensure_ascii=False), encoding="utf-8")
                return self._responder(200, {"ok": True})
        except compilador.ErrorDeGrafo as e:
            # El mensaje va tal cual al canvas: por eso los errores del
            # compilador nombran el nodo culpable.
            return self._responder(400, {"ok": False, "error": str(e)})
        except ValueError as e:
            # `sustituir` con parametros faltantes. Es culpa del pedido, no del
            # servidor: 400 y el nombre de lo que falta. (ErrorDeGrafo tambien
            # es ValueError, por eso este `except` va despues.)
            return self._responder(400, {"ok": False, "error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._responder(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return self._responder(404, {"error": "ruta desconocida"})

    def log_message(self, *a):
        pass                                  # sin ruido de acceso en consola


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    # 127.0.0.1 por defecto. `ORQUESTER_HOST=0.0.0.0` lo abre a la red, y el
    # token deja de ser una formalidad: pasa a ser lo unico que separa a
    # cualquiera de una shell en esta maquina.
    host = os.environ.get("ORQUESTER_HOST", "127.0.0.1")
    print(f"Studio en http://{host}:{puerto}/?token={TOKEN}")
    if host != "127.0.0.1":
        print("  AVISO: expuesto a la red. El token es la unica barrera y esto")
        print("  ejecuta agentes con shell. No lo dejes escuchando sin necesidad.")
    if not os.environ.get("ORQUESTER_TOKEN"):
        print("  (token nuevo en cada arranque; fijalo con ORQUESTER_TOKEN)")
    HTTPServer((host, puerto), Handler).serve_forever()
