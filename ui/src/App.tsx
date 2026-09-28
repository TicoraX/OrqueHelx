// Pantalla principal: rutas y cuotas | conversacion | subagentes (contrato de direccion, forma "tres columnas").
import React, { useEffect, useRef, useState } from "react";
import { aislar } from "./aislar";
import type { Mensaje, Pausa } from "./chat";
import {
	type ChatVivo,
	type EstadoCarga,
	useChat,
	useConexion,
	useEstado,
} from "./datos";
import {
	type EstadoRutas,
	etiquetaPrincipal,
	hora,
	nombreModelo,
	type Resumen,
	type RutaEstado,
	resumen,
} from "./estado";
import type { EstadoConexion } from "./gateway";
import { bloques, type Tramo } from "./markdown";
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
			<h1 className="ohx-marca">OrqueHelx</h1>
			<div className="ohx-conexion" data-estado={conexion} role="status">
				<span>
					<span className="ohx-conexion-k">gateway </span>
					<b>{TEXTO_CONEXION[conexion]}</b>
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

/** Casilla de libro mayor de una ruta: rotulo, detalle, cifra y notas. */
function Casilla({
	clave,
	detalle,
	r,
}: {
	clave: string;
	detalle: string;
	r: Resumen;
}) {
	return (
		<li className="casilla" data-tono={r.tono}>
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
	nota: "nota",
};
const NOTA: Partial<Record<Mensaje["estado"], string>> = {
	escribiendo: "escribiendo",
	interrumpido: "detenido",
	error: "sin respuesta",
};

function Linea({ tramos }: { tramos: Tramo[] }) {
	return (
		<>
			{tramos.map((x, i) => {
				if (x.t === "negrita") return <strong key={i}>{x.v}</strong>;
				if (x.t === "cursiva") return <em key={i}>{x.v}</em>;
				if (x.t === "codigo") return <code key={i}>{x.v}</code>;
				if (x.t === "enlace")
					return (
						<a key={i} href={x.href} target="_blank" rel="noopener noreferrer">
							{x.v}
						</a>
					);
				return x.v;
			})}
		</>
	);
}

function Markdown({ texto }: { texto: string }) {
	return (
		<div className="ohx-mensaje-v ohx-md">
			{bloques(texto).map((b, i) => {
				if (b.t === "codigo")
					return (
						<pre key={i}>
							<code>{b.v}</code>
						</pre>
					);
				if (b.t === "titulo")
					return (
						<p key={i}>
							<strong>
								<Linea tramos={b.tramos} />
							</strong>
						</p>
					);
				if (b.t === "lista") {
					const Lista = b.ordenada ? "ol" : "ul";
					return (
						<Lista key={i}>
							{b.items.map((it, j) => (
								<li key={j}>
									<Linea tramos={it} />
								</li>
							))}
						</Lista>
					);
				}
				return (
					<p key={i}>
						{b.lineas.map((l, j) => (
							<React.Fragment key={j}>
								{j > 0 && <br />}
								<Linea tramos={l} />
							</React.Fragment>
						))}
					</p>
				);
			})}
		</div>
	);
}

function Entrada({ m }: { m: Mensaje }) {
	if (m.rol === "nota") {
		return (
			<p className="ohx-asiento" role="note">
				{m.texto}
			</p>
		);
	}
	const nota =
		m.rol === "usuario" && m.estado === "error" ? "no enviado" : NOTA[m.estado];
	// El texto de un turno fallido es el error crudo de Hermes (en ingles): una linea propia y el original a pedido.
	let cuerpo: React.ReactNode = null;
	if (m.rol === "agente" && m.estado === "error" && m.texto) {
		cuerpo = (
			<details className="ohx-fallo">
				<summary>
					El proveedor no respondió este turno. Ver el detalle de Hermes
				</summary>
				<pre>{m.texto}</pre>
			</details>
		);
	} else if (m.texto && m.rol === "agente") {
		cuerpo = <Markdown texto={m.texto} />;
	} else if (m.texto) {
		// Lo que escribiste se muestra tal cual.
		cuerpo = <p className="ohx-mensaje-v">{m.texto}</p>;
	}
	return (
		<article className="ohx-mensaje" data-rol={m.rol} data-estado={m.estado}>
			<header className="ohx-mensaje-k">
				{ROTULO[m.rol]}
				{m.modelo ? <span> · {nombreModelo(m.modelo)}</span> : null}
				{nota ? <span> · {nota}</span> : null}
			</header>
			{cuerpo}
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
					<dd className="cifra">{vuelve}</dd>
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
							onClick={() =>
								vivo.reenviar(
									elegida,
									etiquetaPrincipal(
										{ proveedor: pausa.proveedor, modelo: pausa.modelo },
										rutas,
									),
								)
							}
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
	principalConfigurado,
	conectado,
}: {
	vivo: ChatVivo;
	rutas: RutaEstado[];
	principalConfigurado: EstadoRutas["principal"];
	conectado: boolean;
}) {
	const { mensajes, error, pausa } = vivo.chat;
	// Quien responde: el de la sesion viva (session.info manda) o, antes del primer turno, el de la config.
	const quien = vivo.chat.principal ?? principalConfigurado;
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
				{quien ? (
					<span className="ohx-principal" title={quien.proveedor ?? undefined}>
						responde <b>{etiquetaPrincipal(quien, rutas)}</b>
					</span>
				) : null}
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
				// Asientos: una fila por subagente; el estado cambia en su fila sin mover las demas.
				<dl className="asientos ohx-subagentes">
					{enOrden(arbol.nodos).map(([n, nivel]) => {
						const f = ficha(n);
						return (
							<div
								key={n.id}
								data-estado={n.estado}
								style={
									nivel
										? ({ "--nivel": nivel } as React.CSSProperties)
										: undefined
								}
							>
								<dt>{n.ruta ?? "subagente"}</dt>
								<dd>
									<span className="ohx-sub-estado">{f.cifra}</span>
									<span className="ohx-sub-objetivo" title={n.objetivo}>
										{n.objetivo}
									</span>
									{f.lineas.map((l) => (
										<span key={l} className="ohx-sub-nota">
											{l}
										</span>
									))}
								</dd>
							</div>
						);
					})}
				</dl>
			)}
		</aside>
	);
}

export function App() {
	const estado = useEstado();
	const { estado: conexion, reintentar } = useConexion();
	const vivo = useChat();
	const raiz = useRef<HTMLDivElement>(null);
	useEffect(() => (raiz.current ? aislar(raiz.current) : undefined), []);
	// D21: el riel se mide de nuevo por evento, cuando una ruta queda sin cuota, para que diga lo mismo que la
	// tarjeta de pausa y el arbol (sin polling).
	const { refrescar } = estado;
	const agotados = vivo.chat.subagentes.nodos.filter(
		(n) => n.estado === "sin_cuota",
	).length;
	const hayPausa = vivo.chat.pausa !== null;
	useEffect(() => {
		if (hayPausa || agotados > 0) refrescar();
	}, [hayPausa, agotados, refrescar]);
	return (
		<div className="ohx" ref={raiz}>
			<Cabecera conexion={conexion} reintentar={reintentar} />
			<div className="ohx-cuerpo">
				<Rutas estado={estado} />
				<Conversacion
					vivo={vivo}
					rutas={estado.datos?.rutas ?? []}
					principalConfigurado={estado.datos?.principal ?? null}
					conectado={conexion === "conectado"}
				/>
				<Subagentes arbol={vivo.chat.subagentes} />
			</div>
		</div>
	);
}
