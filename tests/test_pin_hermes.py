"""El pin de Hermes y la coherencia entre sus DOS copias.

Hermes es upstream ajeno y avanza solo. Hay dos copias en juego:

  hermes-agent/          -> la que importa nuestro codigo Python
  %LOCALAPPDATA%/hermes/ -> la que corre el CLI y su dispatcher

Las dos escriben el MISMO kanban.db. Si divergen en el esquema, dos escritores
con distinta idea de la tabla tocan la misma base. Este check no lo impide;
avisa, que es lo que se puede hacer con codigo que no es nuestro.

    uv run --python 3.11 python ..\tests\test_pin_hermes.py
"""
import os, re, subprocess, sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
pin = dict(l.split("=", 1) for l in
           (RAIZ / "HERMES_PIN").read_text(encoding="utf-8").splitlines()
           if "=" in l and not l.startswith("#"))

actual = subprocess.run(["git", "-C", str(RAIZ / "hermes-agent"), "rev-parse", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
print(f"pin     : {pin['commit'][:12]} ({pin['fecha']})")
print(f"clon    : {actual[:12]}")
if actual != pin["commit"]:
    print("\n  AVISO: el clon se movio del commit verificado.")
    print("  Las afirmaciones de ARQUITECTURA.md §10 se comprobaron contra el pin.")
    print("  Revalidar la suite y actualizar HERMES_PIN, o volver al pin:")
    print(f"    git -C hermes-agent checkout {pin['commit'][:12]}")

# La copia instalada: la que corre el CLI y el dispatcher de Hermes.
exe = Path(os.environ["LOCALAPPDATA"]) / "hermes/hermes-agent/venv/Scripts/hermes.exe"
if exe.is_file():
    salida = subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout
    m = re.search(r"(v[\d.]+)", salida)
    cli = m.group(1) if m else "?"
    print(f"CLI     : {cli}")
    if cli != pin["cli"]:
        print(f"\n  AVISO: el CLI instalado ({cli}) no es el del pin ({pin['cli']}).")
        print("  Las dos copias escriben el mismo kanban.db.")
else:
    print("CLI     : no instalado (solo hace falta para nodos `runtime: hermes`)")

# Lo que de verdad importa que coincida: el esquema de la base compartida.
sys.path.insert(0, str(RAIZ / "hermes-agent"))
import hermes_cli.kanban_db as k
usadas = ["claim_task", "complete_task", "block_task", "unblock_task",
          "build_worker_context", "heartbeat_claim", "list_runs",
          "list_events", "list_tasks", "create_task", "dispatch_once"]
faltan = [f for f in usadas if not hasattr(k, f)]
assert not faltan, f"el clon ya no expone lo que usamos: {faltan}"
print(f"API     : las {len(usadas)} funciones que usamos siguen existiendo")

# Nunca tocar el codigo de Hermes: la arquitectura dice "pinear, sin fork".
sucio = subprocess.run(["git", "-C", str(RAIZ / "hermes-agent"), "status", "--short"],
                       capture_output=True, text=True).stdout.strip()
assert not sucio, f"hermes-agent tiene cambios locales — se rompio el 'sin fork':\n{sucio}"
print("fork    : ninguno, el clon esta intacto")

# --- Portabilidad: ninguna ruta absoluta en el codigo ---
# El repo tiene que correr desde cualquier ruta. Verificado copiandolo a otro
# directorio y corriendo la suite ahi; este check evita que vuelva a colarse.
absoluta = re.compile(r"""["'][A-Za-z]:[/\\]""")
sucios = []
for py in sorted(RAIZ.rglob("*.py")):
    if "hermes-agent" in py.parts:
        continue                                  # upstream ajeno, no es nuestro
    for n, linea in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
        if absoluta.search(linea) and "no/existe" not in linea:
            sucios.append(f"{py.relative_to(RAIZ)}:{n}: {linea.strip()[:70]}")
assert not sucios, "rutas absolutas en el codigo:\n  " + "\n  ".join(sucios)
print("rutas   : ninguna absoluta en el codigo del repo")

print("\nOK: pin verificado.")
