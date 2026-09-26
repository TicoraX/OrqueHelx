// Pantalla principal: rutas y cuotas | conversacion | subagentes (contrato de direccion, forma "tres columnas").
import React, { useEffect, useRef, useState } from "react";
import type { Mensaje, Pausa } from "./chat";
import {
	type ChatVivo,
	type EstadoCarga,
	useChat,
	useConexion,
	useEstado,
} from "./datos";
import { hora, type Resumen, type RutaEstado, resumen } from "./estado";
import type { EstadoConexion } from "./gateway";
import { type Arbol, enOrden, ficha } from "./subagentes";

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

/** Casilla de libro mayor: rotulo, detalle, cifra y notas. Rutas y subagentes comparten el formato. */
function Casilla({
	clave,
	detalle,
	r,
	nivel = 0,
}: {
	clave: string;
	detalle: string;
	r: Resumen;
	nivel?: number;
}) {
	return (
		<li
			className="casilla"
			data-tono={r.tono}
			style={nivel ? ({ "--nivel": nivel } as React.CSSProperties) : undefined}
		>
			<span className="casilla-k">{clave}</span>
			<span className="casilla-m" title={detalle}>
				{detalle}
			</span>
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
					<Casilla
						key={r.nombre}
						clave={r.nombre}
						detalle={r.modelo ?? r.proveedor}
						r={resumen(r)}
					/>
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
	const pausado = vivo.chat.turno === "pausado";
	const ocupado =
		vivo.chat.turno === "esperando" || vivo.chat.turno === "respondiendo";
	const quieto = pausado || !conectado;
	let indicacion =
		"Escribe al agente. Enter envía, Shift+Enter hace un salto de línea.";
	if (pausado)
		indicacion =
			"En pausa por cuota: reanuda, reenvía o cancela arriba para seguir.";
	if (!conectado) indicacion = "Sin conexión con Hermes.";
	const enviar = () => {
		if (!texto.trim() || ocupado || quieto) return;
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
				placeholder={indicacion}
				disabled={quieto}
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
						disabled={!texto.trim() || quieto}
					>
						Enviar
					</button>
				)}
			</div>
		</form>
	);
}

/** Rutas a las que el principal puede pasar: con modelo (config.set lo exige) y de otro proveedor. */
const destinos = (rutas: RutaEstado[], pausa: Pausa) =>
	rutas.filter((r) => r.modelo && r.proveedor !== pausa.proveedor);

/** Lamina de pausa (estiri): que se agoto, cuando vuelve y las tres salidas. Nunca cambia sola de ruta. */
function TarjetaPausa({
	pausa,
	rutas,
	vivo,
	conectado,
}: {
	pausa: Pausa;
	rutas: RutaEstado[];
	vivo: ChatVivo;
	conectado: boolean;
}) {
	const opciones = destinos(rutas, pausa);
	const [destino, setDestino] = useState("");
	const elegida = opciones.find((r) => r.nombre === destino) ?? opciones[0];
	const vuelve = hora(pausa.reinicio);
	return (
		<section className="lamina" aria-labelledby="ohx-pausa">
			<h3 className="lamina-k" id="ohx-pausa">
				En pausa · sin cuota
			</h3>
			<dl className="asientos">
				<div>
					<dt>proveedor</dt>
					<dd>{pausa.proveedor}</dd>
				</div>
				<div>
					<dt>modelo</dt>
					<dd>{pausa.modelo ?? "sin dato"}</dd>
				</div>
				<div>
					<dt>reinicia</dt>
					<dd>{vuelve}</dd>
				</div>
			</dl>
			<p className="lamina-v">
				{pausa.reinicio
					? `El turno se reanuda solo a las ${vuelve}, con el mismo modelo.`
					: "El proveedor no informa cuándo vuelve la cuota. Reanuda cuando quieras o pasa el turno a otra ruta."}
			</p>
			<div className="lamina-acciones">
				<button
					type="button"
					className="btn btn-accion"
					onClick={vivo.reanudar}
					disabled={!conectado}
				>
					Reanudar ahora
				</button>
				{elegida ? (
					<div className="lamina-reenvio">
						<label className="sr" htmlFor="ohx-destino">
							Ruta para reenviar el turno
						</label>
						<select
							id="ohx-destino"
							value={elegida.nombre}
							onChange={(e) => setDestino(e.target.value)}
						>
							{opciones.map((r) => (
								<option key={r.nombre} value={r.nombre}>
									{r.nombre} · {r.modelo}
								</option>
							))}
						</select>
						<button
							type="button"
							className="btn"
							onClick={() => vivo.reenviar(elegida)}
							disabled={!conectado}
						>
							Reenviar
						</button>
					</div>
				) : null}
				<button type="button" className="btn" onClick={vivo.cancelar}>
					Cancelar
				</button>
			</div>
		</section>
	);
}

function Conversacion({
	vivo,
	rutas,
	conectado,
}: {
	vivo: ChatVivo;
	rutas: RutaEstado[];
	conectado: boolean;
}) {
	const { mensajes, error, pausa } = vivo.chat;
	const fin = useRef<HTMLDivElement>(null);
	const ultimo = mensajes.at(-1);
	// Sigue la respuesta mientras llega (el texto de la ultima cambia con cada delta) y la pausa al aparecer.
	useEffect(() => {
		fin.current?.scrollIntoView({ block: "end" });
	}, [mensajes.length, ultimo?.texto, pausa]);
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
					{vivo.chat.pausa ? (
						<TarjetaPausa
							pausa={vivo.chat.pausa}
							rutas={rutas}
							vivo={vivo}
							conectado={conectado}
						/>
					) : null}
					{error ? (
						<div className="aviso" data-tono="error" role="alert">
							<p>
								{error.texto === null
									? error.mensaje
									: `No se pudo enviar: ${error.mensaje}`}
							</p>
							{error.texto === null ? null : (
								<button
									type="button"
									className="btn"
									onClick={() => vivo.enviar(error.texto ?? "")}
									disabled={!conectado}
								>
									Reintentar
								</button>
							)}
						</div>
					) : null}
					<div ref={fin} />
				</div>
			</div>
			<Redactor vivo={vivo} conectado={conectado} />
		</main>
	);
}

function Subagentes({ arbol }: { arbol: Arbol }) {
	return (
		<aside className="ohx-columna ohx-arbol" aria-labelledby="ohx-subagentes">
			<div className="ohx-rotulo">
				<h2 id="ohx-subagentes">Subagentes</h2>
			</div>
			{arbol.nodos.length === 0 ? (
				<p className="vacio">
					Todavía no hay subagentes en esta conversación. Aparecen aquí cuando
					el agente delega en una ruta.
				</p>
			) : (
				<ul className="casillas">
					{enOrden(arbol.nodos).map(([n, nivel]) => (
						<Casilla
							key={n.id}
							clave={n.ruta ?? "subagente"}
							detalle={n.objetivo}
							r={ficha(n)}
							nivel={nivel}
						/>
					))}
				</ul>
			)}
		</aside>
	);
}

export function App() {
	const estado = useEstado();
	const { estado: conexion, reintentar } = useConexion();
	const vivo = useChat();
	return (
		<div className="ohx">
			<Cabecera conexion={conexion} reintentar={reintentar} />
			<div className="ohx-cuerpo">
				<Rutas estado={estado} />
				<Conversacion
					vivo={vivo}
					rutas={estado.datos?.rutas ?? []}
					conectado={conexion === "conectado"}
				/>
				<Subagentes arbol={vivo.chat.subagentes} />
			</div>
		</div>
	);
}
