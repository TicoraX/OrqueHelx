"""El modo App: que el camino que el usuario aprieta llegue hasta el final.

El overhaul folder-first agrego `/api/orquestar-intencion`, que es el UNICO
boton del modo App: una carpeta + una intencion y el sistema compila y arranca.
Venia con dos fallas que ningun test tocaba, las dos en el mismo `if ejecutar:`

  - `compilador.compilar(g, board=..., conexion=..., tope_usd=...)`, y la firma
    real es `compilar(grafo, *, board=None)`;
  - `_correr_dispatcher_board(...)`, una funcion que no existe en el repo.

El test que lo acompanaba pasaba `ejecutar: False`, o sea que rodeaba
exactamente las dos lineas rotas. Con `ejecutar: True` el endpoint devolvia
400 SIEMPRE: el modo App nunca ejecuto nada.

Aca se cubren las dos, sin gastar un centavo en agentes:

  1. `pyflakes` sobre los modulos del motor: ningun nombre puede quedar
     colgado. Es la red para la clase entera de fallas de camino frio, no solo
     para la que ya pasó.
  2. `_orquestar_intencion` con `ejecutar=True` y `compilar`/`_arrancar`
     espiados: se comprueba que se los llama, y con que. Un espia falla igual
     que el original ante una firma equivocada, y no lanza ningun CLI.

    uv run --python 3.11 --with jsonschema --with pyyaml --with pyflakes \\
        python tests/test_modo_app.py
"""
import importlib.util, subprocess, sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "compiler", "dispatcher", "mcp_exporter", "ui"):
    sys.path.insert(0, str(RAIZ / sub))

FUENTE = RAIZ / "ui" / "server.py"
MODULOS = ["ui/server.py", "compiler/compile.py", "compiler/disposicion.py",
           "dispatcher/loop.py", "dispatcher/backends.py",
           "dispatcher/capacidades.py", "mcp_exporter/exportar.py"]

# --- 1. Ningun nombre colgado en los modulos del motor ------------------------
# Con `pyflakes` y no a mano: la version escrita aca daba falsos positivos en
# cada closure, y arreglarlos es escribir un analizador de scopes que ya existe
# hace veinte anos. Verificado por mutacion: con `_correr_dispatcher_board` de
# vuelta, esto lo nombra con archivo y linea.
try:
    import pyflakes                                    # noqa: F401
except ImportError:
    print("OMITIDO: falta pyflakes.")
    print("  uv run --python 3.11 --with jsonschema --with pyyaml --with pyflakes "
          "python tests/test_modo_app.py")
    raise SystemExit(0)

r = subprocess.run([sys.executable, "-m", "pyflakes"] + MODULOS,
                   cwd=str(RAIZ), capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
# Solo `undefined name`: el resto de lo que pyflakes opina (f-strings sin
# placeholder, imports sin usar) es estilo, y este test es sobre codigo que
# revienta al pisarlo.
colgados = [l for l in (r.stdout or "").splitlines() if "undefined name" in l]
assert not colgados, "nombres que no existen:" + "".join("\n  " + l for l in colgados)
print(f"1. ningun nombre colgado en los {len(MODULOS)} modulos del motor: OK")

# --- 2. El camino con `ejecutar=True` llega hasta compilar y arrancar ----------
spec = importlib.util.spec_from_file_location("srv_app", FUENTE)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

llamadas = {}


def _compilar_espia(grafo, *, board=None):
    """Misma firma que `compilador.compilar`: una llamada de mas explota igual."""
    llamadas["compilar"] = {"board": board, "nodos": len(grafo.get("nodos") or [])}
    return {n["id"]: f"t_{n['id']}" for n in grafo.get("nodos") or []}


def _arrancar_espia(board, tope_usd=None):
    llamadas["arrancar"] = {"board": board, "tope": tope_usd}
    return {"ok": True}


srv.compilador.compilar = _compilar_espia
srv._arrancar = _arrancar_espia

r = srv._orquestar_intencion({"workspace": str(RAIZ), "intencion": "seguridad",
                              "ejecutar": True, "tope_usd": "2.5"})
assert r["ok"] and r["plantilla"] == "auditoria-seguridad-cso-strix", r
assert llamadas.get("compilar"), "no llego a compilar: el modo App no ejecuta nada"
assert llamadas["compilar"]["board"] == r["board"], llamadas
assert llamadas.get("arrancar"), "compilo pero no arranco el dispatcher"
assert llamadas["arrancar"] == {"board": r["board"], "tope": 2.5}, llamadas
assert r["ejecutando"] is True, r
print("2. con ejecutar=True se compila el board y se arranca el dispatcher: OK")

# --- 3. Ningun marcador sobrevive, y el workspace llega a cada nodo ------------
for n in r["grafo"]["nodos"]:
    for clave, valor in n.items():
        assert not (isinstance(valor, str) and "{{" in valor), (n["id"], clave, valor)
    if n.get("tipo") != "nota":
        assert n.get("workspace") == str(RAIZ), \
            f"nodo {n['id']} sin el repo del usuario como cwd: {n.get('workspace')!r}"
        assert isinstance(n.get("x"), (int, float)), f"nodo {n['id']} sin coordenadas"
print("3. sin marcadores sueltos, con el repo como cwd y con coordenadas: OK")

# --- 4. Una plantilla que pide algo que el modo App no sabe llenar se dice -----
srv.mcp_original = srv.mcp.parametros
srv.mcp.parametros = lambda g: ["un_parametro_que_nadie_declara"]
try:
    srv._orquestar_intencion({"workspace": str(RAIZ), "intencion": "seguridad",
                              "ejecutar": False})
    raise SystemExit("FALLA: acepto una plantilla con un parametro que no sabe llenar")
except ValueError as e:
    assert "un_parametro_que_nadie_declara" in str(e), e
finally:
    srv.mcp.parametros = srv.mcp_original
print("4. un parametro que el modo App no sabe completar se rechaza nombrandolo: OK")

# --- 5. Un tope invalido no arranca nada --------------------------------------
for malo in ("-1", "0", "abc"):
    try:
        srv._orquestar_intencion({"workspace": str(RAIZ), "intencion": "seguridad",
                                  "ejecutar": False, "tope_usd": malo})
        raise SystemExit(f"FALLA: acepto el tope {malo!r}")
    except ValueError:
        pass
print("5. topes de gasto invalidos rechazados antes de compilar: OK")

# Los grafos que estos casos dejaron en ui/grafos.
for f in (RAIZ / "ui" / "grafos").glob("auditoria-seguridad-cso-strix-*.json"):
    f.unlink(missing_ok=True)

print("\nOK: el boton del modo App llega hasta el final, no solo hasta el 400.")
