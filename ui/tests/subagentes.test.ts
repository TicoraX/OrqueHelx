import { describe, expect, it } from "vitest";
import {
	type Arbol,
	enOrden,
	ficha,
	reducirArbol,
	type Subagente,
	vacio,
} from "../src/subagentes";

// Payloads recortados de una sesion real del gateway (spike/sonda_ws.jsonl).
const inicio = (extra: Record<string, unknown> = {}) => ({
	goal: "Responde solo: listo",
	subagent_id: "sa-0-b4e3bba3",
	delegation_id: "deleg_8824125b",
	depth: 0,
	model: "claude-haiku-4-5",
	...extra,
});

const pasos = (...eventos: [string, unknown][]): Arbol =>
	eventos.reduce((a, [tipo, p]) => reducirArbol(a, tipo, p), vacio);

const delegar = (
	toolId: string,
	ruta: string,
	objetivo = "Responde solo: listo",
) =>
	[
		"tool.start",
		{ tool_id: toolId, name: "delegar", args: { ruta, objetivo } },
	] as [string, unknown];

describe("arbol de subagentes", () => {
	it("la ruta sale de la llamada a delegar, no del modelo que informa el subagente", () => {
		const a = pasos(delegar("t1", "opencode"), ["subagent.start", inicio()]);
		expect(a.nodos).toEqual([
			{
				id: "sa-0-b4e3bba3",
				padre: null,
				ruta: "opencode",
				objetivo: "Responde solo: listo",
				estado: "corriendo",
				actividad: null,
				resumen: null,
				duracion: null,
				reinicio: null,
			},
		]);
	});

	it("muestra que piensa o que herramienta usa, sin el texto de relleno", () => {
		let a = pasos(delegar("t1", "opencode"), ["subagent.start", inicio()]);
		a = reducirArbol(
			a,
			"subagent.thinking",
			inicio({ text: "(´･_･`) processing..." }),
		);
		expect(a.nodos[0].actividad).toBe("pensando");
		a = reducirArbol(
			a,
			"subagent.tool",
			inicio({ tool_name: "read_file", text: "leyendo a.py" }),
		);
		expect(a.nodos[0].actividad).toBe("usando read_file");
	});

	it("subagent.complete cambia el estado en el sitio con resumen y duracion", () => {
		const a = pasos(
			delegar("t1", "opencode"),
			["subagent.start", inicio()],
			[
				"subagent.complete",
				inicio({
					status: "completed",
					summary: "listo",
					duration_seconds: 22.08,
				}),
			],
		);
		expect(a.nodos).toHaveLength(1);
		expect(a.nodos[0]).toMatchObject({
			estado: "completado",
			actividad: null,
			resumen: "listo",
			duracion: 22.08,
		});
	});

	it("un estado final desconocido se muestra como fallido, no como completado", () => {
		const a = pasos(
			delegar("t1", "opencode"),
			["subagent.start", inicio()],
			["subagent.complete", inicio({ status: "timeout", summary: "" })],
		);
		expect(a.nodos[0].estado).toBe("fallido");
		expect(a.nodos[0].resumen).toBeNull();
	});

	it("dos delegaciones se asignan en orden de llamada", () => {
		const a = pasos(
			delegar("t1", "opencode", "uno"),
			delegar("t2", "agy", "dos"),
			["subagent.start", inicio({ subagent_id: "a", goal: "uno" })],
			["subagent.start", inicio({ subagent_id: "b", goal: "dos" })],
		);
		expect(a.nodos.map((n) => [n.id, n.ruta])).toEqual([
			["a", "opencode"],
			["b", "agy"],
		]);
	});

	it("un nieto cuelga de su padre y no consume la ruta de otra delegacion", () => {
		const a = pasos(
			delegar("t1", "opencode"),
			["subagent.start", inicio({ subagent_id: "a" })],
			[
				"subagent.start",
				inicio({ subagent_id: "a.1", parent_id: "a", depth: 1, goal: "sub" }),
			],
		);
		expect(a.nodos[1]).toMatchObject({
			id: "a.1",
			padre: "a",
			ruta: null,
			objetivo: "sub",
		});
	});

	it("sin_cuota crea el nodo sin fingir que corrio, con la hora de reinicio", () => {
		const a = pasos(delegar("t1", "claude"), [
			"tool.complete",
			{
				tool_id: "t1",
				name: "delegar",
				result: {
					estado: "sin_cuota",
					ruta: "claude",
					reinicio: "2026-09-26T19:00:00+00:00",
				},
			},
		]);
		expect(a.nodos).toEqual([
			expect.objectContaining({
				id: "t1",
				ruta: "claude",
				estado: "sin_cuota",
				reinicio: "2026-09-26T19:00:00+00:00",
			}),
		]);
		expect(a.pendientes).toEqual([]);
	});

	it("una delegacion rechazada muestra el motivo", () => {
		const a = pasos(delegar("t1", "claude"), [
			"tool.complete",
			{
				tool_id: "t1",
				name: "delegar",
				result: { error: "ruta desconocida 'x'" },
			},
		]);
		expect(a.nodos[0]).toMatchObject({
			estado: "fallido",
			resumen: "ruta desconocida 'x'",
		});
	});

	it("tool.complete de una delegacion que si corrio no duplica el nodo", () => {
		const a = pasos(
			delegar("t1", "opencode"),
			["subagent.start", inicio()],
			["subagent.complete", inicio({ status: "completed", summary: "listo" })],
			[
				"tool.complete",
				{
					tool_id: "t1",
					name: "delegar",
					result: { results: [{ status: "completed" }] },
				},
			],
		);
		expect(a.nodos).toHaveLength(1);
		expect(a.pendientes).toEqual([]);
	});

	it("ignora otras herramientas y eventos de subagentes desconocidos", () => {
		const a = pasos(
			["tool.start", { tool_id: "t9", name: "read_file", args: {} }],
			[
				"subagent.complete",
				inicio({ subagent_id: "nadie", status: "completed" }),
			],
		);
		expect(a).toEqual(vacio);
	});
});

describe("vista de subagentes", () => {
	const nodo = (extra: Partial<Subagente>): Subagente => ({
		id: "a",
		padre: null,
		ruta: "opencode",
		objetivo: "x",
		estado: "corriendo",
		actividad: null,
		resumen: null,
		duracion: null,
		reinicio: null,
		...extra,
	});

	it("corriendo muestra la actividad; terminado, el resumen y la duracion", () => {
		expect(ficha(nodo({ actividad: "usando read_file" }))).toEqual({
			tono: "normal",
			cifra: "corriendo",
			lineas: ["usando read_file"],
		});
		expect(
			ficha(nodo({ estado: "completado", resumen: "listo", duracion: 22.08 })),
		).toEqual({
			tono: "normal",
			cifra: "completado",
			lineas: ["listo", "22,1 s"],
		});
	});

	it("el resumen de la casilla es texto plano, sin marcas de markdown", () => {
		// Resultado real de opencode (F5): valla de codigo y codigo en linea.
		const resumen =
			"```python\ndef es_bisiesto(año): return año % 4 == 0\n```\nRegla **gregoriana**: `es_bisiesto(2024)` → True";
		expect(ficha(nodo({ estado: "completado", resumen })).lineas[0]).toBe(
			"def es_bisiesto(año): return año % 4 == 0 Regla gregoriana: es_bisiesto(2024) → True",
		);
	});

	it("sin cuota lleva el tono de ruta agotada y la hora de reinicio, o sin dato", () => {
		const ahora = new Date(2026, 8, 26, 12, 0);
		const reinicio = new Date(2026, 8, 26, 19, 5).toISOString();
		expect(ficha(nodo({ estado: "sin_cuota", reinicio }), ahora)).toEqual({
			tono: "agotada",
			cifra: "sin cuota",
			lineas: ["reinicia 19:05"],
		});
		expect(ficha(nodo({ estado: "sin_cuota" }), ahora).lineas).toEqual([
			"reinicia sin dato",
		]);
	});

	it("fallido e interrumpido no se ven como exito", () => {
		expect(
			ficha(nodo({ estado: "fallido", resumen: "timeout" })),
		).toMatchObject({
			tono: "error",
			cifra: "fallido",
		});
		expect(ficha(nodo({ estado: "interrumpido" }))).toMatchObject({
			tono: "sin_dato",
			cifra: "detenido",
		});
	});

	it("ordena en profundidad: cada hijo va debajo de su padre con su nivel", () => {
		const nodos = [
			nodo({ id: "a" }),
			nodo({ id: "b" }),
			nodo({ id: "a.1", padre: "a" }),
			nodo({ id: "a.1.1", padre: "a.1" }),
		];
		expect(enOrden(nodos).map(([n, nivel]) => [n.id, nivel])).toEqual([
			["a", 0],
			["a.1", 1],
			["a.1.1", 2],
			["b", 0],
		]);
	});

	it("un hijo cuyo padre no llego igual se muestra, en la raiz", () => {
		expect(
			enOrden([nodo({ id: "h", padre: "perdido" })]).map(([n, nivel]) => [
				n.id,
				nivel,
			]),
		).toEqual([["h", 0]]);
	});
});
