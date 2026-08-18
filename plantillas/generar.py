"""Genera las plantillas de ORQUESTER. Se corre a mano cuando se editan."""
import json
from pathlib import Path

P = Path("plantillas")
NL = chr(10)


def g(nombre, descripcion, reglas, nodos, aristas):
    (P / (nombre + ".json")).write_text(json.dumps(
        {"board": nombre, "descripcion": descripcion, "reglas": reglas,
         "nodos": nodos, "aristas": aristas}, indent=2, ensure_ascii=False),
        encoding="utf-8")


def nota(i, texto, x, y):
    return {"id": i, "tipo": "nota", "titulo": texto, "x": x, "y": y}


REGLAS_LECTURA = (
    "No modifiques archivos: esto es un diagnostico, no un arreglo." + NL +
    "No instales dependencias ni cambies la configuracion del proyecto." + NL +
    "Si la evidencia no alcanza para afirmar algo, decilo en vez de rellenar." + NL +
    "Responde en espanol rioplatense, directo y sin relleno.")

g("revision-de-repo",
  "Revisa el diff desde cuatro angulos en paralelo (bugs, tests, seguridad, "
  "alcance) y un quinto nodo emite el veredicto de merge.",
  REGLAS_LECTURA,
  [
    nota("nota", "REVISION DE REPO - los cuatro de arriba corren en PARALELO y "
                 "ninguno ve lo que encontro el otro: el veredicto recibe cuatro "
                 "miradas independientes, no una contagiada.", 40, 300),
    {"id": "bugs",
     "titulo": "Revisa el codigo de {{ruta}}: mira `git diff HEAD~{{commits}} --stat` y despues "
               "`git diff HEAD~{{commits}}`. Busca bugs REALES: errores de logica, casos borde "
               "sin cubrir, recursos que no se cierran, excepciones que se tragan. NO comentes "
               "estilo ni nombres. Maximo 5 hallazgos, cada uno con archivo:linea y por que "
               "falla. Si no hay nada serio, decilo.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 40, "y": 60},
    {"id": "tests",
     "titulo": "En {{ruta}} corre la suite de tests con: {{comando_tests}}" + NL +
               "Reporta cuales pasaron y cuales fallaron, con la linea de error de los que "
               "fallen.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 300, "y": 60},
    {"id": "seguridad",
     "titulo": "Mira `git diff HEAD~{{commits}}` en {{ruta}} SOLO desde seguridad: entradas sin "
               "validar, secretos hardcodeados, rutas de archivo armadas con datos de afuera, "
               "SQL o comandos concatenados, permisos que se aflojan. Si el diff no toca nada "
               "sensible, decilo en una linea en vez de inventar hallazgos.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 560, "y": 60},
    {"id": "alcance",
     "titulo": "Compara el diff `git diff HEAD~{{commits}}` de {{ruta}} contra los mensajes de "
               "`git log --oneline -{{commits}}`. El cambio hace lo que dice que hace? Marca lo "
               "que se colo de mas (refactors no pedidos, archivos sin relacion) y lo que falta "
               "para que el cambio este completo (tests, doc).",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 820, "y": 60},
    {"id": "veredicto",
     "titulo": "Tus padres revisaron el mismo cambio por separado: bugs, tests, seguridad y "
               "alcance. Deci si esto se puede mergear. Maximo 6 lineas: veredicto "
               "(MERGEAR / NO MERGEAR / MERGEAR CON REPAROS), el motivo principal, y que "
               "arreglar primero. Si los tests fallaron o hay un hallazgo de seguridad, el "
               "veredicto NO puede ser MERGEAR.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 430, "y": 300},
  ],
  [["bugs", "veredicto"], ["tests", "veredicto"],
   ["seguridad", "veredicto"], ["alcance", "veredicto"]])

g("segunda-opinion",
  "La misma pregunta a tres ejecutores que no se ven entre si, un cuarto que "
  "contrasta y un quinto que verifica lo que quedo en disputa.",
  ("No te apoyes en lo que ya sabes: verifica contra el codigo o los datos a mano." + NL +
   "Si no podes verificar algo, marcalo como no verificado en vez de afirmarlo." + NL +
   "Responde en espanol rioplatense, directo y sin relleno."),
  [
    nota("nota", "SEGUNDA OPINION - el valor no esta en las tres respuestas sino en DONDE "
                 "se contradicen: ahi es donde alguno de los tres esta inventando.", 40, 430),
    {"id": "a",
     "titulo": "{{pregunta}}" + NL + NL + "Responde concreto y breve. Si no estas seguro de "
               "algo, deci explicitamente que no sabes.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 40, "y": 60},
    {"id": "b",
     "titulo": "{{pregunta}}" + NL + NL + "Responde concreto y breve. Si no estas seguro de "
               "algo, deci explicitamente que no sabes.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 320, "y": 60},
    {"id": "c",
     "titulo": "{{pregunta}}" + NL + NL + "Responde concreto y breve. Si no estas seguro de "
               "algo, deci explicitamente que no sabes.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 600, "y": 60},
    {"id": "contraste",
     "titulo": "Tus padres respondieron la MISMA pregunta por separado. Arma: (1) en que "
               "coinciden los tres, (2) en que se contradicen -eso es lo importante-, (3) que "
               "afirmo uno solo y los otros no mencionaron. No elijas ganador todavia.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 200, "y": 280},
    {"id": "verificar",
     "titulo": "Tu padre listo coincidencias y contradicciones. Para cada contradiccion, deci el "
               "comando, archivo o prueba CONCRETA que la resuelve. Si algo se puede verificar "
               "aca mismo, verificalo y deci que salio. Cerra con la respuesta que la evidencia "
               "sostiene, y con lo que quedo sin resolver.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 460, "y": 430},
  ],
  [["a", "contraste"], ["b", "contraste"], ["c", "contraste"],
   ["contraste", "verificar"]])

g("triage-de-bug",
  "Cinco pasos en cadena sobre un bug: reproducir, causa raiz, buscar hermanos "
  "rotos, arreglo minimo y como se verifica. Ningun paso puede saltearse.",
  ("Prohibido proponer el arreglo antes del nodo que lo pide: el orden es el metodo." + NL +
   "No arregles el sintoma. Si no llegas a la causa, deci que falta medir." + NL +
   "No modifiques archivos salvo que el nodo lo pida explicitamente." + NL +
   "Responde en espanol rioplatense, directo y sin relleno."),
  [
    nota("nota", "TRIAGE - la cadena existe para que nadie salte a proponer un parche antes "
                 "de probar la causa. Si 'reproducir' falla, lo que sigue no sirve: para ahi "
                 "y arregla la reproduccion primero.", 480, 60),
    {"id": "reproducir",
     "titulo": "En {{ruta}} hay este bug: {{sintoma}}" + NL + NL + "Tu UNICO trabajo es "
               "reproducirlo. Encontra el comando o el caso minimo que lo dispara y pega la "
               "salida real del error. Si NO lo lograste reproducir, decilo y explica que "
               "probaste: eso tambien es un resultado.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 180, "y": 40},
    {"id": "causa",
     "titulo": "Tu padre reprodujo un bug. Encontra la CAUSA RAIZ leyendo el codigo: que linea, "
               "por que falla y por que el sintoma aparece donde aparece. Prohibido proponer "
               "arreglo. Si la evidencia no alcanza, deci que falta medir.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 180, "y": 180},
    {"id": "hermanos",
     "titulo": "Tu padre encontro la causa raiz. Busca en {{ruta}} TODOS los otros lugares con "
               "el mismo problema: grepea los llamadores de esa funcion y los patrones "
               "parecidos. Un arreglo que solo tapa el caso del ticket deja hermanos rotos. "
               "Lista archivo:linea de cada uno.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 180, "y": 320},
    {"id": "arreglo",
     "titulo": "Con la causa raiz y la lista de lugares afectados, propone el arreglo MAS CHICO "
               "que ataque la causa -no el sintoma- y que cubra a los hermanos de una sola vez. "
               "Mostra el diff propuesto. No lo apliques.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 180, "y": 460},
    {"id": "verificacion",
     "titulo": "Tu padre propuso un arreglo. Escribi COMO se comprueba que quedo arreglado: el "
               "check mas chico que falla si el bug vuelve. Comando concreto o test minimo. Si "
               "el arreglo no es verificable, decilo: eso es un problema del arreglo.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 180, "y": 600},
  ],
  [["reproducir", "causa"], ["causa", "hermanos"], ["hermanos", "arreglo"],
   ["arreglo", "verificacion"]])

g("documentar-cambios",
  "Del diff salen en paralelo el changelog, la doc que quedo vieja y el texto "
  "del PR; un ultimo nodo revisa que los tres digan lo mismo.",
  ("No inventes cambios que el diff no muestra." + NL +
   "Voz activa, sin relleno, sin emojis y sin frases de cierre tipo eslogan." + NL +
   "Responde en espanol rioplatense."),
  [
    nota("nota", "DOCUMENTAR - los tres hijos parten del MISMO resumen: si se contradicen "
                 "entre si, el problema esta en el resumen y no en ellos.", 40, 430),
    {"id": "leer",
     "titulo": "En {{ruta}} lee `git log --oneline -{{commits}}` y `git diff HEAD~{{commits}}`. "
               "Resumi QUE cambio y POR QUE, en terminos de comportamiento observable para "
               "quien usa esto. Nada de listar archivos tocados: eso ya esta en el diff.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 340, "y": 50},
    {"id": "changelog",
     "titulo": "Con el resumen de tu padre, escribi la entrada de CHANGELOG: que se agrego, que "
               "se arreglo y que rompe compatibilidad. Si el cambio no amerita entrada, decilo.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 40, "y": 230},
    {"id": "docs",
     "titulo": "Con el resumen de tu padre, deci que documentacion de {{ruta}} quedo "
               "DESACTUALIZADA: archivo, seccion y que corregir. Si no quedo nada viejo, "
               "decilo en una linea.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 340, "y": 230},
    {"id": "pr",
     "titulo": "Con el resumen de tu padre, escribi el titulo y el cuerpo del pull request: que "
               "problema resuelve, como, y como lo verifico quien lo hizo. Maximo 12 lineas.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 640, "y": 230},
    {"id": "coherencia",
     "titulo": "Tus padres escribieron changelog, notas de doc y texto de PR sobre el MISMO "
               "cambio. Revisa que no se contradigan y que ninguno prometa algo que el diff no "
               "hace. Devolve los tres textos ya corregidos, listos para pegar.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 340, "y": 430},
  ],
  [["leer", "changelog"], ["leer", "docs"], ["leer", "pr"],
   ["changelog", "coherencia"], ["docs", "coherencia"], ["pr", "coherencia"]])

g("explicar-un-repo",
  "Para caer parado en un repo ajeno: arquitectura, puntos de entrada, "
  "dependencias y cobertura en paralelo, y una sintesis con los riesgos.",
  ("Basate en el codigo, no en el README: si el README miente, decilo." + NL +
   "No modifiques nada." + NL +
   "Responde en espanol rioplatense, directo y sin relleno."),
  [
    nota("nota", "EXPLICAR UN REPO - pensado para el primer dia en un proyecto ajeno. "
                 "Cambia {{ruta}} y corre: no necesita mas parametros.", 40, 430),
    {"id": "mapa",
     "titulo": "Explora {{ruta}} y explica su ARQUITECTURA: que hace el proyecto, cuales son "
               "los modulos principales y como se relacionan. Maximo 15 lineas.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 40, "y": 60},
    {"id": "entradas",
     "titulo": "En {{ruta}} encontra los PUNTOS DE ENTRADA: como se corre esto, que comandos "
               "hay, como se corren los tests y que necesita instalado. Comandos concretos y "
               "copiables.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 300, "y": 60},
    {"id": "dependencias",
     "titulo": "Lista las dependencias de {{ruta}} y de cuales depende el proyecto de verdad "
               "(las que si faltan lo rompen). Marca las que esten sin fijar de version y las "
               "que parezcan abandonadas.",
     "runtime": "antigravity", "workspace": "{{ruta}}", "x": 560, "y": 60},
    {"id": "cobertura",
     "titulo": "En {{ruta}} mira que esta cubierto por tests y que NO. No corras nada pesado: "
               "alcanza con ver que tests hay y que modulos no aparecen en ninguno. Nombra las "
               "tres zonas mas desprotegidas.",
     "runtime": "opencode", "workspace": "{{ruta}}", "x": 820, "y": 60},
    {"id": "riesgos",
     "titulo": "Tus padres mapearon el repo. Deci por donde empezaria alguien nuevo y DONDE "
               "estan los riesgos: lo mas enredado, lo que no tiene tests, lo que se rompe "
               "facil. Maximo 10 lineas, ordenado por lo que mas duele.",
     "runtime": "claude-code", "workspace": "{{ruta}}", "x": 430, "y": 300},
  ],
  [["mapa", "riesgos"], ["entradas", "riesgos"],
   ["dependencias", "riesgos"], ["cobertura", "riesgos"]])

for f in sorted(P.glob("*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    ejec = [n for n in d["nodos"] if n.get("tipo") != "nota"]
    print("%-20s %d nodos + %d nota, %d aristas, reglas de %d lineas" % (
        f.stem, len(ejec), len(d["nodos"]) - len(ejec), len(d["aristas"]),
        len(d["reglas"].splitlines())))
