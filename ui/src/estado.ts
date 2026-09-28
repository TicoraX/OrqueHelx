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
	/** Modelo por defecto del principal en la config de Hermes (antes del primer turno); null si no hay. */
	principal: { proveedor: string | null; modelo: string | null } | null;
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
	// Espacio no separable: el numero nunca queda en otra linea que su %.
	return `${Math.floor(usado * 10) / 10} %`;
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
		// "usado": el porcentaje es consumo, no saldo.
		cifra: `${uso(principal.usado)} usado`,
		lineas: m.ventanas.map(
			(v) =>
				// "reinicia" nunca queda en otra linea que su hora: si hace falta cortar, se corta en el "·".
				`${ventana(v.etiqueta)} ${uso(v.usado)}${v.reinicio ? ` · ${`reinicia ${hora(v.reinicio, ahora)}`.replaceAll(" ", " ")}` : ""}`,
		),
	};
}

const VENTANAS: [RegExp, string][] = [
	[/^current session$/i, "sesión"],
	[/^current week$/i, "semana"],
];

/** Etiqueta de ventana en espanol: las conocidas de Claude se traducen, la familia se conserva; el resto, tal cual. */
export function ventana(etiqueta: string): string {
	const familia = etiqueta.match(/^(.*?)\s*(\([^)]*\))$/);
	const base = familia ? familia[1] : etiqueta;
	const traducida = VENTANAS.find(([re]) => re.test(base))?.[1];
	if (!traducida) return etiqueta;
	return familia ? `${traducida} ${familia[2]}` : traducida;
}

/** Un solo nombre por modelo en toda la pantalla: Hermes informa el id con fecha (claude-haiku-4-5-20251001),
 * la config y las rutas el corto. */
export function nombreModelo(modelo: string | null | undefined): string | null {
	return modelo ? modelo.replace(/-\d{8}$/, "") : null;
}

/** "ruta · modelo" del principal: la ruta que usa su proveedor, o el proveedor si ninguna lo cubre. */
export function etiquetaPrincipal(
	p: { proveedor: string | null; modelo: string | null },
	rutas: RutaEstado[],
): string {
	const modelo = nombreModelo(p.modelo);
	const ruta = rutas.find((r) => r.proveedor === p.proveedor);
	const donde = ruta?.nombre ?? p.proveedor;
	return [donde, modelo].filter(Boolean).join(" · ");
}
