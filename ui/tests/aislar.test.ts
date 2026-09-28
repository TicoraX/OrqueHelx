import { describe, expect, it } from "vitest";
import { aislar, type Nodo } from "../src/aislar";

/** Arbol de nodos falsos: html > (head, body > (app > (nav, main), capa > ohx)). */
function arbol() {
	const nodo = (tagName: string, ...hijos: Nodo[]): Nodo => {
		const n: Nodo = {
			tagName,
			parentElement: null,
			children: hijos,
			inert: false,
		};
		for (const h of hijos) h.parentElement = n;
		return n;
	};
	const ohx = nodo("DIV");
	const nav = nodo("NAV");
	const main = nodo("MAIN");
	const app = nodo("DIV", nav, main);
	const capa = nodo("DIV", ohx);
	const head = nodo("HEAD");
	const body = nodo("BODY", app, capa);
	nodo("HTML", head, body);
	return { ohx, nav, main, app, capa, head, body };
}

describe("aislar la pantalla sobre el dashboard", () => {
	it("deja inerte todo lo que no contiene la pantalla y lo restaura al salir", () => {
		const n = arbol();
		const soltar = aislar(n.ohx);
		expect(n.app.inert).toBe(true);
		expect([n.capa.inert, n.ohx.inert, n.body.inert, n.head.inert]).toEqual([
			false,
			false,
			false,
			false,
		]);
		soltar();
		expect(n.app.inert).toBe(false);
	});

	it("no despierta lo que Hermes ya tenia inerte", () => {
		const n = arbol();
		n.app.inert = true;
		aislar(n.ohx)();
		expect(n.app.inert).toBe(true);
	});
});
