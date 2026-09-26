// Pantalla principal: rutas y cuotas | conversacion | subagentes (contrato de direccion, forma "tres columnas").
import React, { useEffect, useRef, useState } from "react";
import type { Mensaje } from "./chat";
import {
	type ChatVivo,
	type EstadoCarga,
	useChat,
	useConexion,
	useEstado,
} from "./datos";
import { type RutaEstado, resumen } from "./estado";
import type { EstadoConexion } from "./gateway";

const TEXTO_CONEXION: Record<EstadoConexion, string> = {
	conectando: "conectando",
	conectado: "conectado",
	sin_conexion: "sin conexión",
};

function IconoRefrescar() {
	return (
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M21 12a9 9 0 1 1-2.64-6.36" />
			<path d="M21 3v6h-6" />
		</svg>
	);
}

function Cabecera({
	conexion,
	reintentar,
}: {
	conexion: EstadoConexion;
	reintentar: () => void;
}) {
	return (
		<header className="ohx-cabecera">
			<span className="ohx-marca">OrqueHelx</span>
			<div className="ohx-conexion" data-estado={conexion} role="status">
				<span>
					gateway <b>{TEXTO_CONEXION[conexion]}</b>
				</span>
				{conexion === "sin_conexion" ? (
					<button type="button" className="btn" onClick={reintentar}>
						Reintentar
					</button>
				) : null}
			</div>
			<nav>
				<a href="/">Volver a Hermes</a>
			</nav>
		</header>
	);
}

function Casilla({ ruta }: { ruta: RutaEstado }) {
	const r = resumen(ruta);
	return (
		<li className="casilla" data-tono={r.tono}>
			<span className="casilla-k">{ruta.nombre}</span>
			<span className="casilla-m">{ruta.modelo ?? ruta.proveedor}</span>
			<p className="casilla-v">{r.cifra}</p>
			<ul className="casilla-n">
				{r.lineas.map((l) => (
					<li key={l}>{l}</li>
				))}
			</ul>
		</li>
	);
}

function Rutas({ estado }: { estado: EstadoCarga }) {
	const { datos, cargando, error, refrescar } = estado;
	let cuerpo: React.ReactNode;
	if (error) {
		cuerpo = (
			<div className="aviso" data-tono="error" role="alert">
				<p>No se pudo leer el estado de las rutas: {error}</p>
				<button type="button" className="btn" onClick={refrescar}>
					Reintentar
				</button>
			</div>
		);
	} else if (!datos) {
		cuerpo = <p className="vacio">Midiendo cuotas…</p>;
	} else if (datos.error) {
		cuerpo = (
			<div className="aviso" data-tono="error" role="alert">
				<p>{datos.error}</p>
			</div>
		);
	} else if (datos.rutas.length === 0) {
		cuerpo = (
			<p className="vacio">
				No hay rutas. Agrégalas en{" "}
				<code>plugins.entries.orquehelx.settings.rutas</code> del config.yaml de
				Hermes.
			</p>
		);
	} else {
		cuerpo = (
			<ul className="casillas" aria-busy={cargando}>
				{datos.rutas.map((r) => (
					<Casilla key={r.nombre} ruta={r} />
				))}
			</ul>
		);
	}
	return (
		<aside className="ohx-columna ohx-riel" aria-labelledby="ohx-rutas">
			<div className="ohx-rotulo">
				<h2 id="ohx-rutas">Rutas</h2>
				<button
					type="button"
					className="btn btn-icono"
					onClick={refrescar}
					disabled={cargando}
					aria-busy={cargando}
					title="Medir de nuevo"
				>
					<IconoRefrescar />
					<span className="sr">Medir de nuevo las cuotas</span>
				</button>
			</div>
			{cuerpo}
		</aside>
	);
}

const ROTULO: Record<Mensaje["rol"], string> = {
	usuario: "tú",
	agente: "agente",
};
const NOTA: Partial<Record<Mensaje["estado"], string>> = {
	escribiendo: "escribiendo",
	interrumpido: "detenido",
	error: "sin respuesta",
};

function Entrada({ m }: { m: Mensaje }) {
	const nota =
		m.rol === "usuario" && m.estado === "error" ? "no enviado" : NOTA[m.estado];
	return (
		<article className="ohx-mensaje" data-rol={m.rol} data-estado={m.estado}>
			<header className="ohx-mensaje-k">
				{ROTULO[m.rol]}
				{nota ? <span> · {nota}</span> : null}
			</header>
			{m.texto ? <p className="ohx-mensaje-v">{m.texto}</p> : null}
		</article>
	);
}

function Redactor({ vivo, conectado }: { vivo: ChatVivo; conectado: boolean }) {
	const [texto, setTexto] = useState("");
	const ocupado = vivo.chat.turno !== "libre";
	const enviar = () => {
		if (!texto.trim() || ocupado || !conectado) return;
		vivo.enviar(texto);
		setTexto("");
	};
	return (
		<form
			className="ohx-redactor"
			onSubmit={(e) => {
				e.preventDefault();
				enviar();
			}}
		>
			<label className="sr" htmlFor="ohx-mensaje">
				Mensaje para el agente
			</label>
			<textarea
				id="ohx-mensaje"
				rows={3}
				value={texto}
				placeholder={
					conectado
						? "Escribe al agente. Enter envía, Shift+Enter hace un salto de línea."
						: "Sin conexión con Hermes."
				}
				disabled={!conectado}
				onChange={(e) => setTexto(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
						e.preventDefault();
						enviar();
					}
				}}
			/>
			<div className="ohx-redactor-acciones">
				<span className="ohx-actividad" role="status">
					{vivo.chat.actividad ?? (ocupado ? "esperando respuesta" : "")}
				</span>
				{ocupado ? (
					<button type="button" className="btn" onClick={vivo.detener}>
						Detener
					</button>
				) : (
					<button
						type="submit"
						className="btn btn-accion"
						disabled={!texto.trim() || !conectado}
					>
						Enviar
					</button>
				)}
			</div>
		</form>
	);
}

function Conversacion({ conectado }: { conectado: boolean }) {
	const vivo = useChat();
	const { mensajes, error } = vivo.chat;
	const fin = useRef<HTMLDivElement>(null);
	const ultimo = mensajes.at(-1);
	// Sigue la respuesta mientras llega; el texto de la ultima es la dependencia que cambia con cada delta.
	useEffect(() => {
		fin.current?.scrollIntoView({ block: "end" });
	}, [mensajes.length, ultimo?.texto]);
	return (
		<main className="ohx-columna ohx-chat">
			<div className="ohx-rotulo">
				<h2>Conversación</h2>
				{mensajes.length > 0 ? (
					<button
						type="button"
						className="btn"
						onClick={vivo.nueva}
						disabled={vivo.chat.turno !== "libre"}
					>
						Nueva conversación
					</button>
				) : null}
			</div>
			<div
				className="ohx-hilo"
				role="log"
				aria-live="polite"
				aria-relevant="additions text"
			>
				<div className="ohx-lectura">
					{mensajes.length === 0 ? (
						<p className="vacio">
							Todavía no hay mensajes. Escribe abajo para empezar.
						</p>
					) : (
						mensajes.map((m) => <Entrada key={m.id} m={m} />)
					)}
					{error ? (
						<div className="aviso" data-tono="error" role="alert">
							<p>No se pudo enviar: {error.mensaje}</p>
							<button
								type="button"
								className="btn"
								onClick={() => vivo.enviar(error.texto)}
								disabled={!conectado}
							>
								Reintentar
							</button>
						</div>
					) : null}
					<div ref={fin} />
				</div>
			</div>
			<Redactor vivo={vivo} conectado={conectado} />
		</main>
	);
}

function Subagentes() {
	return (
		<aside className="ohx-columna ohx-arbol" aria-labelledby="ohx-subagentes">
			<div className="ohx-rotulo">
				<h2 id="ohx-subagentes">Subagentes</h2>
			</div>
			<p className="vacio">
				Ningún subagente activo. Aparecen aquí cuando el agente delega en una
				ruta.
			</p>
		</aside>
	);
}

export function App() {
	const estado = useEstado();
	const { estado: conexion, reintentar } = useConexion();
	return (
		<div className="ohx">
			<Cabecera conexion={conexion} reintentar={reintentar} />
			<div className="ohx-cuerpo">
				<Rutas estado={estado} />
				<Conversacion conectado={conexion === "conectado"} />
				<Subagentes />
			</div>
		</div>
	);
}
