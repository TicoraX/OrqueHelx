// Estado de la conversacion: eventos del gateway de Hermes -> mensajes, turno y actividad visible.
// Reductor puro: lo mismo entra, lo mismo sale; los efectos (sesion, envio) viven en useChat.
//
//   enviado -> turno "esperando" -> message.start -> "respondiendo" -> message.delta* -> message.complete -> "libre"
//   claude-subscription no manda message.delta: el texto llega entero en message.complete.

export type Rol = "usuario" | "agente";
export type EstadoMensaje = "listo" | "escribiendo" | "interrumpido" | "error";

export interface Mensaje {
	id: number;
	rol: Rol;
	texto: string;
	estado: EstadoMensaje;
}

export interface Chat {
	mensajes: Mensaje[];
	turno: "libre" | "esperando" | "respondiendo";
	/** Lo que el agente hace ahora ("pensando", "delegando en opencode"); null si nada. */
	actividad: string | null;
	/** Fallo al enviar: el mensaje y el texto, para reintentar sin volver a escribirlo. */
	error: { mensaje: string; texto: string } | null;
}

export type Accion =
	| { tipo: "enviado"; texto: string }
	| { tipo: "evento"; evento: string; payload: unknown }
	| { tipo: "fallo"; mensaje: string }
	| { tipo: "nueva" };

export const inicial: Chat = { mensajes: [], turno: "libre", actividad: null, error: null };

type Payload = Record<string, unknown> | null;

const siguienteId = (c: Chat) => (c.mensajes.at(-1)?.id ?? 0) + 1;

/** La respuesta en curso: el ultimo mensaje si es del agente y sigue escribiendo; si no, una nueva. */
function conRespuesta(c: Chat, cambiar: (m: Mensaje) => Mensaje): Mensaje[] {
	const ultimo = c.mensajes.at(-1);
	if (ultimo?.rol === "agente" && ultimo.estado === "escribiendo") {
		return [...c.mensajes.slice(0, -1), cambiar(ultimo)];
	}
	return [...c.mensajes, cambiar({ id: siguienteId(c), rol: "agente", texto: "", estado: "escribiendo" })];
}

function actividadDeHerramienta(p: Payload): string {
	const nombre = String(p?.name ?? "una herramienta");
	const args = (p?.args ?? {}) as Record<string, unknown>;
	return nombre === "delegar" && typeof args.ruta === "string" ? `delegando en ${args.ruta}` : `usando ${nombre}`;
}

function alEvento(c: Chat, evento: string, p: Payload): Chat {
	switch (evento) {
		case "message.start":
			return { ...c, turno: "respondiendo", mensajes: conRespuesta(c, (m) => m) };
		case "message.delta":
			return {
				...c,
				turno: "respondiendo",
				mensajes: conRespuesta(c, (m) => ({ ...m, texto: m.texto + String(p?.text ?? "") })),
			};
		case "thinking.delta":
		case "reasoning.delta":
			// El texto del pensamiento es relleno animado del CLI ("synthesizing..."): solo se muestra que piensa.
			return { ...c, actividad: "pensando" };
		case "tool.start":
			return { ...c, actividad: actividadDeHerramienta(p) };
		case "tool.complete":
			return { ...c, actividad: null };
		case "message.complete": {
			const estado: EstadoMensaje =
				p?.status === "interrupted" ? "interrumpido" : p?.status === "error" ? "error" : "listo";
			const final = typeof p?.text === "string" ? p.text : "";
			return {
				...c,
				turno: "libre",
				actividad: null,
				mensajes: conRespuesta(c, (m) => ({ ...m, texto: final || m.texto, estado })),
			};
		}
		default:
			return c;
	}
}

export function reducir(c: Chat, a: Accion): Chat {
	switch (a.tipo) {
		case "enviado":
			return {
				...c,
				turno: "esperando",
				error: null,
				mensajes: [...c.mensajes, { id: siguienteId(c), rol: "usuario", texto: a.texto, estado: "listo" }],
			};
		case "evento":
			return alEvento(c, a.evento, a.payload as Payload);
		case "fallo": {
			const ultimo = c.mensajes.at(-1);
			const mensajes =
				ultimo?.rol === "usuario" ? [...c.mensajes.slice(0, -1), { ...ultimo, estado: "error" as const }] : c.mensajes;
			return { ...c, turno: "libre", actividad: null, mensajes, error: { mensaje: a.mensaje, texto: ultimo?.texto ?? "" } };
		}
		case "nueva":
			return inicial;
	}
}
