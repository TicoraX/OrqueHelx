// Pantalla principal: rutas y cuotas | conversacion | subagentes (contrato de direccion, forma "tres columnas").
import React from "react";
import {
	type EstadoCarga,
	type EstadoConexion,
	useConexion,
	useEstado,
} from "./datos";
import { type RutaEstado, resumen } from "./estado";

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

function Conversacion() {
	return (
		<main className="ohx-columna ohx-chat">
			<div className="ohx-lectura">
				<h1>Conversación</h1>
				<p>Todavía no hay mensajes.</p>
			</div>
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
				<Conversacion />
				<Subagentes />
			</div>
		</div>
	);
}
