// Estado de las rutas tal como lo devuelve GET /api/plugins/orquehelx/estado, y como se lee en pantalla.

export interface Ventana {
	etiqueta: string;
	usado: number | null;
	reinicio: string | null;
}

export interface Medicion {
	estado: "medida" | "sin_dato" | "error";
	error: string | null;
	ventanas: Ventana[];
}

export interface RutaEstado {
	nombre: string;
	proveedor: string;
	modelo: string | null;
	acp: boolean;
	medicion: Medicion;
	agotada: { desde: number; reinicio: string | null } | null;
}

export interface EstadoRutas {
	rutas: RutaEstado[];
	error: string | null;
}

export type Tono = "normal" | "agotada" | "error" | "sin_dato";

export interface Resumen {
	tono: Tono;
	/** El dato principal de la casilla: porcentaje medido, "sin dato" o la hora en que vuelve. */
	cifra: string;
	lineas: string[];
}

/** Trunca a un decimal: 99.96 nunca se muestra como 100 (agotado). */
export function uso(usado: number | null): string {
	if (usado === null || !Number.isFinite(usado)) return "sin dato";
	return `${Math.floor(usado * 10) / 10} %`;
}

/** Hora local; con fecha si no es hoy. Sin hora: "sin dato", nunca una estimacion. */
export function hora(iso: string | null, ahora = new Date()): string {
	if (!iso) return "sin dato";
	const f = new Date(iso);
	if (Number.isNaN(f.getTime())) return "sin dato";
	// Formato manual: el ICU de cada navegador formatea "2-digit" distinto (Node da "28/9").
	const dos = (n: number) => String(n).padStart(2, "0");
	const hm = `${dos(f.getHours())}:${dos(f.getMinutes())}`;
	const mismoDia = f.toDateString() === ahora.toDateString();
	return mismoDia ? hm : `${dos(f.getDate())}/${dos(f.getMonth() + 1)} ${hm}`;
}

export function resumen(ruta: RutaEstado, ahora = new Date()): Resumen {
	if (ruta.agotada) {
		const desde = hora(
			new Date(ruta.agotada.desde * 1000).toISOString(),
			ahora,
		);
		return {
			tono: "agotada",
			cifra: `vuelve ${hora(ruta.agotada.reinicio, ahora)}`,
			lineas: [`sin cuota desde ${desde}`],
		};
	}
	const m = ruta.medicion;
	if (m.estado === "error")
		return {
			tono: "error",
			cifra: "sin dato",
			lineas: [`no se pudo medir: ${m.error}`],
		};
	if (m.estado === "sin_dato" || m.ventanas.length === 0) {
		return {
			tono: "sin_dato",
			cifra: "sin dato",
			lineas: ["el proveedor no informa consumo"],
		};
	}
	// La cifra principal es la ventana mas cargada: es la que primero corta la ruta.
	const principal = m.ventanas.reduce((a, b) =>
		(b.usado ?? -1) > (a.usado ?? -1) ? b : a,
	);
	return {
		tono: "normal",
		cifra: uso(principal.usado),
		lineas: m.ventanas.map(
			(v) =>
				`${v.etiqueta} ${uso(v.usado)}${v.reinicio ? `, reinicia ${hora(v.reinicio, ahora)}` : ""}`,
		),
	};
}
