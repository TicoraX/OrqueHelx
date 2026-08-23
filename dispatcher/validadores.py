"""El critico del ciclo de refinamiento: que se corre DESPUES de un nodo.

Un agente que escribe codigo se equivoca, y hoy nadie lo revisa hasta que el
flujo termino. Esto cierra el lazo: si despues de correr el nodo el validador
encuentra problemas NUEVOS, la card se bloquea como transitoria con esos
problemas adentro, y el reintento --que desde `loop.ejecutar_una` recibe el
mensaje del intento anterior-- arranca sabiendo que romper no era una opcion.

Dos decisiones que valen mas que el codigo:

1. **El comando sale de esta tabla, no del grafo.** Un campo `validador:
   "<comando>"` seria ejecucion directa de shell escrita en un .json, y los
   grafos de este producto los puede haber escrito un modelo
   (`/api/generar-grafo`). Con un nombre de esta tabla, lo peor que puede pedir
   un grafo hostil es "corre pyflakes".

2. **Solo cuentan los hallazgos NUEVOS.** Un repo de verdad ya tiene warnings:
   comparar contra cero bloquearia el primer nodo de cualquier flujo sobre
   codigo ajeno. Se mide antes y despues, y se juzga la diferencia, que es lo
   que el nodo hizo.

    uv run --python 3.11 --with pyflakes python dispatcher/validadores.py
"""
import subprocess, sys

# nombre -> (argv despues del interprete, que mira)
# `sys.executable -m`: el binario del entorno que ya esta corriendo, sin buscar
# nada en el PATH ni pasar por un shell.
VALIDADORES = {
    "pyflakes": (["-m", "pyflakes", "."],
                 "errores de sintaxis y nombres sin definir en el Python del workspace"),
}

TIMEOUT_S = 120


def existe(nombre: str) -> bool:
    return nombre in VALIDADORES


def disponible(nombre: str) -> tuple[bool, str]:
    """¿Se puede correr en ESTA maquina? Devuelve `(si, motivo)`.

    Se pregunta antes de arrancar la corrida y no al validar el primer nodo:
    enterarse de que falta el linter a los diez minutos, con la card ya
    bloqueada, es la peor forma de enterarse.
    """
    if not existe(nombre):
        return False, f"validador desconocido: {nombre!r}. Hay: {sorted(VALIDADORES)}"
    argv, _ = VALIDADORES[nombre]
    try:
        # `--version` no: pyflakes no lo tiene en todas las versiones. Correrlo
        # sobre nada contesta rapido y prueba lo unico que importa, que el
        # modulo este.
        r = subprocess.run([sys.executable, argv[0], argv[1], "--help"],
                           capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL)
        if r.returncode not in (0, 1, 2):
            return False, f"{nombre} contesto {r.returncode}"
        return True, ""
    except FileNotFoundError:
        return False, f"no se encontro el interprete para correr {nombre}"
    except Exception as e:
        return False, f"no se pudo correr {nombre}: {e}"


def revisar(nombre: str, cwd: str) -> set[str]:
    """Los hallazgos del validador en `cwd`, como conjunto de lineas.

    Un conjunto y no una lista: lo que interesa es si aparecio algo que antes
    no estaba, y el orden en que pyflakes recorre archivos no es estable.

    Si el validador no puede correr se devuelve un conjunto vacio, que hace que
    la comparacion no acuse a nadie. Es a proposito: `disponible()` ya se
    pregunto antes de arrancar, y un validador que se cae a mitad de camino no
    puede convertirse en un bloqueo que el agente no tiene como arreglar.
    """
    argv, _ = VALIDADORES[nombre]
    try:
        r = subprocess.run([sys.executable, *argv], cwd=cwd or None,
                           capture_output=True, text=True, timeout=TIMEOUT_S,
                           stdin=subprocess.DEVNULL)
    except Exception:
        return set()
    return {ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()}


def nuevos(antes: set, despues: set) -> list[str]:
    """Lo que aparecio y antes no estaba, ordenado para que el mensaje sea estable."""
    return sorted(despues - antes)


if __name__ == "__main__":
    # Autochequeo: el validador ve lo que el nodo rompio, y NO le cobra al nodo
    # lo que ya estaba roto.
    import tempfile
    from pathlib import Path

    ok, motivo = disponible("pyflakes")
    assert ok, motivo
    assert not existe("rm -rf /"), "la tabla no puede aceptar un comando suelto"

    d = Path(tempfile.mkdtemp())
    (d / "viejo.py").write_text("import os\n", encoding="utf-8")   # ya venia sucio
    antes = revisar("pyflakes", str(d))
    assert antes, f"pyflakes tendria que ver el import sin usar: {antes}"

    (d / "nuevo.py").write_text("def f():\n    return sin_definir\n", encoding="utf-8")
    despues = revisar("pyflakes", str(d))
    hallazgos = nuevos(antes, despues)
    assert any("sin_definir" in h for h in hallazgos), hallazgos
    assert not any("viejo.py" in h for h in hallazgos), (
        f"le cobro al nodo un problema que ya estaba: {hallazgos}")
    print(f"OK: el validador ve lo nuevo ({len(hallazgos)}) y no lo preexistente.")
