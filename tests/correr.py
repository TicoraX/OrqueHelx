"""Correr la suite entera en esta maquina, en un comando.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/correr.py

Existe porque la unica forma documentada era `tests/linux.sh`, que necesita
Docker y solo cubre siete tests. Correrlos a mano con el Python del PATH da
CUATRO fallas fantasma que no son del codigo, sino de que faltan `jsonschema` y
`pyyaml`: un rato perdido buscando un bug que no existe. Esto avisa eso primero
y con el comando exacto, en vez de dejar que se descubra test por test.

Sale distinto de 0 si algo falla, asi que sirve de puerta en cualquier script.
"""
import subprocess, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
# `--with` de uv, no `pip install`: el docstring de cada modulo ya declara estas
# dos como dependencias duras y asi no se toca el Python del sistema.
COMANDO = ("uv run --python 3.11 --with jsonschema --with pyyaml "
           "python tests/correr.py")
# El mas lento (`test_ui_navegador`) tarda ~40s con el navegador incluido; 600
# es techo, no presupuesto.
TIMEOUT_S = 600


def _faltan() -> list[str]:
    ausentes = []
    for mod in ("jsonschema", "yaml"):
        try:
            __import__(mod)
        except ImportError:
            ausentes.append({"yaml": "pyyaml"}.get(mod, mod))
    return ausentes


def main() -> int:
    ausentes = _faltan()
    if ausentes:
        print(f"Faltan dependencias duras: {', '.join(ausentes)}.")
        print(f"No sigo: sin ellas la suite reporta fallas que no son del codigo.")
        print(f"\n    {COMANDO}\n")
        return 2

    tests = sorted(AQUI.glob("test_*.py"))
    if not tests:
        # Verde sobre cero tests es la peor de las respuestas posibles: dice
        # que todo anda y no se probo nada.
        print(f"No encontre ningun test_*.py en {AQUI}.")
        return 2
    fallaron, omitidos = [], []
    for t in tests:
        print(f"{t.stem:<34}", end="", flush=True)
        t0 = time.time()
        try:
            # Sin timeout, un test que se cuelga (un Studio que no baja, un
            # agente esperando stdin) deja la suite entera colgada sin resumen
            # y sin senal. Con el, el que se cuelga cuenta como FALLA y los
            # demas siguen corriendo.
            # ponytail: `subprocess.run` mata al hijo, no a sus nietos. Si algun
            # dia un test deja procesos sueltos, esto pasa a un grupo de
            # procesos (`start_new_session` / CREATE_NEW_PROCESS_GROUP).
            p = subprocess.run([sys.executable, str(t)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               cwd=str(AQUI.parent), timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            seg = time.time() - t0
            print(f"FALLA {seg:4.1f}s  (colgado, lo mate a los {TIMEOUT_S}s)")
            cola = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            fallaron.append((t.stem, [f"timeout de {TIMEOUT_S}s"] + cola.strip().splitlines()[-5:]))
            continue
        seg = time.time() - t0
        salida = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            # Un test OMITIDO sale con codigo 0, igual que uno que paso: mirar
            # solo el exit code cuenta como verde algo que no corrio. Es el caso
            # de `test_api_rbac` sin Postgres, y se descubrio dando un 17/17
            # sobre una API que nadie habia probado.
            if "OMITIDO" in salida:
                motivo = next((l.strip() for l in salida.splitlines()
                               if l.strip().startswith("OMITIDO")), "OMITIDO")
                print(f"OMIT {seg:5.1f}s  {motivo}")
                omitidos.append(t.stem)
            else:
                print(f"OK   {seg:5.1f}s")
            continue
        print(f"FALLA {seg:4.1f}s")
        fallaron.append((t.stem, salida.strip().splitlines()[-6:]))

    corridos = len(tests) - len(omitidos) - len(fallaron)
    print()
    for nombre, cola in fallaron:
        print(f"--- {nombre} ---")
        for linea in cola:
            # La cola viene de un `errors="replace"`, asi que puede traer U+FFFD,
            # y una consola cp1252 no sabe escribirlo: el resumen de fallas moria
            # con un UnicodeEncodeError justo cuando uno lo necesitaba.
            print("    " + linea.encode(sys.stdout.encoding or "utf-8",
                                        "replace").decode(sys.stdout.encoding or "utf-8",
                                                          "replace"))
        print()
    if fallaron:
        print(f"{len(fallaron)} de {len(tests)} fallaron.")
        return 1
    if omitidos:
        print(f"{corridos}/{len(tests)} OK, {len(omitidos)} OMITIDO(S): "
              f"{', '.join(omitidos)}.")
        print("No es verde entero: eso no se probo.")
        return 0
    print(f"OK: {len(tests)}/{len(tests)}, ninguno omitido.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
