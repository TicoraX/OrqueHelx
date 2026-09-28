// Estado de la conversacion: eventos del gateway de Hermes -> mensajes, turno y actividad visible.
// Reductor puro: lo mismo entra, lo mismo sale; los efectos (sesion, envio) viven en useChat.
//
//   enviado -> turno "esperando" -> message.start -> "respondiendo" -> message.delta* -> message.complete -> "libre"
//   claude-subscription no manda message.delta: el texto llega entero en message.complete.

import { type Arbol, reducirArbol, vacio } from "./subagentes";

/** "nota": asiento del sistema en el hilo (un reenvio), no lo dice nadie. */
type Rol = "usuario" | "agente" | "nota";
type EstadoMensaje = "listo" | "escribiendo" | "interrumpido" | "error";

export interface Mensaje {
	id: number;
	rol: Rol;
	texto: string;
	estado: EstadoMensaje;
	/** Solo en respuestas del agente: el modelo del principal que la dio (tras un reenvio, cambia). */
	modelo?: string | null;
}

/** Pausa del agente principal por cuota, tal como la devuelve /api/plugins/orquehelx/pausas. */
export interface Pausa {
	id: number;
	/** stored_session_id de Hermes: sobrevive a recargar la pagina. */
	sesion: string;
	proveedor: string;
	modelo: string | null;
	/** ISO del reinicio medido, o null si el proveedor no lo informa. */
	reinicio: string | null;
	estado: string;
	creado: number;
}

export interface Chat {
	mensajes: Mensaje[];
	/** "pausado": sin cuota; el redactor queda quieto hasta reanudar, reenviar o cancelar (D2). */
	turno: "libre" | "esperando" | "respondiendo" | "pausado";
	pausa: Pausa | null;
	/** Lo que el agente hace ahora ("pensando", "delegando en opencode"); null si nada. */
	actividad: string | null;
	/** Fallo: el mensaje y, si fue al enviar, el texto para reintentar sin volver a escribirlo. */
	error: { mensaje: string; texto: string | null } | null;
	subagentes: Arbol;
	/** Quien responde: proveedor y modelo del agente principal, o null si Hermes aun no lo dijo. */
	principal: Principal | null;
}

type Accion =
	| { tipo: "enviado"; texto: string }
	| { tipo: "evento"; evento: string; payload: unknown }
	| { tipo: "fallo"; mensaje: string }
	| { tipo: "pausado"; pausa: Pausa }
	| { tipo: "reanudando" }
	| { tipo: "cancelada" }
	| { tipo: "historial"; mensajes: { role?: unknown; text?: unknown }[] }
	| { tipo: "principal"; principal: Principal | null }
	| { tipo: "nota"; texto: string }
	| { tipo: "nueva" };

export const inicial: Chat = {
	mensajes: [],
	turno: "libre",
	pausa: null,
	actividad: null,
	error: null,
	subagentes: vacio,
	principal: null,
};

type Payload = Record<string, unknown> | null;

const siguienteId = (c: Chat) => (c.mensajes.at(-1)?.id ?? 0) + 1;

/** La respuesta en curso: el ultimo mensaje si es del agente y sigue escribiendo; si no, una nueva. */
function conRespuesta(c: Chat, cambiar: (m: Mensaje) => Mensaje): Mensaje[] {
	const ultimo = c.mensajes.at(-1);
	if (ultimo?.rol === "agente" && ultimo.estado === "escribiendo") {
		return [...c.mensajes.slice(0, -1), cambiar(ultimo)];
	}
	return [
		...c.mensajes,
		cambiar({
			id: siguienteId(c),
			rol: "agente",
			texto: "",
			estado: "escribiendo",
			modelo: c.principal?.modelo ?? null,
		}),
	];
}

function actividadDeHerramienta(p: Payload): string {
	const nombre = String(p?.name ?? "una herramienta");
	const args = (p?.args ?? {}) as Record<string, unknown>;
	return nombre === "delegar" && typeof args.ruta === "string"
		? `delegando en ${args.ruta}`
		: `usando ${nombre}`;
}

const FINAL: Record<string, EstadoMensaje> = {
	interrupted: "interrumpido",
	error: "error",
};

function alEvento(c: Chat, evento: string, p: Payload): Chat {
	switch (evento) {
		case "message.start":
			return {
				...c,
				turno: "respondiendo",
				mensajes: conRespuesta(c, (m) => m),
			};
		case "message.delta":
			return {
				...c,
				turno: "respondiendo",
				mensajes: conRespuesta(c, (m) => ({
					...m,
					texto: m.texto + String(p?.text ?? ""),
				})),
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
			const estado = FINAL[String(p?.status)] ?? "listo";
			const final = typeof p?.text === "string" ? p.text : "";
			return {
				...c,
				turno: "libre",
				actividad: null,
				mensajes: conRespuesta(c, (m) => ({
					...m,
					texto: final || m.texto,
					estado,
				})),
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
				mensajes: [
					...c.mensajes,
					{
						id: siguienteId(c),
						rol: "usuario",
						texto: a.texto,
						estado: "listo",
					},
				],
			};
		case "evento": {
			const arbol = reducirArbol(c.subagentes, a.evento, a.payload);
			const sigue = alEvento(c, a.evento, a.payload as Payload);
			return arbol === c.subagentes ? sigue : { ...sigue, subagentes: arbol };
		}
		case "fallo": {
			// Solo un envio fallido deja el ultimo mensaje del usuario; si ya habia respuesta (fallo al detener
			// o conexion caida), no hay nada que reenviar.
			const ultimo = c.mensajes.at(-1);
			if (ultimo?.rol !== "usuario") {
				return {
					...c,
					turno: "libre",
					pausa: null,
					actividad: null,
					error: { mensaje: a.mensaje, texto: null },
				};
			}
			return {
				...c,
				turno: "libre",
				pausa: null,
				actividad: null,
				mensajes: [...c.mensajes.slice(0, -1), { ...ultimo, estado: "error" }],
				error: { mensaje: a.mensaje, texto: ultimo.texto },
			};
		}
		case "pausado": {
			// La respuesta que precede a la pausa es el turno que fallo por cuota, aunque llegue del historial.
			const ultimo = c.mensajes.at(-1);
			const mensajes =
				ultimo?.rol === "agente"
					? [
							...c.mensajes.slice(0, -1),
							{ ...ultimo, estado: "error" as const },
						]
					: c.mensajes;
			return {
				...c,
				turno: "pausado",
				pausa: a.pausa,
				actividad: null,
				mensajes,
			};
		}
		case "principal":
			return { ...c, principal: a.principal };
		case "nota":
			return {
				...c,
				mensajes: [
					...c.mensajes,
					{ id: siguienteId(c), rol: "nota", texto: a.texto, estado: "listo" },
				],
			};
		case "reanudando":
			return { ...c, turno: "esperando", pausa: null, error: null };
		case "cancelada":
			return { ...c, turno: "libre", pausa: null };
		case "historial":
			return {
				...inicial,
				principal: c.principal,
				mensajes: desdeHistorial(a.mensajes),
			};
		case "nueva":
			return inicial;
	}
}

const ROL: Record<string, Rol> = { user: "usuario", assistant: "agente" };

/** Transcript de Hermes (session.resume) -> mensajes visibles: solo usuario y agente con texto. */
function desdeHistorial(
	filas: { role?: unknown; text?: unknown }[],
): Mensaje[] {
	const mensajes: Mensaje[] = [];
	for (const f of filas) {
		const rol = ROL[String(f.role)];
		if (rol && typeof f.text === "string" && f.text) {
			mensajes.push({
				id: mensajes.length + 1,
				rol,
				texto: f.text,
				estado: "listo",
			});
		}
	}
	return mensajes;
}

/** Proveedor y modelo del agente principal de la sesion. */
export interface Principal {
	proveedor: string;
	modelo: string | null;
}

/** session.create perezoso (lazy) no trae provider hasta armar el agente; llega en session.info. */
export function principalDe(
	info: Record<string, unknown> | null | undefined,
): Principal | null {
	if (typeof info?.provider !== "string" || !info.provider) return null;
	return {
		proveedor: info.provider,
		modelo: typeof info.model === "string" ? info.model : null,
	};
}
