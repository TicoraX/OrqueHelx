// Main screen: routes and quotas | conversation | subagents (direction contract, "three columns" shape).
import React, { useEffect, useRef, useState } from "react";
import type { Message, Pause } from "./chat";
import {
	type LiveChat,
	type StatusLoad,
	useChat,
	useConnection,
	useStatus,
} from "./data";
import type { ConnectionState } from "./gateway";
import { isolate } from "./isolate";
import { blocks, type Span } from "./markdown";
import { useLocale } from "./sdk";
import {
	clock,
	mainAgentLabel,
	modelName,
	type RouteStatus,
	type RoutesStatus,
	type Summary,
	summary,
} from "./status";
import { card, inOrder, type Tree } from "./subagents";
import { setLanguage, t } from "./texts";

function RefreshIcon() {
	return (
		<svg viewBox="0 0 24 24" aria-hidden="true">
			<path d="M21 12a9 9 0 1 1-2.64-6.36" />
			<path d="M21 3v6h-6" />
		</svg>
	);
}

function Header({
	connection,
	retry,
}: {
	connection: ConnectionState;
	retry: () => void;
}) {
	return (
		<header className="ohx-header">
			<h1 className="ohx-brand">OrqueHelx</h1>
			<div className="ohx-connection" data-state={connection} role="status">
				<span>
					<span className="ohx-connection-k">{t("gateway")} </span>
					<b>{t(connection)}</b>
				</span>
				{connection === "offline" ? (
					<button type="button" className="btn" onClick={retry}>
						{t("retry")}
					</button>
				) : null}
			</div>
			<nav>
				<a href="/">{t("back_to_hermes")}</a>
			</nav>
		</header>
	);
}

/** A route's ledger cell: label, detail, figure and notes. */
function Cell({
	label,
	detail,
	s,
}: {
	label: string;
	detail: string;
	s: Summary;
}) {
	return (
		<li className="cell" data-tone={s.tone}>
			<span className="cell-k">{label}</span>
			<span className="cell-m" title={detail}>
				{detail}
			</span>
			<p className="cell-v">{s.figure}</p>
			<ul className="cell-n">
				{s.lines.map((l) => (
					<li key={l}>{l}</li>
				))}
			</ul>
		</li>
	);
}

function Routes({ status }: { status: StatusLoad }) {
	const { data, loading, error, refresh } = status;
	let body: React.ReactNode;
	if (error) {
		body = (
			<div className="notice" data-tone="error" role="alert">
				<p>{t("status_failed", { error })}</p>
				<button type="button" className="btn" onClick={refresh}>
					{t("retry")}
				</button>
			</div>
		);
	} else if (!data) {
		body = <p className="empty">{t("measuring")}</p>;
	} else if (data.error) {
		body = (
			<div className="notice" data-tone="error" role="alert">
				<p>{data.error}</p>
			</div>
		);
	} else if (data.routes.length === 0) {
		body = (
			<p className="empty">
				{t("no_routes_before")}{" "}
				<code>plugins.entries.orquehelx.settings.routes</code>{" "}
				{t("no_routes_after")}
			</p>
		);
	} else {
		body = (
			<ul className="cells" aria-busy={loading}>
				{data.routes.map((r) => (
					<Cell
						key={r.name}
						label={r.name}
						detail={r.model ?? r.provider}
						s={summary(r)}
					/>
				))}
			</ul>
		);
	}
	return (
		<aside className="ohx-column ohx-rail" aria-labelledby="ohx-routes">
			<div className="ohx-label">
				<h2 id="ohx-routes">{t("routes")}</h2>
				<button
					type="button"
					className="btn btn-icon"
					onClick={refresh}
					disabled={loading}
					aria-busy={loading}
					title={t("measure_again")}
				>
					<RefreshIcon />
					<span className="sr">{t("measure_again_sr")}</span>
				</button>
			</div>
			{body}
		</aside>
	);
}

const ROLE = {
	user: "role_user",
	agent: "role_agent",
	note: "role_note",
} as const;
const NOTE = {
	writing: "note_writing",
	interrupted: "note_stopped",
	error: "note_no_answer",
} as const;

function Line({ spans }: { spans: Span[] }) {
	return (
		<>
			{spans.map((x, i) => {
				if (x.t === "bold") return <strong key={i}>{x.v}</strong>;
				if (x.t === "italic") return <em key={i}>{x.v}</em>;
				if (x.t === "code") return <code key={i}>{x.v}</code>;
				if (x.t === "link")
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

function Markdown({ text }: { text: string }) {
	return (
		<div className="ohx-message-v ohx-md">
			{blocks(text).map((b, i) => {
				if (b.t === "code")
					return (
						<pre key={i}>
							<code>{b.v}</code>
						</pre>
					);
				if (b.t === "heading")
					return (
						<p key={i}>
							<strong>
								<Line spans={b.spans} />
							</strong>
						</p>
					);
				if (b.t === "list") {
					const List = b.ordered ? "ol" : "ul";
					return (
						<List key={i} start={b.ordered ? b.start : undefined}>
							{b.items.map((it, j) => (
								<li key={j}>
									<Line spans={it} />
								</li>
							))}
						</List>
					);
				}
				return (
					<p key={i}>
						{b.lines.map((l, j) => (
							<React.Fragment key={j}>
								{j > 0 && <br />}
								<Line spans={l} />
							</React.Fragment>
						))}
					</p>
				);
			})}
		</div>
	);
}

function Entry({ m }: { m: Message }) {
	if (m.role === "note") {
		return (
			<p className="ohx-note" role="note">
				{m.text}
			</p>
		);
	}
	const noteId =
		m.role === "user" && m.state === "error"
			? "note_not_sent"
			: NOTE[m.state as keyof typeof NOTE];
	// A failed turn's text is Hermes' raw error: a line of its own, with the original on demand.
	let body: React.ReactNode = null;
	if (m.role === "agent" && m.state === "error" && m.text) {
		body = (
			<details className="ohx-failure">
				<summary>{t("failure_summary")}</summary>
				<pre>{m.text}</pre>
			</details>
		);
	} else if (m.text && m.role === "agent") {
		body = <Markdown text={m.text} />;
	} else if (m.text) {
		// What you wrote shows as is.
		body = <p className="ohx-message-v">{m.text}</p>;
	}
	return (
		<article className="ohx-message" data-role={m.role} data-state={m.state}>
			<header className="ohx-message-k">
				{t(ROLE[m.role])}
				{m.model ? <span> · {modelName(m.model)}</span> : null}
				{noteId ? <span> · {t(noteId)}</span> : null}
			</header>
			{body}
		</article>
	);
}

function Composer({ live, connected }: { live: LiveChat; connected: boolean }) {
	const [text, setText] = useState("");
	const paused = live.chat.turn === "paused";
	const busy = live.chat.turn === "waiting" || live.chat.turn === "responding";
	const still = paused || !connected;
	let hint = t("composer_hint");
	if (paused) hint = t("composer_paused");
	if (!connected) hint = t("composer_offline");
	const send = () => {
		if (!text.trim() || busy || still) return;
		live.send(text);
		setText("");
	};
	return (
		<form
			className="ohx-composer"
			onSubmit={(e) => {
				e.preventDefault();
				send();
			}}
		>
			<label className="sr" htmlFor="ohx-message">
				{t("composer_label")}
			</label>
			<textarea
				id="ohx-message"
				rows={3}
				value={text}
				placeholder={hint}
				disabled={still}
				onChange={(e) => setText(e.target.value)}
				onKeyDown={(e) => {
					if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
						e.preventDefault();
						send();
					}
				}}
			/>
			<div className="ohx-composer-actions">
				<span className="ohx-activity" role="status">
					{live.chat.activity ?? (busy ? t("waiting_answer") : "")}
				</span>
				{busy ? (
					<button type="button" className="btn" onClick={live.stop}>
						{t("stop")}
					</button>
				) : (
					<button
						type="submit"
						className="btn btn-primary"
						disabled={!text.trim() || still}
					>
						{t("send")}
					</button>
				)}
			</div>
		</form>
	);
}

/** Routes the main agent can move to: with a model (config.set requires it) and on another provider. */
const targets = (routes: RouteStatus[], pause: Pause) =>
	routes.filter((r) => r.model && r.provider !== pause.provider);

/** Pause sheet (estiri): what ran out, when it comes back and the three exits. It never switches routes alone. */
function PauseSheet({
	pause,
	routes,
	live,
	connected,
}: {
	pause: Pause;
	routes: RouteStatus[];
	live: LiveChat;
	connected: boolean;
}) {
	const options = targets(routes, pause);
	const [target, setTarget] = useState("");
	const chosen = options.find((r) => r.name === target) ?? options[0];
	const back = clock(pause.reset_at);
	return (
		<section className="sheet" aria-labelledby="ohx-pause">
			<h3 className="sheet-k" id="ohx-pause">
				{t("paused_title")}
			</h3>
			<dl className="entries">
				<div>
					<dt>{t("provider")}</dt>
					<dd>{pause.provider}</dd>
				</div>
				<div>
					<dt>{t("model")}</dt>
					<dd>{pause.model ?? t("no_data")}</dd>
				</div>
				<div>
					<dt>{t("resets_label")}</dt>
					<dd className="figure">{back}</dd>
				</div>
			</dl>
			<p className="sheet-v">
				{pause.reset_at ? t("resumes_at", { time: back }) : t("reset_unknown")}
			</p>
			<div className="sheet-actions">
				<button
					type="button"
					className="btn btn-primary"
					onClick={live.resume}
					disabled={!connected}
				>
					{t("resume_now")}
				</button>
				{chosen ? (
					<div className="sheet-resend">
						<label className="sr" htmlFor="ohx-target">
							{t("resend_route_label")}
						</label>
						<select
							id="ohx-target"
							value={chosen.name}
							onChange={(e) => setTarget(e.target.value)}
						>
							{options.map((r) => (
								<option key={r.name} value={r.name}>
									{r.name} · {r.model}
								</option>
							))}
						</select>
						<button
							type="button"
							className="btn"
							onClick={() =>
								live.resend(
									chosen,
									mainAgentLabel(
										{ provider: pause.provider, model: pause.model },
										routes,
									),
								)
							}
							disabled={!connected}
						>
							{t("resend")}
						</button>
					</div>
				) : null}
				<button type="button" className="btn" onClick={live.cancel}>
					{t("cancel")}
				</button>
			</div>
		</section>
	);
}

function Conversation({
	live,
	routes,
	configuredMain,
	connected,
}: {
	live: LiveChat;
	routes: RouteStatus[];
	configuredMain: RoutesStatus["main"];
	connected: boolean;
}) {
	const { messages, error, pause } = live.chat;
	// Who answers: the live session's (session.info wins) or, before the first turn, the config's.
	const who = live.chat.main ?? configuredMain;
	const end = useRef<HTMLDivElement>(null);
	const last = messages.at(-1);
	// Follow the answer while it arrives (the last text changes with each delta) and the pause when it shows.
	useEffect(() => {
		end.current?.scrollIntoView({ block: "end" });
	}, [messages.length, last?.text, pause]);
	return (
		<main className="ohx-column ohx-chat">
			<div className="ohx-label">
				<h2>{t("conversation")}</h2>
				{who ? (
					<span className="ohx-main-agent" title={who.provider ?? undefined}>
						{t("answers")} <b>{mainAgentLabel(who, routes)}</b>
					</span>
				) : null}
				{messages.length > 0 ? (
					<button
						type="button"
						className="btn"
						onClick={live.reset}
						disabled={live.chat.turn !== "idle"}
					>
						{t("new_conversation")}
					</button>
				) : null}
			</div>
			<div
				className="ohx-thread"
				role="log"
				aria-live="polite"
				aria-relevant="additions text"
			>
				<div className="ohx-reading">
					{messages.length === 0 ? (
						<p className="empty">{t("no_messages")}</p>
					) : (
						messages.map((m) => <Entry key={m.id} m={m} />)
					)}
					{pause ? (
						<PauseSheet
							pause={pause}
							routes={routes}
							live={live}
							connected={connected}
						/>
					) : null}
					{error ? (
						<div className="notice" data-tone="error" role="alert">
							<p>
								{error.text === null
									? error.message
									: t("send_failed", { error: error.message })}
							</p>
							{error.text === null ? null : (
								<button
									type="button"
									className="btn"
									onClick={() => live.send(error.text ?? "")}
									disabled={!connected}
								>
									{t("retry")}
								</button>
							)}
						</div>
					) : null}
					<div ref={end} />
				</div>
			</div>
			<Composer live={live} connected={connected} />
		</main>
	);
}

function Subagents({ tree }: { tree: Tree }) {
	return (
		<aside className="ohx-column ohx-tree" aria-labelledby="ohx-subagents">
			<div className="ohx-label">
				<h2 id="ohx-subagents">{t("subagents")}</h2>
			</div>
			{tree.nodes.length === 0 ? (
				<p className="empty">{t("no_subagents")}</p>
			) : (
				// Entries: one row per subagent; the state changes in its row without moving the others.
				<dl className="entries ohx-subagents">
					{inOrder(tree.nodes).map(([n, level]) => {
						const c = card(n);
						return (
							<div
								key={n.id}
								data-state={n.state}
								style={
									level
										? ({ "--level": level } as React.CSSProperties)
										: undefined
								}
							>
								<dt>{n.route ?? t("subagent")}</dt>
								<dd>
									<span className="ohx-sub-state">{c.figure}</span>
									<span className="ohx-sub-goal" title={n.goal}>
										{n.goal}
									</span>
									{c.lines.map((l) => (
										<span key={l} className="ohx-sub-note">
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
	// Before anything renders: every t() below reads this language.
	setLanguage(useLocale());
	const status = useStatus();
	const { state: connection, retry } = useConnection();
	const live = useChat();
	const root = useRef<HTMLDivElement>(null);
	useEffect(() => (root.current ? isolate(root.current) : undefined), []);
	// The rail is measured again on an event, when a route runs out of quota, so it says the same as the pause
	// sheet and the tree (no polling).
	const { refresh } = status;
	const exhausted = live.chat.subagents.nodes.filter(
		(n) => n.state === "out_of_quota",
	).length;
	const hasPause = live.chat.pause !== null;
	useEffect(() => {
		if (hasPause || exhausted > 0) refresh();
	}, [hasPause, exhausted, refresh]);
	return (
		<div className="ohx" ref={root}>
			<Header connection={connection} retry={retry} />
			<div className="ohx-body">
				<Routes status={status} />
				<Conversation
					live={live}
					routes={status.data?.routes ?? []}
					configuredMain={status.data?.main ?? null}
					connected={connection === "connected"}
				/>
				<Subagents tree={live.chat.subagents} />
			</div>
		</div>
	);
}
