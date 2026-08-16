#!/bin/sh
# Suite en Linux, dentro de un contenedor. Desde la raiz del repo:
#
#     sh tests/linux.sh
#
# En Windows hace falta MSYS_NO_PATHCONV=1 para que Git Bash no traduzca las
# rutas del -v y el -w a rutas de Windows.
#
# Cubre lo que NO necesita los agentes instalados. Los e2e con `claude`,
# `opencode` y `agy` solo corren donde esos CLIs esten autenticados.
set -e
REPO=$(cd "$(dirname "$0")/.." && pwd)

docker run --rm -v "$REPO:/repo" -w /repo/hermes-agent python:3.11-slim sh -c '
pip install -q jsonschema pyyaml
fallos=0
for t in test_contract test_dag_rombo test_dos_dispatchers test_compilador \
         test_concurrencia_reintentos test_mcp_export test_consumo; do
  printf "%-32s" "$t"
  if python ../tests/$t.py > /tmp/o.txt 2>&1; then echo OK; else
    echo FALLA; sed "s/^/    /" /tmp/o.txt | tail -6; fallos=$((fallos+1)); fi
done

# Un CLI de agente como los de Linux: ejecutable plano, sin shim .cmd ni .ps1.
# Es el unico camino de `_resolver_argv` que la logica de Windows no ejercita.
cat > /usr/local/bin/opencode <<SH
#!/bin/sh
echo "{\"type\":\"text\",\"part\":{\"type\":\"text\",\"text\":\"{\\\\\"status\\\\\":\\\\\"success\\\\\",\\\\\"summary\\\\\":\\\\\"ok\\\\\"}\"}}"
echo "{\"type\":\"step_finish\",\"part\":{\"tokens\":{\"total\":10,\"input\":8,\"output\":2,\"cache\":{\"read\":0}},\"cost\":0.001}}"
SH
chmod +x /usr/local/bin/opencode
printf "%-32s" "resolucion de binario"
python - <<PY
import sys; sys.path.insert(0, "/repo/dispatcher")
import backends as b
assert b._resolver_argv(["opencode","run","x"])[0] == "/usr/local/bin/opencode"
r = b.run_backend("opencode", "hola", timeout=60)
assert r["status"] == "success" and r["uso"]["total"] == 10, r
print("OK")
PY
[ $fallos -eq 0 ] || { echo; echo "$fallos test(s) fallaron en Linux"; exit 1; }
echo
echo "OK: la suite corre en Linux."
'
