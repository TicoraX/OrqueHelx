// Una conexion con el gateway de Hermes (/api/ws): JSON-RPC 2.0 por linea, pedidos correlacionados por id
// y eventos {"method": "event", "params": {type, session_id, payload}}. Ver diseno/PROTOCOLO-WS.md.

export type EstadoConexion = "conectando" | "conectado" | "sin_conexion";
type OyenteEvento = (tipo: string, sesion: string, payload: unknown) => void;

interface Pendiente {
	resolver: (resultado: unknown) => void;
	rechazar: (error: Error) => void;
}

export class Gateway {
	private socket: WebSocket | null = null;
	private siguienteId = 0;
	private pendientes = new Map<number, Pendiente>();
	private oyentesEvento = new Set<OyenteEvento>();
	private oyentesEstado = new Set<() => void>();
	private estadoActual: EstadoConexion = "sin_conexion";

	constructor(
		private urlDelGateway: () => Promise<string>,
		private abrir: (url: string) => WebSocket = (url) => new WebSocket(url),
	) {}

	estado = (): EstadoConexion => this.estadoActual;

	alEstado = (oyente: () => void): (() => void) => {
		this.oyentesEstado.add(oyente);
		return () => this.oyentesEstado.delete(oyente);
	};

	alEvento(oyente: OyenteEvento): () => void {
		this.oyentesEvento.add(oyente);
		return () => this.oyentesEvento.delete(oyente);
	}

	private cambiar(estado: EstadoConexion): void {
		if (estado === this.estadoActual) return;
		this.estadoActual = estado;
		for (const o of this.oyentesEstado) o();
	}

	async conectar(): Promise<void> {
		if (this.socket) return;
		this.cambiar("conectando");
		let url: string;
		try {
			url = await this.urlDelGateway();
		} catch (e) {
			console.warn("orquehelx: no se pudo armar la URL del gateway", e);
			this.cambiar("sin_conexion");
			return;
		}
		const socket = this.abrir(url);
		this.socket = socket;
		socket.onmessage = (ev) => this.recibir(String(ev.data));
		socket.onclose = () => {
			if (this.socket !== socket) return;
			this.socket = null;
			for (const p of this.pendientes.values())
				p.rechazar(new Error("se perdió la conexión con el gateway"));
			this.pendientes.clear();
			this.cambiar("sin_conexion");
		};
	}

	reconectar(): void {
		this.socket?.close();
		this.socket = null;
		void this.conectar();
	}

	private recibir(texto: string): void {
		let marco: {
			id?: number;
			method?: string;
			params?: Record<string, unknown>;
			result?: unknown;
			error?: { message?: string };
		};
		try {
			marco = JSON.parse(texto);
		} catch (e) {
			console.warn("orquehelx: el gateway mando un marco que no es JSON", e);
			return;
		}
		if (typeof marco.id === "number" && this.pendientes.has(marco.id)) {
			const p = this.pendientes.get(marco.id) as Pendiente;
			this.pendientes.delete(marco.id);
			if (marco.error)
				p.rechazar(
					new Error(marco.error.message ?? "el gateway respondió con error"),
				);
			else p.resolver(marco.result);
			return;
		}
		if (marco.method !== "event" || !marco.params) return;
		const tipo = String(marco.params.type ?? "");
		if (tipo === "gateway.ready") this.cambiar("conectado");
		for (const o of this.oyentesEvento)
			o(
				tipo,
				String(marco.params.session_id ?? ""),
				marco.params.payload ?? null,
			);
	}

	rpc<T = unknown>(
		metodo: string,
		params: Record<string, unknown>,
	): Promise<T> {
		const socket = this.socket;
		if (!socket) return Promise.reject(new Error("gateway sin conexión"));
		this.siguienteId += 1;
		const id = this.siguienteId;
		return new Promise<T>((resolver, rechazar) => {
			this.pendientes.set(id, {
				resolver: resolver as (r: unknown) => void,
				rechazar,
			});
			socket.send(
				JSON.stringify({ jsonrpc: "2.0", id, method: metodo, params }),
			);
		});
	}
}
