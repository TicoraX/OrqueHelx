import { describe, expect, it } from "vitest";
import { Gateway } from "../src/gateway";

/** WebSocket falso: guarda lo enviado y deja emitir marcos del servidor. */
class SocketFalso {
	static ultimo: SocketFalso;
	static creados = 0;
	falla = false;
	enviados: unknown[] = [];
	onmessage: ((ev: { data: string }) => void) | null = null;
	onclose: (() => void) | null = null;
	constructor(public url: string) {
		SocketFalso.ultimo = this;
		SocketFalso.creados += 1;
	}
	send(dato: string) {
		if (this.falla) throw new Error("InvalidStateError");
		this.enviados.push(JSON.parse(dato));
	}
	/** Como el real: onclose llega despues, no dentro de close(). */
	close() {
		queueMicrotask(() => this.onclose?.());
	}
	servidor(marco: unknown) {
		this.onmessage?.({ data: JSON.stringify(marco) });
	}
}

const nuevo = () =>
	new Gateway(
		async () => "ws://prueba/api/ws?token=t",
		(url) => new SocketFalso(url) as unknown as WebSocket,
	);

describe("gateway", () => {
	it("queda conectado solo con gateway.ready", async () => {
		const g = nuevo();
		await g.conectar();
		expect(g.estado()).toBe("conectando");
		SocketFalso.ultimo.servidor({
			jsonrpc: "2.0",
			method: "event",
			params: { type: "gateway.ready" },
		});
		expect(g.estado()).toBe("conectado");
	});

	it("rpc correlaciona la respuesta por id y rechaza los errores", async () => {
		const g = nuevo();
		await g.conectar();
		const s = SocketFalso.ultimo;
		const creada = g.rpc("session.create", {});
		const fallida = g.rpc("prompt.submit", { text: "x" });
		const [p1, p2] = s.enviados as { id: number; method: string }[];
		expect([p1.method, p2.method]).toEqual(["session.create", "prompt.submit"]);
		s.servidor({
			jsonrpc: "2.0",
			id: p2.id,
			error: { message: "sesion inexistente" },
		});
		s.servidor({ jsonrpc: "2.0", id: p1.id, result: { session_id: "abc" } });
		await expect(creada).resolves.toEqual({ session_id: "abc" });
		await expect(fallida).rejects.toThrow("sesion inexistente");
	});

	it("reparte los eventos a los suscriptores con su sesion", async () => {
		const g = nuevo();
		await g.conectar();
		const vistos: unknown[] = [];
		const desuscribir = g.alEvento((tipo, sesion, payload) =>
			vistos.push([tipo, sesion, payload]),
		);
		SocketFalso.ultimo.servidor({
			jsonrpc: "2.0",
			method: "event",
			params: {
				type: "message.delta",
				session_id: "abc",
				payload: { text: "ho" },
			},
		});
		desuscribir();
		SocketFalso.ultimo.servidor({
			jsonrpc: "2.0",
			method: "event",
			params: { type: "message.delta", session_id: "abc" },
		});
		expect(vistos).toEqual([["message.delta", "abc", { text: "ho" }]]);
	});

	it("al cerrarse rechaza los pedidos pendientes y queda sin conexion", async () => {
		const g = nuevo();
		await g.conectar();
		const pendiente = g.rpc("session.create", {});
		const rechazo = expect(pendiente).rejects.toThrow("conexión");
		SocketFalso.ultimo.close();
		await rechazo;
		expect(g.estado()).toBe("sin_conexion");
	});

	it("rpc sin conexion falla en voz alta", async () => {
		await expect(nuevo().rpc("session.create", {})).rejects.toThrow(
			"sin conexión",
		);
	});

	it("reconectar rechaza los pedidos del socket viejo", async () => {
		const g = nuevo();
		await g.conectar();
		const pendiente = g.rpc("prompt.submit", {});
		const rechazo = expect(pendiente).rejects.toThrow("conexión");
		g.reconectar();
		await rechazo;
	});

	it("dos conectar a la vez abren un solo socket", async () => {
		const g = nuevo();
		const antes = SocketFalso.creados;
		await Promise.all([g.conectar(), g.conectar()]);
		expect(SocketFalso.creados - antes).toBe(1);
	});

	it("si send falla, rpc rechaza y no deja el pedido colgado", async () => {
		const g = nuevo();
		await g.conectar();
		SocketFalso.ultimo.falla = true;
		await expect(g.rpc("session.create", {})).rejects.toThrow(
			"InvalidStateError",
		);
		SocketFalso.ultimo.falla = false;
		const r = g.rpc("session.create", {});
		const id = (SocketFalso.ultimo.enviados.at(-1) as { id: number }).id;
		SocketFalso.ultimo.servidor({ jsonrpc: "2.0", id, result: 1 });
		await expect(r).resolves.toBe(1);
	});
});
