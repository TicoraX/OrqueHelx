// La pestaña /orquehelx vive dentro del <main> de Hermes, que recorta lo fijo por debajo de su barra
// lateral. La pantalla se dibuja en el slot "overlay" (raiz de la app) mientras la pestaña este montada;
// este es el unico estado que comparten.

let estaActiva = false;
const avisos = new Set<() => void>();

export function activa(): boolean {
	return estaActiva;
}

export function marcar(valor: boolean): void {
	if (valor === estaActiva) return;
	estaActiva = valor;
	for (const aviso of avisos) aviso();
}

export function suscribir(aviso: () => void): () => void {
	avisos.add(aviso);
	return () => avisos.delete(aviso);
}
