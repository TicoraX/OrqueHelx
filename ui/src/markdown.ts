// Markdown de las respuestas del agente a una estructura pura; App.tsx la pinta con elementos de React,
// nunca con innerHTML, asi que el texto del modelo no se interpreta como HTML.
// ponytail: subconjunto (parrafos, codigo, listas, titulos, negrita, cursiva, enlaces http[s]); tablas y
// citas salen como texto. Si hace falta mas, un parser real (marked + sanitizado) cuesta ~20 KB gzip.

export type Tramo =
	| { t: "texto" | "negrita" | "cursiva" | "codigo"; v: string }
	| { t: "enlace"; v: string; href: string };

export type Bloque =
	| { t: "parrafo"; lineas: Tramo[][] }
	| { t: "codigo"; lenguaje: string; v: string }
	| { t: "lista"; ordenada: boolean; inicio: number; items: Tramo[][] }
	| { t: "titulo"; tramos: Tramo[] };

// Orden de alternativas = prioridad: el codigo gana a todo lo demas.
// Cursiva con _ solo entre limites de palabra, para que snake_case quede como texto.
const INLINE =
	/`([^`]+)`|\*\*([^*]+)\*\*|\*(?!\s)([^*]+?)(?<!\s)\*|(?<!\w)_(?!\s)([^_]+?)(?<!\s)_(?!\w)|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gu;

export function tramos(texto: string): Tramo[] {
	const salida: Tramo[] = [];
	let desde = 0;
	for (const m of texto.matchAll(INLINE)) {
		if (m.index > desde)
			salida.push({ t: "texto", v: texto.slice(desde, m.index) });
		if (m[1] !== undefined) salida.push({ t: "codigo", v: m[1] });
		else if (m[2] !== undefined) salida.push({ t: "negrita", v: m[2] });
		else if (m[3] !== undefined || m[4] !== undefined)
			salida.push({ t: "cursiva", v: (m[3] ?? m[4]) as string });
		else salida.push({ t: "enlace", v: m[5] as string, href: m[6] as string });
		desde = m.index + m[0].length;
	}
	if (desde < texto.length) salida.push({ t: "texto", v: texto.slice(desde) });
	return salida;
}

const VINETA = /^\s*[-*+]\s+(.*)$/u;
const NUMERO = /^\s*\d+[.)]\s+(.*)$/u;
const TITULO = /^#{1,6}\s+(.*)$/u;
const VALLA = /^\s*```(\S*)/u;
const CIERRE = /^\s*```\s*$/u;

export function bloques(texto: string): Bloque[] {
	const salida: Bloque[] = [];
	const lineas = texto.split(/\r?\n/u);
	for (let i = 0; i < lineas.length; ) {
		const linea = lineas[i] as string;
		const valla = linea.match(VALLA);
		if (valla) {
			// Sin valla de cierre (respuesta a medio llegar) el resto es codigo.
			const fin = lineas.findIndex((l, j) => j > i && CIERRE.test(l));
			const hasta = fin === -1 ? lineas.length : fin;
			salida.push({
				t: "codigo",
				lenguaje: valla[1] ?? "",
				v: lineas.slice(i + 1, hasta).join("\n"),
			});
			i = hasta + 1;
			continue;
		}
		if (!linea.trim()) {
			i++;
			continue;
		}
		const titulo = linea.match(TITULO);
		if (titulo) {
			salida.push({ t: "titulo", tramos: tramos(titulo[1] as string) });
			i++;
			continue;
		}
		const patron = [VINETA, NUMERO].find((p) => p.test(linea));
		if (patron) {
			// Una lista numerada cortada por un parrafo sigue en su numero (<ol start>).
			const inicio = Number.parseInt(linea, 10) || 1;
			const items: Tramo[][] = [];
			for (; i < lineas.length && patron.test(lineas[i] as string); i++)
				items.push(tramos((lineas[i] as string).match(patron)?.[1] as string));
			salida.push({ t: "lista", ordenada: patron === NUMERO, inicio, items });
			continue;
		}
		const parrafo: Tramo[][] = [];
		for (; i < lineas.length; i++) {
			const l = lineas[i] as string;
			if (
				!l.trim() ||
				VALLA.test(l) ||
				TITULO.test(l) ||
				VINETA.test(l) ||
				NUMERO.test(l)
			)
				break;
			parrafo.push(tramos(l));
		}
		salida.push({ t: "parrafo", lineas: parrafo });
	}
	return salida;
}
