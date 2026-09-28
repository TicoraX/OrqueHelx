// La pantalla tapa el dashboard de Hermes, pero su navegacion sigue en el DOM: sin esto, Tab y los lectores
// de pantalla recorren enlaces que no se ven. Mientras la pantalla esta montada, lo demas queda `inert`.

/** Lo que usa aislar de un Element; basta para probarlo sin DOM. */
export interface Nodo {
	tagName: string;
	parentElement: Nodo | null;
	children: ArrayLike<Nodo>;
	/** Opcional: los hijos son Element, y en el DOM solo HTMLElement declara inert. */
	inert?: boolean;
}

/** Vuelve inerte cada hermano de la cadena nodo -> body; devuelve la funcion que lo restaura. */
export function aislar(nodo: Nodo): () => void {
	const tocados: Nodo[] = [];
	for (
		let n = nodo;
		n.parentElement && n.parentElement.tagName !== "HTML";
		n = n.parentElement
	) {
		for (const hermano of Array.from(n.parentElement.children)) {
			if (hermano === n || hermano.inert) continue;
			hermano.inert = true;
			tocados.push(hermano);
		}
	}
	return () => {
		for (const t of tocados) t.inert = false;
	};
}
