"""Lo que el Studio suma en esta tanda: abrir un grafo, parametros y workspace.

Los tres eran agujeros reales, no adornos:
  - se podia Guardar y no volver (el endpoint existia, el canvas no lo usaba);
  - un grafo con `{{marcadores}}` solo se podia correr por MCP o por codigo;
  - el `workspace` solo se ponia editando el JSON a mano, y sin el un nodo
    corre en el scratch de Hermes, que se BORRA al completar (SS4.1).

    uv run --python 3.11 --with jsonschema python ..\\tests\\test_ui_expansion.py
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "hermes-agent"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
import hermes_cli.kanban_db as k
import capacidades
import loop as dispatcher

PUERTO = 8798
TOKEN = "token-de-prueba-ui"
NOMBRE = "prueba-ui-expansion"
BOARD = f"ui-exp-{int(time.time()) % 100000}"

# El preflight de `validar` exige el binario instalado, asi que se compila con
# un runtime que EXISTA en esta maquina. En un contenedor sin CLIs no hay
# ninguno y esa parte se saltea diciendolo, en vez de fallar por el entorno.
disponibles = [rt for rt, d in capacidades.tabla().items()
               if d["disponible"] and d["lo_ejecuta"] == "orquester"]
RT = disponibles[0] if disponibles else None

env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")


def pedir(ruta, cuerpo=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PUERTO}{ruta}",
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        headers={"X-Orquester-Token": TOKEN} |
                ({"Content-Type": "application/json"} if cuerpo is not None else {}),
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


GRAFO = {
    "board": NOMBRE,
    "nodos": [
        {"id": "a", "titulo": "Revisá {{repo}} y contá los tests",
         "runtime": RT or "hermes", "workspace": "{{repo}}"},
    ],
    "aristas": [],
}

try:
    for _ in range(50):
        try:
            pedir("/api/grafos")
            break
        except Exception:
            time.sleep(0.2)

    # --- 1. Guardar y volver a abrir ---
    codigo, _ = pedir("/api/grafo", GRAFO)
    assert codigo == 200, codigo
    codigo, datos = pedir("/api/grafos")
    assert NOMBRE in datos["grafos"], datos
    codigo, vuelto = pedir(f"/api/grafo?nombre={NOMBRE}")
    assert codigo == 200 and vuelto["nodos"] == GRAFO["nodos"], vuelto
    print("1. guardar -> listar -> abrir devuelve el MISMO grafo: OK")

    # --- 2. Los parametros salen del servidor, no de una regex del navegador ---
    codigo, datos = pedir("/api/parametros", GRAFO)
    assert datos["parametros"] == ["repo"], datos
    # El marcador esta en `titulo` Y en `workspace`: un parametro, no dos.
    print("2. /api/parametros detecta {{repo}} en titulo y workspace: OK")

    # --- 3. Compilar sin los valores no puede pasar de largo ---
    # Antes de esto no habia forma de mandarlos desde el canvas; si el grafo se
    # compilaba igual, el `{{repo}}` llegaba literal al disco como cwd.
    codigo, datos = pedir("/api/compilar", GRAFO)
    assert codigo == 400 and "repo" in datos["error"], (codigo, datos)
    print("3. compilar sin parametros: 400 nombrando el que falta: OK")

    if RT is None:
        print("4-5. compilacion salteada: no hay CLIs de agente en esta maquina")
    else:
        destino = str(RAIZ)                    # un directorio que existe de verdad
        codigo, datos = pedir("/api/compilar",
                              {**GRAFO, "board": BOARD, "valores": {"repo": destino}})
        assert codigo == 200, (codigo, datos)
        tid = datos["ids"]["a"]
        print(f"4. compilar con valores: OK (board {BOARD})")

        # --- 5. Lo sustituido y el workspace llegan a la card ---
        conn = k.connect(board=BOARD)
        t = k.get_task(conn, tid)
        assert "{{" not in t.title, f"quedo un marcador sin sustituir: {t.title!r}"
        assert destino in t.title, t.title
        assert t.workspace_kind == "dir" and t.workspace_path == destino, \
            f"el workspace no llego a la card: {t.workspace_kind} {t.workspace_path}"
        assert t.assignee == dispatcher.carril(RT), t.assignee
        conn.close()
        print("5. la card quedo con el titulo sustituido y el workspace puesto: OK")

    # --- 6. El grafo guardado conserva sus marcadores ---
    # Se guarda el grafo, no la corrida: si al guardar se sustituyera, el grafo
    # dejaria de ser reutilizable con otros datos.
    codigo, vuelto = pedir(f"/api/grafo?nombre={NOMBRE}")
    assert "{{repo}}" in vuelto["nodos"][0]["titulo"], vuelto
    print("6. el grafo guardado sigue parametrizado: OK")
finally:
    proc.terminate()
    proc.wait(timeout=10)
    (RAIZ / "ui" / "grafos" / f"{NOMBRE}.json").unlink(missing_ok=True)

print("\nOK: abrir un grafo, pasarle parametros y elegir el workspace.")
