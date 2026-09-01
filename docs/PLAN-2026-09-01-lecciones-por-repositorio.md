# Plan: lecciones por repositorio — 2026-09-01

Origen: split de `docs/PLAN-2026-09-01-terminal-y-lecciones.md` — la revisión
CEO marcó que T2 (esto) es una ganancia simple y de bajo riesgo que no debería
esperar a T1 (terminal en vivo, arquitectura más riesgosa). PR separado,
puede mergear primero.

## 0. Premisas

1. **El punto de inyección ya existe y no hay que tocarlo.**
   `compiler/compile.py:270-281` ya concatena `grafo["reglas"]` al `body` de
   cada nodo al compilar (`ENCABEZADO_REGLAS`). Las lecciones por repo son
   el MISMO mecanismo, una fuente más: un archivo en el workspace del nodo
   (`<workspace>/.orquester-lecciones.md`), leído en el mismo punto y
   concatenado igual.
2. **No hay nodo `hermes` sin workspace que pueda tener lecciones** —
   coherente con el hallazgo del visor de diff: sin `workspace` explícito
   no hay carpeta que identifique "este repo".
3. **Lectura de archivo local, sin hardening especial.** A diferencia de
   las operaciones git del visor de diff (que invocan un intérprete vía
   `textconv` y necesitaron neutralizar variables de entorno), esto es un
   `.read_text()` plano — no hay proceso hijo ni intérprete de por medio.
   No aplica el mismo endurecimiento, solo el patrón general: cap de
   tamaño + degradar con gracia si el archivo no existe.
4. **No hay allowlist de rutas de workspace** — mismo precedente que
   `_analizar_workspace`: el token de la API es la barrera real, no una
   lista de paths permitidos.
5. **Cap de tamaño: 4000 caracteres**, con un marcador visible de
   truncamiento (`[... lecciones truncadas, archivo más largo ...]`) si se
   excede — evita inflar el contexto del agente sin límite.
6. **Staleness**: un archivo de lecciones editado a mano puede quedar
   desactualizado sin que nadie lo note. Mitigación barata: si se guarda
   desde el panel de la UI, anteponer un encabezado con la fecha
   (`<!-- actualizado 2026-09-01 -->`) — no resuelve el problema de fondo,
   pero da una señal visible de cuán vieja es la lección al leerla.

## 1. Alcance

**Adentro:**
- Lectura de `<workspace>/.orquester-lecciones.md` al compilar, concatenada
  a `reglas` con el mismo mecanismo existente (Premisas 1, 5).
- Panel simple en la UI para ver/editar el archivo del workspace del nodo
  seleccionado, con sello de fecha al guardar (Premisa 6).

**Afuera:**
- Inferencia automática de lecciones a partir de fallos — requiere un
  agente extra analizando, mucho más caro y con riesgo de alucinar reglas
  falsas. Manual por ahora.
- Lecciones compartidas entre workspaces distintos del mismo repo lógico
  (ej. dos checkouts del mismo repo en rutas distintas) — el archivo es
  por ruta, no por identidad de repo (hash de remote, etc.).

## 2. Tareas

### L1 · Lectura de lecciones al compilar
- **Qué**: en `compiler/compile.py`, junto a la lectura de `reglas` (línea
  270), para cada nodo con `workspace`: si existe
  `<workspace>/.orquester-lecciones.md`, leerlo (capado a 4000 chars con
  marcador de truncamiento), concatenarlo DESPUÉS de `reglas` con su
  propio encabezado (`## Lecciones de este repositorio`).
- **Riesgo**: ninguno nuevo — mismo mecanismo que `reglas`, ya probado.
- **Test**: un nodo con workspace que tiene lecciones → aparecen en el
  `body`; un workspace sin el archivo → compila igual, sin error; un
  archivo de más de 4000 chars → se trunca con el marcador visible.

### L2 · Panel para ver/editar lecciones
- **Qué**: en el panel del nodo, si tiene `workspace`, un botón/textarea
  que lee y escribe `<workspace>/.orquester-lecciones.md` directo (no hay
  base de datos nueva — el archivo ES el dato, vive con el repo, es
  git-trackable si el usuario quiere versionarlo). Al guardar, antepone
  `<!-- actualizado YYYY-MM-DD -->` como primera línea.
- **Test**: Playwright — abrir, editar, guardar, releer, confirmar el
  sello de fecha.

## 3. Qué NO resuelve este plan

- Inferencia automática de lecciones desde fallos.
- Lecciones compartidas entre workspaces del mismo repo lógico.
- Terminal en vivo — ver `docs/PLAN-2026-09-01-terminal-en-vivo.md`.
