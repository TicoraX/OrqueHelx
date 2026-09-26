import { describe, expect, it } from "vitest";
import { type Chat, inicial, principalDe, reducir } from "../src/chat";

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

	it("los eventos de subagentes arman el arbol y nueva conversacion lo vacia", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "delega" },
			{
				tipo: "evento",
				evento: "tool.start",
				payload: {
					tool_id: "t1",
					name: "delegar",
					args: { ruta: "opencode", objetivo: "x" },
				},
			},
			{
				tipo: "evento",
				evento: "subagent.start",
				payload: { subagent_id: "a", goal: "x", depth: 0 },
			},
		);
		expect(c.subagentes.nodos.map((n) => [n.id, n.ruta, n.estado])).toEqual([
			["a", "opencode", "corriendo"],
		]);
		expect(c.actividad).toBe("delegando en opencode");
		expect(reducir(c, { tipo: "nueva" })).toEqual(inicial);
	});

	const PAUSA = {
		id: 7,
		sesion: "20260926_190000_ab12",
		proveedor: "claude-subscription-directsdk-experimental",
		modelo: "claude-haiku-4-5",
		reinicio: "2026-09-26T19:00:00+00:00",
		estado: "pausado",
		creado: 1790000000,
	};

	it("una pausa deja el chat estatico con la pausa a la vista", () => {
		const c = pasos(
			{ tipo: "enviado", texto: "hola" },
			{
				tipo: "evento",
				evento: "message.complete",
				payload: { text: "Usage limit reached", status: "error" },
			},
			{ tipo: "pausado", pausa: PAUSA },
		);
		expect(c.turno).toBe("pausado");
		expect(c.pausa).toEqual(PAUSA);
		expect(c.actividad).toBeNull();
	});

	it("reanudar o reenviar quita la pausa y espera la respuesta; cancelar libera el turno", () => {
		const pausado = pasos(
			{ tipo: "enviado", texto: "hola" },
			{ tipo: "pausado", pausa: PAUSA },
		);
		expect(reducir(pausado, { tipo: "reanudando" })).toMatchObject({
			turno: "esperando",
			pausa: null,
		});
		expect(reducir(pausado, { tipo: "cancelada" })).toMatchObject({
			turno: "libre",
			pausa: null,
		});
	});

	it("si otra pestania ya resolvio la pausa, se informa y el turno queda libre", () => {
		const c = reducir(
			pasos(
				{ tipo: "enviado", texto: "hola" },
				{
					tipo: "evento",
					evento: "message.complete",
					payload: { text: "Usage limit", status: "error" },
				},
				{ tipo: "pausado", pausa: PAUSA },
			),
			{
				tipo: "fallo",
				mensaje: "ya se resolvió",
			},
		);
		expect(c).toMatchObject({ turno: "libre", pausa: null });
		expect(c.error?.mensaje).toBe("ya se resolvió");
	});

	it("retomar una sesion guardada carga su historial visible", () => {
		const c = reducir(inicial, {
			tipo: "historial",
			mensajes: [
				{ role: "user", text: "hola" },
				{ role: "tool", text: "{}" },
				{ role: "assistant", text: "" },
				{ role: "assistant", text: "Usage limit reached" },
			],
		});
		expect(c.mensajes).toEqual([
			{ id: 1, rol: "usuario", texto: "hola", estado: "listo" },
			{ id: 2, rol: "agente", texto: "Usage limit reached", estado: "listo" },
		]);
	});
});

describe("principal de la sesion", () => {
	it("session.create perezoso no trae proveedor; session.info si (marcos reales del gateway)", () => {
		expect(
			principalDe({ model: "modelo-que-no-existe", lazy: true }),
		).toBeNull();
		expect(
			principalDe({
				model: "modelo-que-no-existe",
				provider: "antigravity-subscription-directsdk",
			}),
		).toEqual({
			proveedor: "antigravity-subscription-directsdk",
			modelo: "modelo-que-no-existe",
		});
		expect(principalDe(null)).toBeNull();
		expect(principalDe({ provider: "" })).toBeNull();
	});
});
