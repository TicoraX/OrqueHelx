"""El visor de diff por nodo (F1/F2 de docs/PLAN-2026-08-31-visor-diff-en-vivo.md):
qué tocó el workspace de una card ya ejecutada, no solo qué dijo que hizo.

Los tres casos de la Premisa 2 (verificados contra kanban_db/loop.py/backends.py
en la revision de ingenieria): un nodo externo sin `workspace` no tiene NINGUNA
carpeta propia, una carpeta pudo limpiarse mientras tanto, y puede no ser git.

    uv run --python 3.11 --with jsonschema --with pyyaml python tests/test_visor_diff.py
"""
import shutil, subprocess, sys, tempfile, time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for sub in ("hermes-agent", "compiler", "dispatcher", "ui"):
    sys.path.insert(0, str(RAIZ / sub))

import compile as c
import hermes_cli.kanban_db as k
import server as srv

BOARD = f"diff-{int(time.time() * 1000) % 10_000_000}"


def _tarea(workspace=None):
    """Compila un nodo suelto y devuelve su task_id."""
    nid = f"n{len(_tarea._usados)}"
    _tarea._usados.add(nid)
    nodo = {"id": nid, "titulo": f"nodo {nid}", "runtime": "opencode"}
    if workspace:
        nodo["workspace"] = workspace
    g = {"board": BOARD, "aristas": [], "nodos": [nodo]}
    ids = c.compilar(g, board=BOARD)
    return ids[nid]
_tarea._usados = set()


# --- 1. Sin workspace declarado: ni carpeta propia ---------------------------
t1 = _tarea()
d1 = srv._diff_nodo(BOARD, t1)
assert d1 == {"ok": True, "caso": "sin_workspace", "stat": "", "diff": "", "truncado": False}, d1
print("1. sin workspace declarado: caso=sin_workspace, OK")

# --- 2. Workspace que se limpio (existio, ya no) -----------------------------
fantasma = Path(tempfile.mkdtemp()) / "ya-no-existe"
t2 = _tarea(str(fantasma))
d2 = srv._diff_nodo(BOARD, t2)
assert d2["caso"] == "workspace_perdido", d2
print("2. workspace que ya no existe en disco: caso=workspace_perdido, OK")

# --- 3. Carpeta real, pero no es un repo git ----------------------------------
plana = Path(tempfile.mkdtemp())
(plana / "a.txt").write_text("nada de git aca\n", encoding="utf-8")
t3 = _tarea(str(plana))
d3 = srv._diff_nodo(BOARD, t3)
assert d3["caso"] == "no_es_git", d3
print("3. carpeta sin git: caso=no_es_git, OK")
shutil.rmtree(plana, ignore_errors=True)

if not shutil.which("git"):
    print("4-6. salteados: no hay `git` en el PATH")
else:
    def _repo_git(nombre):
        p = Path(tempfile.mkdtemp()) / nombre
        p.mkdir()
        def _g(*args):
            return subprocess.run(["git", *args], cwd=str(p), capture_output=True,
                                  text=True, timeout=15)
        _g("init", "-q", ".")
        _g("config", "user.email", "prueba@local")
        _g("config", "user.name", "prueba")
        return p, _g

    # --- 4. Repo real con un cambio: aparece en stat y en diff ---------------
    repo4, g4 = _repo_git("con-cambios")
    (repo4 / "a.txt").write_text("linea original\n", encoding="utf-8")
    g4("add", "-A")
    g4("commit", "-qm", "base")
    (repo4 / "a.txt").write_text("linea modificada\n", encoding="utf-8")
    t4 = _tarea(str(repo4))
    d4 = srv._diff_nodo(BOARD, t4)
    assert d4["caso"] == "ok" and "a.txt" in d4["stat"], d4
    assert "linea modificada" in d4["diff"], d4
    assert d4["truncado"] is False
    print("4. repo con cambios reales: stat y diff no vacios, OK")
    shutil.rmtree(repo4.parent, ignore_errors=True)

    # --- 5. Repo hostil con textconv: --no-textconv lo tiene que frenar ------
    # Mismo molde que test_modo_app.py usa para `filter.*.clean`, aplicado a
    # `diff.<driver>.textconv` -- el hallazgo de seguridad de la revision de
    # ingenieria: `_sin_filtros` no cubre textconv, `git diff` SI lo invoca.
    repo5, g5 = _repo_git("hostil-textconv")
    testigo = repo5 / "EJECUTADO"
    (repo5 / "a.txt").write_text("contenido\n", encoding="utf-8")
    (repo5 / ".gitattributes").write_text("a.txt diff=malo\n", encoding="utf-8")
    g5("add", "-A")
    g5("commit", "-qm", "base")
    g5("config", "diff.malo.textconv",
       f'sh -c \'touch "{testigo.as_posix()}"; cat "$1"\' --')
    (repo5 / "a.txt").write_text("contenidX\n", encoding="utf-8")

    # Sanity: SIN --no-textconv, el driver hostil se dispara de verdad --
    # si esto no pasa, el test no prueba nada.
    testigo.unlink(missing_ok=True)
    subprocess.run(["git", "diff"], cwd=str(repo5), capture_output=True, timeout=15)
    assert testigo.exists(), \
        "el repo hostil no dispara textconv ni con `git diff` crudo: el test no prueba nada"

    testigo.unlink(missing_ok=True)
    t5 = _tarea(str(repo5))
    d5 = srv._diff_nodo(BOARD, t5)
    assert not testigo.exists(), \
        "_diff_nodo ejecuto el textconv de una carpeta ajena (--no-textconv no alcanzo)"
    assert d5["caso"] == "ok" and "a.txt" in d5["stat"], \
        f"se blindo pero dejo de detectar el cambio: {d5}"
    print("5. repo hostil con textconv: --no-textconv lo frena, y sigue "
          "detectando el cambio: OK")
    shutil.rmtree(repo5.parent, ignore_errors=True)

    # --- 6. Diff enorme: se capa, no se manda crudo --------------------------
    repo6, g6 = _repo_git("diff-gigante")
    (repo6 / "grande.txt").write_text("linea\n" * 50, encoding="utf-8")
    g6("add", "-A")
    g6("commit", "-qm", "base")
    (repo6 / "grande.txt").write_text(("x" * 200 + "\n") * 500, encoding="utf-8")
    t6 = _tarea(str(repo6))
    d6 = srv._diff_nodo(BOARD, t6)
    assert d6["caso"] == "ok"
    assert len(d6["stat"]) <= srv._CAP_DIFF and len(d6["diff"]) <= srv._CAP_DIFF, d6
    print(f"6. diff gigante se capa a {srv._CAP_DIFF} chars, truncado={d6['truncado']}: OK")
    shutil.rmtree(repo6.parent, ignore_errors=True)

print("\nOK: el visor de diff resuelve los tres casos y no ejecuta lo que "
      "el workspace ajeno declare.")
