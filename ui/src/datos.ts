// Datos de la pantalla: estado de las rutas (backend del plugin), conexion con el gateway y conversacion.
import {
	useCallback,
	useEffect,
	useReducer,
	useRef,
	useState,
	useSyncExternalStore,
} from "react";
import {
	type Chat,
	inicial,
	type Pausa,
	type Principal,
	principalDe,
	reducir,
} from "./chat";
import type { EstadoRutas, RutaEstado } from "./estado";
import { type EstadoConexion, Gateway } from "./gateway";
import { sdk } from "./sdk";

const RUTA_ESTADO = "/api/plugins/orquehelx/estado";

/** Una sola conexion por carga de la pagina; la cabecera y el chat la comparten. */
const gateway = new Gateway(() => sdk().buildWsUrl("/api/ws"));

export interface EstadoCarga {
	datos: EstadoRutas | null;
	cargando: boolean;
	error: string | null;
	refrescar: () => void;
}

/** Rutas y cuota medida. Mide al pedirlo: sin temporizadores (D21). */
export function useEstado(): EstadoCarga {
	const [datos, setDatos] = useState<EstadoRutas | null>(null);
	const [cargando, setCargando] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [pedido, setPedido] = useState(0);

	useEffect(() => {
		let vigente = true;
		setCargando(true);
		sdk()
			.fetchJSON<EstadoRutas>(RUTA_ESTADO)
			.then((d) => {
				if (!vigente) return;
				setDatos(d);
				setError(null);
			})
			.catch((e: unknown) => {
				if (vigente) setError(e instanceof Error ? e.message : String(e));
			})
			.finally(() => {
				if (vigente) setCargando(false);
			});
		return () => {
			vigente = false;
		};
	}, [pedido]);

	const refrescar = useCallback(() => setPedido((n) => n + 1), []);
	return { datos, cargando, error, refrescar };
}

/** Estado de la conexion con /api/ws. "conectado" solo tras gateway.ready. */
export function useConexion(): {
	estado: EstadoConexion;
	reintentar: () => void;
} {
	const estado = useSyncExternalStore(gateway.alEstado, gateway.estado);
	useEffect(() => {
		void gateway.conectar();
	}, []);
	const reintentar = useCallback(() => gateway.reconectar(), []);
	return { estado, reintentar };
}

export interface ChatVivo {
	chat: Chat;
	enviar: (texto: string) => void;
	detener: () => void;
	nueva: () => void;
	reanudar: () => void;
	reenviar: (ruta: RutaEstado) => void;
	cancelar: () => void;
}

const RUTA_PAUSAS = "/api/plugins/orquehelx/pausas";

interface SesionHermes {
	session_id: string;
	stored_session_id?: string | null;
	messages?: { role?: unknown; text?: unknown }[];
	info?: Record<string, unknown>;
}

const post = <T>(url: string, cuerpo: unknown) =>
	sdk().fetchJSON<T>(url, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(cuerpo),
	});

/** Resuelve cuando el gateway manda gateway.ready (useConexion abre el socket). */
const conectado = () =>
	new Promise<void>((listo) => {
		if (gateway.estado() === "conectado") return listo();
		const quitar = gateway.alEstado(() => {
			if (gateway.estado() !== "conectado") return;
			quitar();
			listo();
		});
	});

// ponytail: setTimeout acepta hasta ~24,8 dias; una ventana semanal entra de sobra.
const MAX_ESPERA = 2 ** 31 - 1;
/** Margen tras la hora de reinicio: el proveedor no siempre libera la cuota en el segundo exacto. */
const MARGEN_REINICIO = 30_000;

/**
 * La conversacion con el agente principal. La sesion de Hermes se crea con el primer mensaje.
 *
 * Pausa por cuota (D2, D16, D19): un message.complete con error consulta /pausas; solo hay pausa si el
 * observador de cuota registro un agotamiento vigente del proveedor. Salir de la pausa primero la
 * reclama en el backend (atomico: otra pestania recibe 409) y despues reintenta en Hermes con
 * command.dispatch retry, que rebobina el ultimo turno sin duplicar el mensaje en el historial.
 */
export function useChat(): ChatVivo {
	const [chat, despachar] = useReducer(reducir, inicial);
	// La sesion como promesa compartida: enviar y detener esperan la misma creacion, y detener pedido
	// mientras se crea se manda despues del prompt (el socket conserva el orden).
	const sesion = useRef<Promise<string> | null>(null);
	const idSesion = useRef<string | null>(null);
	// Lo que necesita una pausa: la sesion guardada (sobrevive recargas) y el proveedor del principal,
	// que en una sesion perezosa llega recien con session.info.
	const guardada = useRef<string | null>(null);
	const principal = useRef<Principal | null>(null);

	const fallo = useCallback(
		(e: unknown) =>
			despachar({
				tipo: "fallo",
				mensaje: e instanceof Error ? e.message : String(e),
			}),
		[],
	);

	const pausarSiEsCuota = useCallback(() => {
		const sesionGuardada = guardada.current;
		const p = principal.current;
		if (!sesionGuardada || !p) return;
		post<{ pausa: Pausa | null }>(RUTA_PAUSAS, {
			sesion: sesionGuardada,
			proveedor: p.proveedor,
			modelo: p.modelo,
		})
			.then((r) => {
				if (r.pausa) despachar({ tipo: "pausado", pausa: r.pausa });
			})
			.catch((e: unknown) =>
				fallo(
					`no se pudo registrar la pausa por cuota: ${e instanceof Error ? e.message : String(e)}`,
				),
			);
	}, [fallo]);

	useEffect(
		() =>
			gateway.alEvento((tipo, sid, payload) => {
				if (!sid || sid !== idSesion.current) return;
				if (tipo === "session.info")
					principal.current =
						principalDe(payload as Record<string, unknown> | null) ??
						principal.current;
				despachar({ tipo: "evento", evento: tipo, payload });
				if (
					tipo === "message.complete" &&
					(payload as { status?: unknown } | null)?.status === "error"
				)
					pausarSiEsCuota();
			}),
		[pausarSiEsCuota],
	);

	/** Engancha el chat a una sesion guardada (pausa de una carga anterior) y muestra su historial. */
	const retomar = useCallback(async (idGuardado: string) => {
		const r = await gateway.rpc<SesionHermes>("session.resume", {
			session_id: idGuardado,
			lazy: true,
		});
		idSesion.current = r.session_id;
		sesion.current = Promise.resolve(r.session_id);
		guardada.current = idGuardado;
		principal.current = principalDe(r.info);
		despachar({ tipo: "historial", mensajes: r.messages ?? [] });
	}, []);

	// Al abrir la pestania: una pausa pendiente de antes (recarga, PC apagado) vuelve a la vista.
	useEffect(() => {
		let vigente = true;
		sdk()
			.fetchJSON<{ pausas: Pausa[] }>(RUTA_PAUSAS)
			.then(async ({ pausas }) => {
				const ultima = pausas.at(-1);
				if (!vigente || !ultima) return;
				await conectado();
				await retomar(ultima.sesion).catch((e: unknown) =>
					fallo(
						`no se pudo retomar la sesión en pausa: ${e instanceof Error ? e.message : String(e)}`,
					),
				);
				if (vigente) despachar({ tipo: "pausado", pausa: ultima });
			})
			.catch(fallo);
		return () => {
			vigente = false;
		};
	}, [retomar, fallo]);

	const enviar = useCallback(
		(texto: string) => {
			const limpio = texto.trim();
			if (!limpio) return;
			despachar({ tipo: "enviado", texto: limpio });
			if (!sesion.current) {
				const creando = gateway
					.rpc<SesionHermes>("session.create", {})
					.then((r) => {
						idSesion.current = r.session_id;
						guardada.current = r.stored_session_id ?? r.session_id;
						principal.current = principalDe(r.info);
						return r.session_id;
					});
				sesion.current = creando;
				// Si la creacion falla, el reintento crea otra.
				creando.catch(() => {
					if (sesion.current === creando) sesion.current = null;
				});
			}
			sesion.current
				.then((id) =>
					gateway.rpc("prompt.submit", { session_id: id, text: limpio }),
				)
				.catch(fallo);
		},
		[fallo],
	);

	const detener = useCallback(() => {
		sesion.current
			?.then((id) => gateway.rpc("session.interrupt", { session_id: id }))
			.catch(fallo);
	}, [fallo]);

	const nueva = useCallback(() => {
		sesion.current = null;
		idSesion.current = null;
		guardada.current = null;
		principal.current = null;
		despachar({ tipo: "nueva" });
	}, []);

	/** Reclama la pausa; false si otra pestania (o el reinicio) ya la resolvio. */
	const reclamar = useCallback(
		async (
			pausa: Pausa,
			accion: "reanudar" | "reenviar" | "cancelar",
			destino?: string,
		) => {
			try {
				await post(`${RUTA_PAUSAS}/${pausa.id}/${accion}`, {
					destino: destino ?? null,
				});
				return true;
			} catch (e) {
				if ((e as { status?: number }).status === 409) {
					fallo("Esta pausa ya se resolvió en otra pestaña.");
					return false;
				}
				throw e;
			}
		},
		[fallo],
	);

	/** El ultimo turno otra vez, con el modelo actual de la sesion. */
	const reintentar = useCallback(async () => {
		const id = await (sesion.current ??
			Promise.reject(new Error("la pausa no tiene sesión")));
		const r = await gateway.rpc<{ type?: string; message?: string }>(
			"command.dispatch",
			{
				session_id: id,
				name: "retry",
			},
		);
		if (r.type !== "send" || !r.message)
			throw new Error("Hermes no devolvió el turno a reintentar");
		await gateway.rpc("prompt.submit", { session_id: id, text: r.message });
	}, []);

	const salir = useCallback(
		(pasos: () => Promise<void>) => {
			pasos().catch((e: unknown) =>
				fallo(
					`la pausa se resolvió, pero Hermes no aceptó el reintento: ${e instanceof Error ? e.message : String(e)}. Escribe de nuevo para continuar.`,
				),
			);
		},
		[fallo],
	);

	const pausa = chat.pausa;

	const reanudar = useCallback(() => {
		if (!pausa) return;
		reclamar(pausa, "reanudar")
			.then((gano) => {
				if (!gano) return;
				despachar({ tipo: "reanudando" });
				salir(reintentar);
			})
			.catch(fallo);
	}, [pausa, reclamar, reintentar, salir, fallo]);

	const reenviar = useCallback(
		(ruta: RutaEstado) => {
			if (!pausa || !ruta.modelo) return;
			const modelo = ruta.modelo;
			reclamar(pausa, "reenviar", ruta.nombre)
				.then((gano) => {
					if (!gano) return;
					despachar({ tipo: "reanudando" });
					salir(async () => {
						const id = await (sesion.current ??
							Promise.reject(new Error("la pausa no tiene sesión")));
						// --session: el cambio vale para esta conversacion, no toca el modelo por defecto de Hermes.
						await gateway.rpc("config.set", {
							session_id: id,
							key: "model",
							value: `${modelo} --provider ${ruta.proveedor} --session`,
						});
						principal.current = { proveedor: ruta.proveedor, modelo };
						await reintentar();
					});
				})
				.catch(fallo);
		},
		[pausa, reclamar, reintentar, salir, fallo],
	);

	const cancelar = useCallback(() => {
		if (!pausa) return;
		reclamar(pausa, "cancelar")
			.then((gano) => {
				if (gano) despachar({ tipo: "cancelada" });
			})
			.catch(fallo);
	}, [pausa, reclamar, fallo]);

	// D2: con hora de reinicio medida, se reanuda solo en el mismo proveedor. Sin conexion, espera al boton.
	useEffect(() => {
		if (!pausa?.reinicio) return;
		const espera = Date.parse(pausa.reinicio) - Date.now() + MARGEN_REINICIO;
		if (Number.isNaN(espera)) return;
		const t = setTimeout(
			() => {
				if (gateway.estado() === "conectado") reanudar();
			},
			Math.min(Math.max(espera, 0), MAX_ESPERA),
		);
		return () => clearTimeout(t);
	}, [pausa, reanudar]);

	return { chat, enviar, detener, nueva, reanudar, reenviar, cancelar };
}
