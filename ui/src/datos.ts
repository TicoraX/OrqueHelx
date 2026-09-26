// Datos de la pantalla: estado de las rutas (backend del plugin) y conexion con el gateway de Hermes.
import { useCallback, useEffect, useState } from "react";
import type { EstadoRutas } from "./estado";
import { sdk } from "./sdk";

const RUTA_ESTADO = "/api/plugins/orquehelx/estado";

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

export type EstadoConexion = "conectando" | "conectado" | "sin_conexion";

/** Conexion con /api/ws. "conectado" solo tras gateway.ready, no al abrir el socket. */
export function useConexion(): {
	estado: EstadoConexion;
	reintentar: () => void;
} {
	const [estado, setEstado] = useState<EstadoConexion>("conectando");
	const [intento, setIntento] = useState(0);

	useEffect(() => {
		let socket: WebSocket | null = null;
		let vigente = true;
		setEstado("conectando");
		sdk()
			.buildWsUrl("/api/ws")
			.then((url) => {
				if (!vigente) return;
				socket = new WebSocket(url);
				socket.onmessage = (ev) => {
					let tipo: unknown;
					try {
						tipo = JSON.parse(String(ev.data))?.params?.type;
					} catch (e) {
						console.warn(
							"orquehelx: el gateway mando un marco que no es JSON",
							e,
						);
						return;
					}
					if (tipo === "gateway.ready") setEstado("conectado");
				};
				socket.onclose = () => vigente && setEstado("sin_conexion");
			})
			.catch(() => vigente && setEstado("sin_conexion"));
		return () => {
			vigente = false;
			socket?.close();
		};
	}, [intento]);

	const reintentar = useCallback(() => setIntento((n) => n + 1), []);
	return { estado, reintentar };
}
