"""Acomodar un grafo: coordenadas a partir de la topología, no del pulso.

Un grafo armado a mano queda con los nodos donde uno los soltó, y a partir de
cinco o seis se cruzan las flechas. Esto calcula dónde va cada nodo.

Vive acá y no en el navegador porque **el orden por capas ya existe**:
`compile._orden_topologico` hace Kahn para saber en qué orden crear las cards.
Reimplementar Kahn en JavaScript sería el mismo algoritmo dos veces, y la
segunda copia se desincroniza de la primera.

La UI no decide nada: manda el grafo, recibe `{id: {x, y}}` y lo aplica.
"""
from compile import es_nota, _orden_topologico

# Tienen que coincidir con las del canvas (`A` y `ALTO` en ui/index.html). Si
# cambian allá, acá quedan chicas y los nodos se tocan: es cuestión de estética,
# no de correctitud, porque la UI aplica las coordenadas tal cual llegan.
ANCHO, ALTO = 196, 60
ANCHO_NOTA = int(ANCHO * 1.35)
SEP_X, SEP_Y = 64, 150
MARGEN = 40


def _capas(grafo: dict) -> dict[str, int]:
    """Capa de cada nodo ejecutable: 1 + la capa más profunda de sus padres.

    Se recorre en orden topológico, así que cuando se calcula un nodo sus
    padres ya tienen capa. Es la profundidad del camino MÁS LARGO, no la del
    primero que llega: si un nodo depende de uno de capa 0 y otro de capa 2,
    va en la 3 y no en la 1, o la flecha subiría en vez de bajar.
    """
    padres: dict[str, list[str]] = {n["id"]: [] for n in grafo["nodos"]
                                    if not es_nota(n)}
    for p, h in grafo.get("aristas") or []:
        if p in padres and h in padres:
            padres[h].append(p)

    capa: dict[str, int] = {}
    for nodo in _orden_topologico(grafo):          # ignora las notas
        i = nodo["id"]
        capa[i] = 1 + max((capa[p] for p in padres[i]), default=-1)
    return capa


def ordenar(grafo: dict) -> dict[str, dict]:
    """Coordenadas nuevas para todos los nodos. No muta el grafo."""
    capa = _capas(grafo)
    por_capa: dict[int, list[str]] = {}
    for i, c in capa.items():
        por_capa.setdefault(c, []).append(i)

    # El orden dentro de la capa se hereda del grafo, no del diccionario: dos
    # llamadas seguidas tienen que dar lo mismo o el boton "Ordenar" haria
    # saltar los nodos cada vez que se aprieta.
    orden_original = [n["id"] for n in grafo["nodos"]]
    for ids in por_capa.values():
        ids.sort(key=orden_original.index)

    # Las notas no tienen aristas, asi que la topologia no las ubica: van a su
    # propia columna a la izquierda. Sin esto se apilarian todas en la capa 0,
    # encima del primer nodo.
    notas = [n["id"] for n in grafo["nodos"] if es_nota(n)]
    x0 = MARGEN + (ANCHO_NOTA + SEP_X if notas else 0)

    ancho_max = max((len(ids) for ids in por_capa.values()), default=1)
    centro = x0 + (ancho_max * (ANCHO + SEP_X) - SEP_X) / 2

    pos = {}
    for c, ids in por_capa.items():
        fila = len(ids) * (ANCHO + SEP_X) - SEP_X
        x = centro - fila / 2
        for k, i in enumerate(ids):
            pos[i] = {"x": round(x + k * (ANCHO + SEP_X)), "y": MARGEN + c * SEP_Y}

    for k, i in enumerate(notas):
        pos[i] = {"x": MARGEN, "y": MARGEN + k * (ALTO * 2 + SEP_X)}
    return pos
