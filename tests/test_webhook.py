"""El webhook de fin de corrida (D2b): lo unico que avisa sin el Studio abierto.

`notificarNativo()` en la UI ya sonaba y notificaba con la pestana abierta;
esto es el otro medio, para cuando esta cerrada. Se prueba `_notificar_webhook`
como unidad porque es la pieza que se agrego: un servidor HTTP de prueba local
hace de receptor y se mira que le llegue, sin correr un dispatcher de verdad.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_webhook.py
"""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "dispatcher", "compiler", "ui"):
    sys.path.insert(0, str(RAIZ / sub))

import compile as c
import server as srv

BOARD = f"webhook-{int(time.time() * 1000) % 10_000_000}"
GRAFO = {"board": BOARD, "aristas": [], "nodos": [
    {"id": "a", "titulo": "uno", "runtime": "opencode"},
    {"id": "b", "titulo": "dos", "runtime": "opencode"}]}
c.compilar(GRAFO, board=BOARD)

# --- 1. Sin ORQUESTER_WEBHOOK_URL, no manda nada ---------------------------
recibidos = []


class _Receptor(BaseHTTPRequestHandler):
    def do_POST(self):
        largo = int(self.headers.get("Content-Length", 0))
        recibidos.append(json.loads(self.rfile.read(largo)))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass  # silencio: el test ya imprime lo que importa


servidor = HTTPServer(("127.0.0.1", 0), _Receptor)
puerto = servidor.server_address[1]
hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
hilo.start()

try:
    srv.WEBHOOK_URL = None
    srv._notificar_webhook(BOARD)
    assert not recibidos, "sin URL configurada no deberia mandar nada"
    print("1. sin ORQUESTER_WEBHOOK_URL, no manda nada: OK")

    # --- 2. Con URL configurada, manda board/total/conteo ------------------
    srv.WEBHOOK_URL = f"http://127.0.0.1:{puerto}/hook"
    srv._notificar_webhook(BOARD)
    time.sleep(0.2)  # el POST es sincronico pero el hilo del servidor procesa aparte
    assert len(recibidos) == 1, f"esperaba 1 POST, llegaron {len(recibidos)}"
    payload = recibidos[0]
    assert payload["board"] == BOARD
    assert payload["total"] == 2
    assert isinstance(payload["conteo_por_estado"], dict)
    assert sum(payload["conteo_por_estado"].values()) == 2
    assert "resumen" not in json.dumps(payload), \
        "el webhook no debe llevar el contenido de las cards, solo conteos"
    print(f"2. con URL configurada, manda board/total/conteo: OK ({payload})")

    # --- 3. Un webhook inalcanzable no puede tumbar la corrida --------------
    servidor.shutdown()  # el puerto ya no responde
    srv.WEBHOOK_URL = f"http://127.0.0.1:{puerto}/hook"
    srv._notificar_webhook(BOARD)  # no debe lanzar
    print("3. un webhook caido no lanza excepcion: OK")

    print("\nOK: el webhook de fin de corrida avisa, calla si no esta "
          "configurado, y no rompe nada si falla.")
finally:
    srv.WEBHOOK_URL = None
    try:
        servidor.shutdown()
    except Exception:
        pass
