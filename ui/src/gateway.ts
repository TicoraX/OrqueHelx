// One connection with Hermes' gateway (/api/ws): JSON-RPC 2.0 per line, requests matched by id and events
// {"method": "event", "params": {type, session_id, payload}}.
import { t } from "./texts";

export type ConnectionState = "connecting" | "connected" | "offline";
type EventListener = (type: string, session: string, payload: unknown) => void;

interface Pending {
	resolve: (result: unknown) => void;
	reject: (error: Error) => void;
}

export class Gateway {
	private socket: WebSocket | null = null;
	private nextId = 0;
	private pending = new Map<number, Pending>();
	private eventListeners = new Set<EventListener>();
	private stateListeners = new Set<() => void>();
	private currentState: ConnectionState = "offline";
	/** Only the latest connect() in flight opens a socket: an older one coming back from await is dropped. */
	private attempt = 0;

	constructor(
		private gatewayUrl: () => Promise<string>,
		private open: (url: string) => WebSocket = (url) => new WebSocket(url),
	) {}

	state = (): ConnectionState => this.currentState;

	onState = (listener: () => void): (() => void) => {
		this.stateListeners.add(listener);
		return () => this.stateListeners.delete(listener);
	};

	onEvent(listener: EventListener): () => void {
		this.eventListeners.add(listener);
		return () => this.eventListeners.delete(listener);
	}

	private change(state: ConnectionState): void {
		if (state === this.currentState) return;
		this.currentState = state;
		for (const l of this.stateListeners) l();
	}

	async connect(): Promise<void> {
		if (this.socket) return;
		this.attempt += 1;
		const attempt = this.attempt;
		this.change("connecting");
		let url: string;
		try {
			url = await this.gatewayUrl();
		} catch (e) {
			console.warn("orquehelx: could not build the gateway URL", e);
			if (attempt === this.attempt) this.change("offline");
			return;
		}
		if (attempt !== this.attempt) return;
		const socket = this.open(url);
		this.socket = socket;
		socket.onmessage = (ev) => this.receive(String(ev.data));
		socket.onclose = () => {
			if (this.socket === socket) this.release();
		};
	}

	/** Forget the current socket and reject its requests: their answers will never arrive. */
	private release(): void {
		this.socket = null;
		for (const p of this.pending.values())
			p.reject(new Error(t("connection_lost")));
		this.pending.clear();
		this.change("offline");
	}

	reconnect(): void {
		this.socket?.close();
		this.release();
		void this.connect();
	}

	private receive(text: string): void {
		let frame: {
			id?: number;
			method?: string;
			params?: Record<string, unknown>;
			result?: unknown;
			error?: { message?: string };
		};
		try {
			frame = JSON.parse(text);
		} catch (e) {
			console.warn("orquehelx: the gateway sent a frame that is not JSON", e);
			return;
		}
		if (typeof frame.id === "number" && this.pending.has(frame.id)) {
			const p = this.pending.get(frame.id) as Pending;
			this.pending.delete(frame.id);
			if (frame.error)
				p.reject(new Error(frame.error.message ?? t("gateway_error")));
			else p.resolve(frame.result);
			return;
		}
		if (frame.method !== "event" || !frame.params) return;
		const type = String(frame.params.type ?? "");
		if (type === "gateway.ready") this.change("connected");
		for (const l of this.eventListeners)
			l(
				type,
				String(frame.params.session_id ?? ""),
				frame.params.payload ?? null,
			);
	}

	rpc<T = unknown>(
		method: string,
		params: Record<string, unknown>,
	): Promise<T> {
		const socket = this.socket;
		if (!socket) return Promise.reject(new Error(t("gateway_offline")));
		this.nextId += 1;
		const id = this.nextId;
		return new Promise<T>((resolve, reject) => {
			this.pending.set(id, {
				resolve: resolve as (r: unknown) => void,
				reject,
			});
			try {
				socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
			} catch (e) {
				this.pending.delete(id);
				throw e;
			}
		});
	}
}
