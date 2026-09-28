// Arbol de subagentes de la conversacion, a partir de los eventos del gateway (reductor puro).
//
//   tool.start delegar{ruta}  -> pendiente (la ruta solo viaja aca: subagent.* informa el modelo del padre)
//   subagent.start depth 0    -> nodo con la ruta de la pendiente mas vieja sin asignar
//   subagent.thinking/tool    -> actividad
//   subagent.complete         -> estado final en el sitio
//   tool.complete delegar     -> si no arranco subagente: sin_cuota o rechazo, nodo con ese estado

import { hora, type Resumen, type Tono, unido } from "./estado";
import { plano } from "./markdown";

type EstadoSubagente =
	| "corriendo"
	| "completado"
	| "interrumpido"
	| "fallido"
	| "sin_cuota";

export interface Subagente {
	/** subagent_id de Hermes, o el tool_id de delegar si nunca arranco. */
	id: string;
	padre: string | null;
	/** null en nietos: su llamada a delegar ocurre dentro del hijo y no llega a esta sesion. */
	ruta: string | null;
	objetivo: string;
	estado: EstadoSubagente;
	actividad: string | null;
	resumen: string | null;
	duracion: number | null;
	/** Solo con sin_cuota: ISO del reinicio, o null si el proveedor no lo informa. */
	reinicio: string | null;
}

interface Pendiente {
	toolId: string;
	ruta: string;
	objetivo: string;
	subagente: string | null;
}

export interface Arbol {
	nodos: Subagente[];
	pendientes: Pendiente[];
}

export const vacio: Arbol = { nodos: [], pendientes: [] };

type Payload = Record<string, unknown>;

const FINAL: Record<string, EstadoSubagente> = {
	completed: "completado",
	interrupted: "interrumpido",
};

const texto = (v: unknown): string | null =>
	typeof v === "string" && v ? v : null;

function cambiarNodo(
	a: Arbol,
	id: unknown,
	cambio: (n: Subagente) => Subagente,
): Arbol {
	if (!a.nodos.some((n) => n.id === id)) return a;
	return { ...a, nodos: a.nodos.map((n) => (n.id === id ? cambio(n) : n)) };
}

function inicio(a: Arbol, p: Payload): Arbol {
	const id = texto(p.subagent_id);
	if (!id || a.nodos.some((n) => n.id === id)) return a;
	const padre = texto(p.parent_id);
	const i = padre ? -1 : a.pendientes.findIndex((d) => d.subagente === null);
	const pendiente = i >= 0 ? a.pendientes[i] : null;
	const nodo: Subagente = {
		id,
		padre,
		ruta: pendiente?.ruta ?? null,
		objetivo: texto(p.goal) ?? pendiente?.objetivo ?? "",
		estado: "corriendo",
		actividad: null,
		resumen: null,
		duracion: null,
		reinicio: null,
	};
	return {
		nodos: [...a.nodos, nodo],
		pendientes: pendiente
			? a.pendientes.map((d, j) => (j === i ? { ...d, subagente: id } : d))
			: a.pendientes,
	};
}

function finDeDelegar(a: Arbol, p: Payload): Arbol {
	const pendiente = a.pendientes.find((d) => d.toolId === p.tool_id);
	if (!pendiente) return a;
	const pendientes = a.pendientes.filter((d) => d !== pendiente);
	if (pendiente.subagente !== null) return { ...a, pendientes };
	// Hermes ya parseo el JSON de delegar; un resultado que no es objeto no trae estado ni motivo.
	const r = (
		p.result && typeof p.result === "object" ? p.result : {}
	) as Payload;
	const sinCuota = r.estado === "sin_cuota";
	const nodo: Subagente = {
		id: pendiente.toolId,
		padre: null,
		ruta: pendiente.ruta,
		objetivo: pendiente.objetivo,
		estado: sinCuota ? "sin_cuota" : "fallido",
		actividad: null,
		resumen: sinCuota ? null : texto(r.error),
		duracion: null,
		reinicio: sinCuota ? texto(r.reinicio) : null,
	};
	return { nodos: [...a.nodos, nodo], pendientes };
}

export function reducirArbol(
	a: Arbol,
	evento: string,
	payload: unknown,
): Arbol {
	const p = (payload && typeof payload === "object" ? payload : {}) as Payload;
	switch (evento) {
		case "tool.start": {
			const args = (p.args ?? {}) as Payload;
			const toolId = texto(p.tool_id);
			const ruta = texto(args.ruta);
			if (p.name !== "delegar" || !toolId || !ruta) return a;
			const pendiente = {
				toolId,
				ruta,
				objetivo: texto(args.objetivo) ?? "",
				subagente: null,
			};
			return { ...a, pendientes: [...a.pendientes, pendiente] };
		}
		case "tool.complete":
			return p.name === "delegar" ? finDeDelegar(a, p) : a;
		case "subagent.start":
			return inicio(a, p);
		case "subagent.thinking":
			return cambiarNodo(a, p.subagent_id, (n) => ({
				...n,
				actividad: "pensando",
			}));
		case "subagent.tool":
			return cambiarNodo(a, p.subagent_id, (n) => ({
				...n,
				actividad: `usando ${texto(p.tool_name) ?? "una herramienta"}`,
			}));
		case "subagent.complete":
			return cambiarNodo(a, p.subagent_id, (n) => ({
				...n,
				estado: FINAL[String(p.status)] ?? "fallido",
				actividad: null,
				resumen: texto(p.summary),
				duracion:
					typeof p.duration_seconds === "number" ? p.duration_seconds : null,
			}));
		default:
			return a;
	}
}

const CIFRA: Record<EstadoSubagente, [Tono, string]> = {
	corriendo: ["normal", "corriendo"],
	completado: ["normal", "completado"],
	interrumpido: ["sin_dato", "detenido"],
	fallido: ["error", "fallido"],
	sin_cuota: ["agotada", "sin cuota"],
};

/** Lo que muestra la casilla de un subagente: mismo formato que la de una ruta. */
export function ficha(n: Subagente, ahora = new Date()): Resumen {
	const [tono, cifra] = CIFRA[n.estado];
	if (n.estado === "sin_cuota")
		return {
			tono,
			cifra,
			lineas: [unido(`reinicia ${hora(n.reinicio, ahora)}`)],
		};
	// El resumen viene en markdown; la casilla lo recorta a 4 lineas, asi que va sin marcas.
	const lineas =
		n.estado === "corriendo"
			? [n.actividad]
			: [n.resumen === null ? null : plano(n.resumen)];
	if (n.duracion !== null)
		lineas.push(
			`${String(Math.round(n.duracion * 10) / 10).replace(".", ",")} s`,
		);
	return { tono, cifra, lineas: lineas.filter((l): l is string => l !== null) };
}

/** Recorrido en profundidad con el nivel de cada nodo; un hijo sin padre conocido va a la raiz. */
export function enOrden(nodos: Subagente[]): [Subagente, number][] {
	const ids = new Set(nodos.map((n) => n.id));
	const salida: [Subagente, number][] = [];
	const visitar = (padre: string | null, nivel: number) => {
		for (const n of nodos) {
			const raiz = n.padre === null || !ids.has(n.padre);
			if (padre === null ? raiz : n.padre === padre) {
				salida.push([n, nivel]);
				visitar(n.id, nivel + 1);
			}
		}
	};
	visitar(null, 0);
	return salida;
}
