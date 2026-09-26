import { describe, expect, it } from "vitest";
import { type Chat, inicial, reducir } from "../src/chat";

const pasos = (...acciones: Parameters<typeof reducir>[1][]): Chat =>
	acciones.reduce(reducir, inicial);

describe("chat", () => {
	it("enviar agrega el mensaje del usuario y deja el turno esperando", () => {
		const c = pasos({ tipo: "enviado", texto: "hola" });
		expect(c.mensajes).toEqual([
			{ id: 1, rol: "usuario", texto: "hola", estado: "listo" },
		]);
		expect(c.turno).toBe("esperando");
	});

	it("la respuesta llega por deltas y se cierra con message.complete", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{ tipo: "evento", evento: "message.start", payload: null },
			{ tipo: "evento", evento: "message.delta", payload: { text: "Ho" } },
			{ tipo: "evento", evento: "message.delta", payload: { text: "la" } },
		);
		expect(c.mensajes[1]).toMatchObject({
			rol: "agente",
			texto: "Hola",
			estado: "escribiendo",
		});
		expect(c.turno).toBe("respondiendo");
		const fin = reducir(c, {
			tipo: "evento",
			evento: "message.complete",
			payload: { text: "Hola.", status: "complete" },
		});
		expect(fin.mensajes[1]).toMatchObject({ texto: "Hola.", estado: "listo" });
		expect(fin.turno).toBe("libre");
	});

	it("sin deltas (claude-subscription) el texto llega entero en message.complete", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{ tipo: "evento", evento: "message.start", payload: null },
			{
				tipo: "evento",
				evento: "message.complete",
				payload: { text: "Respuesta completa", status: "complete" },
			},
		);
		expect(c.mensajes[1]).toMatchObject({
			texto: "Respuesta completa",
			estado: "listo",
		});
	});

	it("message.complete sin message.start igual crea la respuesta", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{
				tipo: "evento",
				evento: "message.complete",
				payload: { text: "ok", status: "complete" },
			},
		);
		expect(c.mensajes.map((m) => m.rol)).toEqual(["usuario", "agente"]);
	});

	it("la actividad muestra lo que hace el agente, sin el texto de relleno del pensamiento", () => {
		let c = pasos(
			{ tipo: "enviado", texto: "x" },
			{ tipo: "evento", evento: "message.start", payload: null },
		);
		c = reducir(c, {
			tipo: "evento",
			evento: "thinking.delta",
			payload: { text: "( •_•)>⌐■-■ synthesizing..." },
		});
		expect(c.actividad).toBe("pensando");
		c = reducir(c, {
			tipo: "evento",
			evento: "tool.start",
			payload: { name: "delegar", args: { ruta: "opencode" } },
		});
		expect(c.actividad).toBe("delegando en opencode");
		c = reducir(c, {
			tipo: "evento",
			evento: "tool.start",
			payload: { name: "terminal", args: {} },
		});
		expect(c.actividad).toBe("usando terminal");
		c = reducir(c, {
			tipo: "evento",
			evento: "tool.complete",
			payload: { name: "terminal" },
		});
		expect(c.actividad).toBeNull();
	});

	it("un turno interrumpido o con error queda marcado y libera el turno", () => {
		const base = pasos(
			{ tipo: "enviado", texto: "x" },
			{ tipo: "evento", evento: "message.start", payload: null },
		);
		const cortado = reducir(base, {
			tipo: "evento",
			evento: "message.complete",
			payload: { text: "", status: "interrupted" },
		});
		expect(cortado.mensajes[1].estado).toBe("interrumpido");
		expect(cortado.turno).toBe("libre");
		const roto = reducir(base, {
			tipo: "evento",
			evento: "message.complete",
			payload: { text: "", status: "error" },
		});
		expect(roto.mensajes[1].estado).toBe("error");
	});

	it("un fallo al enviar deja el error visible y el texto para reintentar", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{ tipo: "fallo", mensaje: "gateway sin conexión" },
		);
		expect(c.error).toEqual({ mensaje: "gateway sin conexión", texto: "hola" });
		expect(c.turno).toBe("libre");
		expect(c.mensajes[0].estado).toBe("error");
	});

	it("eventos desconocidos no cambian nada", () => {
		const c = pasos({ tipo: "enviado", texto: "x" });
		expect(
			reducir(c, { tipo: "evento", evento: "session.usage", payload: {} }),
		).toBe(c);
	});

	it("nueva conversacion vuelve al estado inicial", () => {
		expect(pasos({ tipo: "enviado", texto: "x" }, { tipo: "nueva" })).toEqual(
			inicial,
		);
	});

	it("un fallo con la respuesta a medias no ofrece reenviar el texto del agente", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{ tipo: "evento", evento: "message.delta", payload: { text: "Ho" } },
			{ tipo: "fallo", mensaje: "gateway sin conexión" },
		);
		expect(c.error).toEqual({ mensaje: "gateway sin conexión", texto: null });
		expect(c.mensajes[0].estado).toBe("listo");
	});
});
