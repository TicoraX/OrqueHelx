#!/usr/bin/env python3
"""CLI autónomo de ORQUESTER (v2).

Permite validar, inspeccionar y ejecutar flujos de agentes desde la terminal
o pipelines de CI/CD sin necesidad de levantar el servidor web del Studio.

Uso:
    python cli.py run <grafo.json> [--board <nombre>] [--presupuesto <usd>] [--json]
    python cli.py validar <grafo.json>
    python cli.py plantillas
    python cli.py skills
    python cli.py doctor
"""
import argparse, json, os, sys, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "compiler"))
sys.path.insert(0, str(RAIZ / "dispatcher"))
sys.path.insert(0, str(RAIZ / "hermes-agent"))

import compile as compilador
import loop as dispatcher
import capacidades
import hermes_cli.kanban_db as k


def _cargar_grafo(ruta_archivo: str) -> dict:
    p = Path(ruta_archivo)
    if not p.is_file():
        # Buscar en plantillas/ o ui/grafos/ si no es ruta directa
        p_pl = RAIZ / "plantillas" / (p.name if p.suffix == ".json" else f"{p.name}.json")
        p_gr = RAIZ / "ui" / "grafos" / (p.name if p.suffix == ".json" else f"{p.name}.json")
        if p_pl.is_file():
            p = p_pl
        elif p_gr.is_file():
            p = p_gr
        else:
            raise FileNotFoundError(f"No se encontró el archivo de grafo '{ruta_archivo}'")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Error leyendo JSON de '{p}': {e}") from e


def cmd_validar(args) -> int:
    try:
        g = _cargar_grafo(args.grafo)
        compilador.validar(g, capacidades=not args.ignorar_capacidades)
        n_nodos = len([n for n in g.get("nodos", []) if n.get("tipo") != "nota"])
        print(f"✓ Grafo válido: '{g.get('board', 'sin-nombre')}' ({n_nodos} nodos ejecutables, {len(g.get('aristas', []))} aristas)")
        return 0
    except Exception as e:
        print(f"✗ Error de validación: {e}", file=sys.stderr)
        return 1


def cmd_doctor(args) -> int:
    doc = capacidades.doctor()
    print("=== Diagnóstico de Entorno ORQUESTER ===")
    plat = doc.get("plataforma", {})
    print(f"Sistema  : {plat.get('os')} {plat.get('release')} (Python {plat.get('python')})")
    print(f"SQLite   : v{plat.get('sqlite_version')} (WAL seguro: {'✓' if plat.get('wal_seguro') else '✗'})")
    print(f"Runtimes : {doc.get('runtimes_activos', 0)} activos")
    print("\nDetalle de binarios:")
    for nombre, info in sorted(doc.get("binarios", {}).items()):
        disp = "✓" if info.get("operable") else ("?" if info.get("disponible") else "✗")
        ver = info.get("version") or (info.get("problema") or "no instalado")
        ruta = f" -> {info.get('ruta')}" if info.get("ruta") else ""
        print(f"  [{disp}] {nombre:14}: {ver}{ruta}")
    return 0 if doc.get("ok") else 1


def cmd_plantillas(args) -> int:
    pl_dir = RAIZ / "plantillas"
    if not pl_dir.is_dir():
        print("No se encontró el directorio plantillas/")
        return 1
    plantillas = sorted(pl_dir.glob("*.json"))
    print(f"=== Catálogo de Plantillas ({len(plantillas)}) ===")
    for p in plantillas:
        try:
            g = json.loads(p.read_text(encoding="utf-8"))
            nodos = [n for n in g.get("nodos", []) if n.get("tipo") != "nota"]
            rts = sorted({n.get("runtime", "hermes") for n in nodos})
            desc = g.get("descripcion", "")
            print(f"• {p.stem:30} [{len(nodos)} nodos, runtimes: {', '.join(rts)}]")
            if desc:
                print(f"    {desc}")
        except Exception:
            continue
    return 0


def cmd_skills(args) -> int:
    carpetas = [
        Path.home() / ".gemini" / "config" / "skills",
        RAIZ.parent / "skills",
        Path.home() / ".claude" / "skills",
        Path.home() / ".codex" / "skills",
    ]
    encontradas = {}
    for base in carpetas:
        if not base.is_dir():
            continue
        try:
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                skill_md = d / "SKILL.md"
                if not skill_md.is_file() or d.name in encontradas:
                    continue
                desc = ""
                try:
                    txt = skill_md.read_text(encoding="utf-8", errors="ignore")
                    if txt.startswith("---"):
                        partes = txt.split("---", 2)
                        if len(partes) >= 3:
                            for linea in partes[1].splitlines():
                                if linea.strip().startswith("description:"):
                                    desc = linea.partition(":")[2].strip().strip('"').strip("'")
                                    break
                except Exception:
                    pass
                encontradas[d.name] = desc or "Habilidad local"
        except Exception:
            continue

    print(f"=== Habilidades Locales Descubiertas ({len(encontradas)}) ===")
    for nombre, desc in sorted(encontradas.items()):
        print(f"• {nombre:30} : {desc[:70]}")
    return 0


def cmd_run(args) -> int:
    try:
        g = _cargar_grafo(args.grafo)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    board = args.board or g.get("board") or f"cli-{int(time.time())}"
    g["board"] = board

    if args.workspace:
        ws_abs = str(Path(args.workspace).resolve())
        for n in g.get("nodos", []):
            if not n.get("workspace"):
                n["workspace"] = ws_abs

    try:
        compilador.validar(g, capacidades=not args.ignorar_capacidades)
    except Exception as e:
        print(f"Error de validación: {e}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"==> Compilando flujo en board: '{board}'...")
    ids = compilador.compilar(g, board=board)

    if not args.json:
        print(f"==> {len(ids)} tareas registradas en kanban.db")
        print("==> Ejecutando dispatcher...")

    tope = float(args.presupuesto) if args.presupuesto else None
    conn = k.connect(board=board)

    # Bucle de ejecucion headless
    while True:
        hechas = dispatcher.tick(conn, board=board, tope_usd=tope)
        if not args.json:
            for tid, out in hechas:
                st = "✓" if out.get("status") == "success" else "✗"
                res = (out.get("summary") or "").strip()[:80]
                print(f"  [{st}] {tid} -> {res}")
        if not hechas and not dispatcher._queda_trabajo(conn):
            break
        time.sleep(1.0)

    # Recolectar resultados finales
    tasks = k.list_tasks(conn)
    completadas = [t for t in tasks if t.status == "done"]
    fallidas = [t for t in tasks if t.status in ("blocked", "triage")]
    gasto = dispatcher.gasto_usd(conn)

    reporte = {
        "ok": len(fallidas) == 0,
        "board": board,
        "total_tareas": len(tasks),
        "completadas": len(completadas),
        "fallidas": len(fallidas),
        "costo_usd": gasto,
        "tareas": [
            {
                "id": t.id,
                "titulo": t.title,
                "assignee": t.assignee,
                "status": t.status,
                "summary": t.summary,
                "result": t.result,
            }
            for t in tasks
        ],
    }

    if args.json:
        print(json.dumps(reporte, indent=2, ensure_ascii=False))
    else:
        print("\n================ RESULTADO ================")
        print(f"Estado general : {'✓ ÉXITO' if reporte['ok'] else '✗ CON FALLOS'}")
        print(f"Progreso       : {len(completadas)}/{len(tasks)} completadas ({len(fallidas)} fallidas)")
        print(f"Consumo medido : US$ {gasto:.4f}")
        print("-------------------------------------------")
        for t in tasks:
            simbolo = "✓" if t.status == "done" else ("●" if t.status == "running" else "✗")
            print(f"[{simbolo}] {t.title} ({t.assignee}) -> {t.status}")
            if t.summary:
                print(f"    Resumen: {t.summary[:100]}")
        print("===========================================")

    return 0 if reporte["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ORQUESTER CLI: Diseña, valida y ejecuta flujos de agentes multi-proveedor.",
        prog="orquester"
    )
    sub = parser.add_subparsers(dest="comando", help="Comando a ejecutar")

    # run
    p_run = sub.add_parser("run", help="Compilar y ejecutar un grafo")
    p_run.add_argument("grafo", help="Ruta al archivo .json del grafo o nombre de plantilla")
    p_run.add_argument("--board", "-b", help="Nombre del board kanban (default: del grafo)")
    p_run.add_argument("--presupuesto", "-p", help="Tope de presupuesto en USD")
    p_run.add_argument("--workspace", "-w", help="Directorio workspace para la ejecución")
    p_run.add_argument("--ignorar-capacidades", action="store_true", help="Omitir preflight de binarios")
    p_run.add_argument("--json", action="store_true", help="Salida en formato JSON estructurado")

    # validar
    p_val = sub.add_parser("validar", help="Validar la estructura y capacidades de un grafo")
    p_val.add_argument("grafo", help="Ruta al archivo .json del grafo")
    p_val.add_argument("--ignorar-capacidades", action="store_true", help="Omitir verificación de binarios locales")

    # plantillas
    sub.add_parser("plantillas", help="Listar plantillas prediseñadas disponibles")

    # skills
    sub.add_parser("skills", help="Listar habilidades locales descubiertas")

    # doctor
    sub.add_parser("doctor", help="Verificar binarios y dependencias del sistema")

    args = parser.parse_args()
    if not args.comando:
        parser.print_help()
        return 1

    if args.comando == "validar":
        return cmd_validar(args)
    if args.comando == "doctor":
        return cmd_doctor(args)
    if args.comando == "plantillas":
        return cmd_plantillas(args)
    if args.comando == "skills":
        return cmd_skills(args)
    if args.comando == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
