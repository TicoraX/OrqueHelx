"""La UI, en un navegador de verdad.

`ESTADO.md` lo tiene como decisión: la suite de Python no toca `ui/index.html`,
los cambios de interfaz se verifican con Playwright. Hasta ahora eso era una
intención sin archivo, así que el canvas se rompía en silencio: un nodo sin
coordenadas pintaba `d="Mundefined,NaN"` y no lo veía nadie.

Este arranca el Studio, corre `ui_navegador.mjs` contra él y lo baja. Las
respuestas del diseñador con IA van mockeadas en el navegador: se prueba el
cableado de la UI, no el modelo, y una suite que llama a un agente de verdad
cuesta plata en cada corrida.

Se OMITE (código 0, diciéndolo) si falta node o playwright: son dependencias
del navegador, no del producto, y `tests/correr.py` distingue OMITIDO de OK.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_ui_navegador.py
"""
import os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

# La salida del guion trae los iconos del timeline. Una consola cp1252 no sabe
# escribirlos y el test moria con UnicodeEncodeError en el `print`, no en un
# assert: la falla parecia del producto y era de la terminal.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent.parent
GUION = Path(__file__).resolve().parent / "ui_navegador.mjs"
TOKEN = "token-de-prueba-navegador"


def _libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if not shutil.which("node"):
    print("OMITIDO: no hay `node` en el PATH.")
    raise SystemExit(0)
if subprocess.run(["node", "-e", "require.resolve('playwright')"],
                  cwd=str(RAIZ), capture_output=True).returncode != 0:
    print("OMITIDO: falta playwright.")
    print("  Instalalo con:  npm install --no-save playwright && npx playwright install chromium")
    raise SystemExit(0)
# El paquete y el NAVEGADOR se instalan aparte. Con el paquete puesto y sin
# `npx playwright install chromium`, `chromium.launch()` explota y esto
# reportaba FALLA: justo lo contrario del criterio de arriba, porque las
# dependencias del navegador no son del producto.
_ver_chromium = ("import('playwright').then(p => process.exit("
                 "require('fs').existsSync(p.chromium.executablePath()) ? 0 : 1))")
if subprocess.run(["node", "-e", _ver_chromium],
                  cwd=str(RAIZ), capture_output=True).returncode != 0:
    print("OMITIDO: falta el binario de Chromium.")
    print("  Instalalo con:  npx playwright install chromium")
    raise SystemExit(0)

PUERTO = _libre()
env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
# A un archivo y no a `PIPE`: nadie lee ese pipe mientras corre el navegador, y
# un buffer lleno cuelga al servidor. Ademas, cuando algo falla, la cola del log
# del Studio es lo primero que uno quiere ver.
log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False,
                                  encoding="utf-8", errors="replace")
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=log, stderr=subprocess.STDOUT)


def _morir(msg):
    log.flush()
    cola = Path(log.name).read_text(encoding="utf-8", errors="replace").strip()
    if cola:
        print("--- ultimas lineas del Studio ---")
        for linea in cola.splitlines()[-15:]:
            print("   ", linea)
    raise SystemExit(msg)


try:
    for _ in range(60):                       # esperar a que escuche
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", PUERTO)) == 0:
                break
        time.sleep(0.3)
    else:
        _morir("FALLA: el Studio no levanto")

    r = subprocess.run(
        ["node", str(GUION), f"http://127.0.0.1:{PUERTO}/?token={TOKEN}"],
        cwd=str(RAIZ), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip()[-1500:])
        _morir(f"FALLA: la verificacion en navegador salio {r.returncode}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    # En Windows el handle del hijo tarda en soltarse despues del terminate, y
    # un `unlink` inmediato tira PermissionError. Se reintenta un rato corto; si
    # no sale, queda un archivo de log en %TEMP% y no le importa a nadie.
    for _ in range(10):
        try:
            Path(log.name).unlink(missing_ok=True)
            break
        except OSError:
            time.sleep(0.2)

print("\nOK: la UI del disenador con IA anda en un navegador de verdad.")
