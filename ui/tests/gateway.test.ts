import { describe, expect, it } from "vitest";
import { Gateway } from "../src/gateway";

/** Fake WebSocket: keeps what was sent and lets the server emit frames. */
class FakeSocket {
	static last: FakeSocket;
	static created = 0;
	fails = false;
	sent: unknown[] = [];
	onmessage: ((ev: { data: string }) => void) | null = null;
	onclose: (() => void) | null = null;
	constructor(public url: string) {
		FakeSocket.last = this;
		FakeSocket.created += 1;
	}
	send(data: string) {
		if (this.fails) throw new Error("InvalidStateError");
		this.sent.push(JSON.parse(data));
	}
	/** Like the real one: onclose comes later, not inside close(). */
	close() {
		queueMicrotask(() => this.onclose?.());
	}
	server(frame: unknown) {
		this.onmessage?.({ data: JSON.stringify(frame) });
	}
}

const fresh = () =>
	new Gateway(
		async () => "ws://test/api/ws?token=t",
		(url) => new FakeSocket(url) as unknown as WebSocket,
	);

describe("gateway", () => {
	it("is connected only after gateway.ready", async () => {
		const g = fresh();
		await g.connect();
		expect(g.state()).toBe("connecting");
		FakeSocket.last.server({
			jsonrpc: "2.0",
			method: "event",
			params: { type: "gateway.ready" },
		});
		expect(g.state()).toBe("connected");
	});

	it("rpc matches the answer by id and rejects errors", async () => {
		const g = fresh();
		await g.connect();
		const s = FakeSocket.last;
		const created = g.rpc("session.create", {});
		const failed = g.rpc("prompt.submit", { text: "x" });
		const [p1, p2] = s.sent as { id: number; method: string }[];
		expect([p1.method, p2.method]).toEqual(["session.create", "prompt.submit"]);
		s.server({
			jsonrpc: "2.0",
			id: p2.id,
			error: { message: "no such session" },
		});
		s.server({ jsonrpc: "2.0", id: p1.id, result: { session_id: "abc" } });
		await expect(created).resolves.toEqual({ session_id: "abc" });
		await expect(failed).rejects.toThrow("no such session");
	});

	it("hands events to subscribers with their session", async () => {
		const g = fresh();
		await g.connect();
		const seen: unknown[] = [];
		const unsubscribe = g.onEvent((type, session, payload) =>
			seen.push([type, session, payload]),
		);
		FakeSocket.last.server({
			jsonrpc: "2.0",
			method: "event",
			params: {
				type: "message.delta",
				session_id: "abc",
				payload: { text: "hi" },
			},
		});
		unsubscribe();
		FakeSocket.last.server({
			jsonrpc: "2.0",
			method: "event",
			params: { type: "message.delta", session_id: "abc" },
		});
		expect(seen).toEqual([["message.delta", "abc", { text: "hi" }]]);
	});

	it("on close it rejects pending requests and goes offline", async () => {
		const g = fresh();
		await g.connect();
		const pending = g.rpc("session.create", {});
		const rejection = expect(pending).rejects.toThrow("connection");
		FakeSocket.last.close();
		await rejection;
		expect(g.state()).toBe("offline");
	});

	it("rpc while offline fails loudly", async () => {
		await expect(fresh().rpc("session.create", {})).rejects.toThrow("offline");
	});

	it("reconnect rejects the old socket's requests", async () => {
		const g = fresh();
		await g.connect();
		const pending = g.rpc("prompt.submit", {});
		const rejection = expect(pending).rejects.toThrow("connection");
		g.reconnect();
		await rejection;
	});

	it("two connect calls at once open a single socket", async () => {
		const g = fresh();
		const before = FakeSocket.created;
		await Promise.all([g.connect(), g.connect()]);
		expect(FakeSocket.created - before).toBe(1);
	});

	it("if send fails, rpc rejects and leaves no hanging request", async () => {
		const g = fresh();
		await g.connect();
		FakeSocket.last.fails = true;
		await expect(g.rpc("session.create", {})).rejects.toThrow(
			"InvalidStateError",
		);
		FakeSocket.last.fails = false;
		const r = g.rpc("session.create", {});
		const id = (FakeSocket.last.sent.at(-1) as { id: number }).id;
		FakeSocket.last.server({ jsonrpc: "2.0", id, result: 1 });
		await expect(r).resolves.toBe(1);
	});
});
