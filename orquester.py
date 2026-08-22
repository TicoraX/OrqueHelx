"""ORQUESTER sin Studio: compilar un grafo y correrlo desde la terminal.

    uv run --python 3.11 --with jsonschema python orquester.py run plantillas/revision-de-repo.json
    uv run --python 3.11 --with jsonschema python orquester.py run g.json --board mi-corrida --tope 2

Hasta ahora, correr un DAG pedia levantar el servidor HTTP y apretar un boton:
la logica de la corrida vivia adentro del handler. Es lo que obliga a los tests
de flujo a arrancar un `Popen` del Studio, y lo que hacia que el workflow que
genera `/api/exportar-ci` tuviera que invocar a un servidor web dentro de CI.

Codigo de salida: 0 si todos los nodos terminaron en `done`, 1 si alguno quedo
`blocked`/`triage` o la corrida se corto por tope o timeout. Eso es lo que hace
que sirva en CI.
"""
import argparse, json, sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
for sub in ("hermes-agent", "dispatcher", "compiler"):
    sys.path.insert(0, str(RAIZ / sub))

import hermes_cli.kanban_db as k
import compile as compilador
import loop as dispatcher
import corrida


def run(args) -> int:
    ruta = Path(args.grafo).expanduser().resolve()
    grafo = json.loads(ruta.read_text(encoding="utf-8"))
    # El nombre puede venir del .json, que no siempre lo escribio quien corre
    # el comando. `corrida.slug` aplica la regla del kanban --que rechaza
    # mayusculas, espacios, `/` y `..`-- y da un mensaje en vez de un traceback.
    try:
        board = corrida.slug(args.board or grafo.get("board") or ruta.stem)
    except ValueError as e:
        sys.exit(str(e))

    # `capacidades=True`: si falta el binario de un runtime, es mejor saberlo
    # ahora que ver la card bloquearse a los tres minutos. Es la misma guarda
    # que aplica el Studio antes de compilar.
    compilador.validar(grafo, capacidades=True)
    ids = compilador.compilar(grafo, board=board)
    print(f"==> {len(ids)} nodos compilados en el board '{board}'")

    fin = corrida.correr(board, tope_usd=args.tope, timeout=args.timeout,
                         log=corrida.imprimir)

    conn = k.connect(board=board)
    tareas = {nid: k.get_task(conn, tid) for nid, tid in ids.items()}
    hechas = [n for n, t in tareas.items() if t.status == "done"]
    print(f"\n==> {len(hechas)}/{len(tareas)} nodos en `done` "
          f"({fin['motivo']}, {fin['vueltas']} vueltas)")
    for nid, t in tareas.items():
        if t.status != "done":
            print(f"    {nid}: {t.status}")
    print(f"==> Consumo medido: US$ {dispatcher.gasto_usd(conn):.4f}")
    conn.close()
    return 0 if len(hechas) == len(tareas) and fin["motivo"] in ("sin trabajo", "listo") else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="orquester", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="compilar un grafo .json y ejecutarlo")
    r.add_argument("grafo", help="ruta al .json del grafo")
    r.add_argument("--board", help="board donde compilar (por defecto, el del grafo)")
    r.add_argument("--tope", type=float, default=None,
                   help="tope de gasto en USD; al alcanzarlo no arranca nodos nuevos")
    r.add_argument("--timeout", type=float, default=None,
                   help="segundos de pared para toda la corrida")
    r.set_defaults(func=run)
    args = p.parse_args(argv)
    if getattr(args, "tope", None) is not None and args.tope <= 0:
        p.error("el tope de gasto tiene que ser > 0")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
