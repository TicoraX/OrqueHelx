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
import os, shutil, socket, subprocess, sys, time
from pathlib import Path

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

PUERTO = _libre()
env = {**os.environ, "ORQUESTER_TOKEN": TOKEN, "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, str(RAIZ / "ui" / "server.py"), str(PUERTO)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
try:
    for _ in range(60):                       # esperar a que escuche
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", PUERTO)) == 0:
                break
        time.sleep(0.3)
    else:
        raise SystemExit("FALLA: el Studio no levanto")

    r = subprocess.run(
        ["node", str(GUION), f"http://127.0.0.1:{PUERTO}/?token={TOKEN}"],
        cwd=str(RAIZ), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip()[-1500:])
        raise SystemExit(f"FALLA: la verificacion en navegador salio {r.returncode}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print("\nOK: la UI del disenador con IA anda en un navegador de verdad.")
