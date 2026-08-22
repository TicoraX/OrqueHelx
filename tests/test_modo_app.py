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

Y de yapa el otro riesgo del modo folder-first: que ABRIR una carpeta ajena no
sea ejecutarla. Se arma un repo hostil de verdad, con un filtro de contenido
propio, y se comprueba que `git status` crudo si lo dispara y que
`_analizar_workspace` no.

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

# --- 6. Mirar una carpeta no ejecuta lo que esa carpeta diga ------------------
# `git` corre comandos que salen del `.git/config` del repo que se le apunta:
# `core.fsmonitor` en cualquier operacion, y el filtro `clean` que elija el
# `.gitattributes` cuando `git status` decide si un archivo cambio. Este
# endpoint se dispara con el boton "Abrir", ANTES de que nadie autorice correr
# un agente, asi que abrir una carpeta descargada no puede ser ejecucion.
#
# Se arma un repo hostil de verdad y se comprueba las dos mitades: que `git
# status` crudo SI ejecuta el filtro (si no, el test no probaria nada) y que
# pasando por `_analizar_workspace` no, sin perder la deteccion.
import shutil, subprocess, tempfile

if not shutil.which("git"):
    print("6. repo hostil: salteado, no hay `git` en el PATH")
else:
    hostil = Path(tempfile.mkdtemp()) / "hostil"
    hostil.mkdir()
    testigo = hostil / "EJECUTADO"

    def _g(*args, **kw):
        return subprocess.run(["git", *args], cwd=str(hostil), capture_output=True,
                              text=True, timeout=15, **kw)

    _g("init", "-q", ".")
    _g("config", "user.email", "prueba@local")
    _g("config", "user.name", "prueba")
    (hostil / "a.txt").write_text("contenido\n", encoding="utf-8")
    (hostil / ".gitattributes").write_text("* filter=malo\n", encoding="utf-8")
    _g("add", "-A")
    _g("-c", "filter.malo.clean=cat", "commit", "-qm", "base")
    # El filtro deja un archivo testigo al correr. Un `touch` no le hace nada a
    # nadie; lo que importa es que se pueda ver si git lo ejecuto.
    _g("config", "filter.malo.clean",
       f'sh -c "touch {testigo.as_posix()}; cat"')
    # MISMO tamano que el original a proposito: con tamanos distintos git puede
    # concluir que el archivo cambio sin leer el contenido, y entonces no corre
    # el filtro y el test no probaria nada. Igualados, tiene que compararlos.
    (hostil / "a.txt").write_text("contenidX\n", encoding="utf-8")

    testigo.unlink(missing_ok=True)
    _g("status", "--porcelain")
    assert testigo.exists(),         "el repo hostil no dispara el filtro ni con `git status` crudo: el test no prueba nada"

    testigo.unlink(missing_ok=True)
    info = srv._analizar_workspace(str(hostil))
    assert not testigo.exists(),         "_analizar_workspace ejecuto el filtro que definia la carpeta ajena"
    assert info["es_git"] and info["git_cambios_pendientes"] == 1,         f"se blindo pero dejo de detectar: {info}"
    print("6. abrir una carpeta hostil no ejecuta sus filtros de git, y sigue "
          "detectando el cambio: OK")
    shutil.rmtree(hostil.parent, ignore_errors=True)

# --- 7. La UI no puede pintar estados que el kanban no tiene -----------------
# Cuarta aparicion del mismo bug en esta rama: la UI tenia un mapa
# estado -> color con las claves `failed` y `cancelled`, que NO existen en
# `VALID_STATUSES`. El rojo estaba asignado a `failed`, o sea a nada, y
# `blocked` --el estado que si llega-- quedaba con el MISMO gris que `todo`. El
# lienzo no podia mostrar que un nodo se habia trabado, mientras la leyenda de
# al lado prometia rojo para "bloqueada".
import re
import hermes_cli.kanban_db as kdb

html = (RAIZ / "ui" / "index.html").read_text(encoding="utf-8")
m = re.search(r"const VAR_ESTADO = \{(.*?)\};", html, re.S)
assert m, "no se encontro el mapa VAR_ESTADO en ui/index.html"
variables = dict(re.findall(r"(\w+)\s*:\s*\"(--[\w-]+)\"", m.group(1)))
assert variables, "el mapa VAR_ESTADO quedo vacio o cambio de forma"

inventados = set(variables) - set(kdb.VALID_STATUSES)
assert not inventados, f"la UI pinta estados que el kanban no tiene: {sorted(inventados)}"

# Y al reves: un `blocked` con el mismo color que un `todo` es un nodo roto que
# se ve sano. `scheduled` y `archived` pueden compartir con otro; esos dos no.
assert variables.get("blocked") != variables.get("todo"), \
    "un nodo bloqueado se pinta igual que uno en espera"
avance = [variables.get(e) for e in ("ready", "running", "done")]
assert len(set(avance)) == 3, f"estados de avance con colores repetidos: {avance}"

# Todo color que la UI nombre tiene que existir en `:root`, o el nodo sale
# pintado de vacio.
for estado, variable in variables.items():
    assert f"{variable}:" in html, f"'{estado}' usa {variable} y no esta en :root"
print(f"7. los {len(variables)} estados que pinta la UI existen en el kanban, "
      "tienen variable propia y `blocked` se distingue de `todo`: OK")

# --- 8. El selector de carpetas: las tres salidas -----------------------------
# El navegador no puede dar una ruta absoluta (`showDirectoryPicker` da un
# handle, `webkitdirectory` da rutas relativas), asi que el dialogo lo abre el
# servidor. Eso mete un subproceso con GUI en el camino, y las tres salidas
# tienen que ser distinguibles: elegiste algo, cancelaste, o esta maquina no
# puede abrir ventanas. Confundir "cancelaste" con "fallo" es lo que hace que
# una app te grite en rojo por apretar Escape.
#
# Con un `subprocess.run` de mentira: abrir un dialogo de verdad colgaria la
# suite esperando a que un humano haga clic.
import subprocess as _sp

class _Falso:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

_run_real = srv.subprocess.run
try:
    srv.subprocess.run = lambda *a, **k: _Falso(stdout=str(RAIZ) + "\n")
    r_ok = srv._elegir_carpeta()
    assert r_ok["ok"] and r_ok["ruta"] == str(RAIZ), r_ok
    assert r_ok["stack"], "vuelve sin analizar: obliga a un segundo clic"

    srv.subprocess.run = lambda *a, **k: _Falso(stdout="\n")
    r_cancel = srv._elegir_carpeta()
    assert r_cancel["ok"] is False and r_cancel.get("cancelado") is True, r_cancel

    srv.subprocess.run = lambda *a, **k: _Falso(
        returncode=1, stderr="_tkinter.TclError: no display name and no $DISPLAY")
    r_sin = srv._elegir_carpeta()
    assert r_sin["ok"] is False and not r_sin.get("cancelado"), r_sin
    assert "DISPLAY" in r_sin["motivo"], \
        f"el motivo real de Tk se pierde: {r_sin['motivo']}"

    def _cuelga(*a, **k):
        raise _sp.TimeoutExpired(cmd="dialogo", timeout=1)
    srv.subprocess.run = _cuelga
    r_timeout = srv._elegir_carpeta()
    assert r_timeout["ok"] is False and r_timeout.get("cancelado") is True, r_timeout
finally:
    srv.subprocess.run = _run_real
print("8. el selector distingue elegir, cancelar, sin-display y timeout: OK")

# --- 9. Cada tarjeta del modo App llega a su plantilla ------------------------
# El catalogo de tarjetas vive hardcodeado en `index.html` y el mapa que las
# traduce a un archivo vive en `server.py`. Son dos listas separadas que tienen
# que decir lo mismo, y no lo decian: `triage-de-bug`, `documentar-cambios` y
# `segunda-opinion` no estaban en el mapa --el mapa decia `triage` y
# `documentar`, y de la tercera no sabia nada--, asi que esas tres tarjetas
# caian al `elif prompt` y el usuario recibia un diseno generico del agente en
# vez de la plantilla que eligio. Sin error, sin aviso: se veia igual.
#
# Se prueba yendo hasta la plantilla que sale, no comparando las dos listas: lo
# que importa no es que las claves coincidan, es que la tarjeta que uno aprieta
# termine en el flujo que promete.
import re as _re
_html = (RAIZ / "ui" / "index.html").read_text(encoding="utf-8")
_i = _html.index("const PLANTILLAS_CATALOGO")
_tarjetas = _re.findall(r'id:\s*["\']([^"\']+)', _html[_i:_i + 3000])
assert len(_tarjetas) >= 6, f"no se pudo leer el catalogo de tarjetas: {_tarjetas}"

_arrancar_real = srv._arrancar
srv._arrancar = lambda board, tope: {"ok": True, "motivo": ""}
try:
    _sueltas, _dejados = [], []
    for _cid in _tarjetas:
        _r = srv._orquestar_intencion({
            "workspace": str(RAIZ), "intencion": _cid, "prompt": "revisa el repo",
            # `dry_run`: si la tarjeta cae al disenador, que no llame a nadie.
            "ejecutar": False, "dry_run": True})
        if _r.get("plantilla") == "sintesis-ia":
            _sueltas.append(_cid)
        # Cada llamada compila un board y guarda su grafo: nueve por corrida,
        # en el kanban y en la carpeta donde el usuario guarda los suyos.
        _dejados.append(_r.get("board", ""))
finally:
    srv._arrancar = _arrancar_real
    for _b in _dejados:
        if not _b:
            continue
        (RAIZ / "ui" / "grafos" / f"{_b}.json").unlink(missing_ok=True)
        try:
            srv.k.remove_board(_b, archive=False)
        except Exception as _e:
            print(f"   [aviso] quedo el board {_b}: {type(_e).__name__}")

assert not _sueltas, (
    "tarjetas del modo App que no llegan a ninguna plantilla y caen al "
    f"disenador: {_sueltas}\n  (agregalas a `mapa_plantillas` en server.py "
    "con el nombre del archivo en plantillas/)")
print(f"9. las {len(_tarjetas)} tarjetas del modo App llegan a su plantilla: OK")

# Los grafos que estos casos dejaron en ui/grafos.
for f in (RAIZ / "ui" / "grafos").glob("auditoria-seguridad-cso-strix-*.json"):
    f.unlink(missing_ok=True)

print("\nOK: el boton del modo App llega hasta el final, no solo hasta el 400.")
