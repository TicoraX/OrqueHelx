// Datos de la pantalla: estado de las rutas (backend del plugin), conexion con el gateway y conversacion.
import {
	useCallback,
	useEffect,
	useReducer,
	useRef,
	useState,
	useSyncExternalStore,
} from "react";
import { type Chat, inicial, reducir } from "./chat";
import type { EstadoRutas } from "./estado";
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
}

/** La conversacion con el agente principal. La sesion de Hermes se crea con el primer mensaje. */
export function useChat(): ChatVivo {
	const [chat, despachar] = useReducer(reducir, inicial);
	const sesion = useRef<string | null>(null);

	useEffect(
		() =>
			gateway.alEvento((tipo, sid, payload) => {
				if (sid && sid === sesion.current)
					despachar({ tipo: "evento", evento: tipo, payload });
			}),
		[],
	);

	const enviar = useCallback((texto: string) => {
		const limpio = texto.trim();
		if (!limpio) return;
		despachar({ tipo: "enviado", texto: limpio });
		(async () => {
			if (!sesion.current) {
				const creada = await gateway.rpc<{ session_id: string }>(
					"session.create",
					{},
				);
				sesion.current = creada.session_id;
			}
			await gateway.rpc("prompt.submit", {
				session_id: sesion.current,
				text: limpio,
			});
		})().catch((e: unknown) =>
			despachar({
				tipo: "fallo",
				mensaje: e instanceof Error ? e.message : String(e),
			}),
		);
	}, []);

	const detener = useCallback(() => {
		if (!sesion.current) return;
		gateway
			.rpc("session.interrupt", { session_id: sesion.current })
			.catch((e: unknown) =>
				despachar({
					tipo: "fallo",
					mensaje: e instanceof Error ? e.message : String(e),
				}),
			);
	}, []);

	const nueva = useCallback(() => {
		sesion.current = null;
		despachar({ tipo: "nueva" });
	}, []);

	return { chat, enviar, detener, nueva };
}
