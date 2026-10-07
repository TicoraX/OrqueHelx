// Screen data: routes status (the plugin's backend), the gateway connection and the conversation.
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
	initial,
	type MainAgent,
	mainAgentOf,
	type Pause,
	reduce,
} from "./chat";
import { type ConnectionState, Gateway } from "./gateway";
import { sdk } from "./sdk";
import type { RouteStatus, RoutesStatus } from "./status";
import { t } from "./texts";

const STATUS_URL = "/api/plugins/orquehelx/status";
const PAUSES_URL = "/api/plugins/orquehelx/pauses";

/** One connection per page load; the header and the chat share it. */
const gateway = new Gateway(() => sdk().buildWsUrl("/api/ws"));

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export interface StatusLoad {
	data: RoutesStatus | null;
	loading: boolean;
	error: string | null;
	refresh: () => void;
}

/** Routes and measured quota. Measures on request: no timers. */
export function useStatus(): StatusLoad {
	const [data, setData] = useState<RoutesStatus | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const [request, setRequest] = useState(0);

	useEffect(() => {
		let current = true;
		setLoading(true);
		sdk()
			.fetchJSON<RoutesStatus>(STATUS_URL)
			.then((d) => {
				if (!current) return;
				setData(d);
				setError(null);
			})
			.catch((e: unknown) => {
				if (current) setError(errorText(e));
			})
			.finally(() => {
				if (current) setLoading(false);
			});
		return () => {
			current = false;
		};
	}, [request]);

	const refresh = useCallback(() => setRequest((n) => n + 1), []);
	return { data, loading, error, refresh };
}

/** State of the /api/ws connection. "connected" only after gateway.ready. */
export function useConnection(): { state: ConnectionState; retry: () => void } {
	const state = useSyncExternalStore(gateway.onState, gateway.state);
	useEffect(() => {
		void gateway.connect();
	}, []);
	const retry = useCallback(() => gateway.reconnect(), []);
	return { state, retry };
}

export interface LiveChat {
	chat: Chat;
	send: (text: string) => void;
	stop: () => void;
	reset: () => void;
	resume: () => void;
	/** from: "route · model" of the main agent that ran out of quota, for the thread's note. */
	resend: (route: RouteStatus, from: string) => void;
	cancel: () => void;
}

interface HermesSession {
	session_id: string;
	stored_session_id?: string | null;
	messages?: { role?: unknown; text?: unknown }[];
	info?: Record<string, unknown>;
}

const post = <T>(url: string, body: unknown) =>
	sdk().fetchJSON<T>(url, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body),
	});

/** Resolves when the gateway sends gateway.ready (useConnection opens the socket). */
const connected = () =>
	new Promise<void>((done) => {
		if (gateway.state() === "connected") return done();
		const off = gateway.onState(() => {
			if (gateway.state() !== "connected") return;
			off();
			done();
		});
	});

// ponytail: setTimeout accepts up to ~24.8 days; a weekly window fits easily.
const MAX_WAIT = 2 ** 31 - 1;
/** Margin after the reset time: the provider does not always free the quota on the exact second. */
const RESET_MARGIN = 30_000;

/**
 * The conversation with the main agent. Hermes' session is created with the first message.
 *
 * Quota pause: a message.complete with error asks /pauses; there is only a pause if the quota observer
 * recorded a current exhaustion for the provider. Leaving the pause first claims it in the backend (atomic:
 * another tab gets 409) and then retries in Hermes with command.dispatch retry, which rewinds the last turn
 * without duplicating the message in the history.
 */
export function useChat(): LiveChat {
	const [chat, dispatch] = useReducer(reduce, initial);
	// The session as a shared promise: send and stop wait for the same creation, and a stop requested while it
	// is being created goes out after the prompt (the socket keeps the order).
	const session = useRef<Promise<string> | null>(null);
	const sessionId = useRef<string | null>(null);
	// What a pause needs: the stored session (survives reloads) and the main agent's provider, which in a lazy
	// session only arrives with session.info.
	const stored = useRef<string | null>(null);
	const main = useRef<MainAgent | null>(null);
	/** Effects read the ref; the reducer shows it (who answers). */
	const setMain = useCallback((m: MainAgent | null) => {
		main.current = m;
		dispatch({ type: "main", main: m });
	}, []);

	const fail = useCallback(
		(e: unknown) => dispatch({ type: "failure", message: errorText(e) }),
		[],
	);

	const pauseIfQuota = useCallback(() => {
		const storedSession = stored.current;
		const m = main.current;
		if (!storedSession || !m) return;
		post<{ pause: Pause | null }>(PAUSES_URL, {
			session: storedSession,
			provider: m.provider,
			model: m.model,
		})
			.then((r) => {
				if (r.pause) dispatch({ type: "paused", pause: r.pause });
			})
			.catch((e: unknown) =>
				fail(t("pause_register_failed", { error: errorText(e) })),
			);
	}, [fail]);

	useEffect(
		() =>
			gateway.onEvent((type, sid, payload) => {
				if (!sid || sid !== sessionId.current) return;
				if (type === "session.info") {
					const reported = mainAgentOf(
						payload as Record<string, unknown> | null,
					);
					if (reported) setMain(reported);
				}
				dispatch({ type: "event", event: type, payload });
				if (
					type === "message.complete" &&
					(payload as { status?: unknown } | null)?.status === "error"
				)
					pauseIfQuota();
			}),
		[pauseIfQuota, setMain],
	);

	/** Attach the chat to a stored session (a pause from an earlier load) and show its history. */
	const reattach = useCallback(
		async (storedId: string) => {
			const r = await gateway.rpc<HermesSession>("session.resume", {
				session_id: storedId,
				lazy: true,
			});
			sessionId.current = r.session_id;
			session.current = Promise.resolve(r.session_id);
			stored.current = storedId;
			dispatch({ type: "history", messages: r.messages ?? [] });
			setMain(mainAgentOf(r.info));
		},
		[setMain],
	);

	// When the tab opens: a pending pause from before (reload, computer turned off) comes back into view.
	useEffect(() => {
		let current = true;
		sdk()
			.fetchJSON<{ pauses: Pause[] }>(PAUSES_URL)
			.then(async ({ pauses }) => {
				const last = pauses.at(-1);
				if (!current || !last) return;
				await connected();
				if (!current) return;
				// Without a session there is nowhere to resume: the pause stays pending in SQLite and comes back on
				// the next load, instead of showing a card whose claim would spend it without retrying anything.
				try {
					await reattach(last.session);
				} catch (e) {
					fail(t("resume_session_failed", { error: errorText(e) }));
					return;
				}
				if (current) dispatch({ type: "paused", pause: last });
			})
			.catch(fail);
		return () => {
			current = false;
		};
	}, [reattach, fail]);

	const send = useCallback(
		(text: string) => {
			const clean = text.trim();
			if (!clean) return;
			dispatch({ type: "sent", text: clean });
			if (!session.current) {
				const creating = gateway
					.rpc<HermesSession>("session.create", {})
					.then((r) => {
						sessionId.current = r.session_id;
						stored.current = r.stored_session_id ?? r.session_id;
						setMain(mainAgentOf(r.info));
						return r.session_id;
					});
				session.current = creating;
				// If creation fails, the retry creates another one.
				creating.catch(() => {
					if (session.current === creating) session.current = null;
				});
			}
			session.current
				.then((id) =>
					gateway.rpc("prompt.submit", { session_id: id, text: clean }),
				)
				.catch(fail);
		},
		[fail],
	);

	const stop = useCallback(() => {
		session.current
			?.then((id) => gateway.rpc("session.interrupt", { session_id: id }))
			.catch(fail);
	}, [fail]);

	const reset = useCallback(() => {
		session.current = null;
		sessionId.current = null;
		stored.current = null;
		main.current = null;
		dispatch({ type: "new" });
	}, []);

	/** Claim the pause; false if another tab (or the reset) already resolved it. */
	const claim = useCallback(
		async (
			pause: Pause,
			action: "resume" | "resend" | "cancel",
			target?: string,
		) => {
			try {
				await post(`${PAUSES_URL}/${pause.id}/${action}`, {
					target: target ?? null,
				});
				return true;
			} catch (e) {
				if ((e as { status?: number }).status === 409) {
					fail(t("pause_resolved_elsewhere"));
					return false;
				}
				throw e;
			}
		},
		[fail],
	);

	const sessionOfPause = () =>
		session.current ?? Promise.reject(new Error(t("pause_no_session")));

	/** The last turn again, with the session's current model. */
	const retry = useCallback(async () => {
		const id = await sessionOfPause();
		const r = await gateway.rpc<{ type?: string; message?: string }>(
			"command.dispatch",
			{
				session_id: id,
				name: "retry",
			},
		);
		if (r.type !== "send" || !r.message) throw new Error(t("retry_missing"));
		await gateway.rpc("prompt.submit", { session_id: id, text: r.message });
	}, []);

	const leave = useCallback(
		(steps: () => Promise<void>) => {
			steps().catch((e: unknown) =>
				fail(t("retry_rejected", { error: errorText(e) })),
			);
		},
		[fail],
	);

	const pause = chat.pause;

	const resume = useCallback(() => {
		if (!pause) return;
		claim(pause, "resume")
			.then((won) => {
				if (!won) return;
				dispatch({ type: "resuming" });
				leave(retry);
			})
			.catch(fail);
	}, [pause, claim, retry, leave, fail]);

	const resend = useCallback(
		(route: RouteStatus, from: string) => {
			if (!pause || !route.model) return;
			const model = route.model;
			claim(pause, "resend", route.name)
				.then((won) => {
					if (!won) return;
					dispatch({
						type: "note",
						text: t("turn_resent", { from, route: route.name, model }),
					});
					dispatch({ type: "resuming" });
					leave(async () => {
						const id = await sessionOfPause();
						// --session: the change applies to this conversation, it does not touch Hermes' default model.
						await gateway.rpc("config.set", {
							session_id: id,
							key: "model",
							value: `${model} --provider ${route.provider} --session`,
						});
						setMain({ provider: route.provider, model });
						await retry();
					});
				})
				.catch(fail);
		},
		[pause, claim, retry, leave, fail],
	);

	const cancel = useCallback(() => {
		if (!pause) return;
		claim(pause, "cancel")
			.then((won) => {
				if (won) dispatch({ type: "cancelled" });
			})
			.catch(fail);
	}, [pause, claim, fail]);

	// With a measured reset time, it resumes on its own on the same provider. Offline, it waits for the button.
	useEffect(() => {
		if (!pause?.reset_at) return;
		const wait = Date.parse(pause.reset_at) - Date.now() + RESET_MARGIN;
		if (Number.isNaN(wait)) return;
		const timer = setTimeout(
			() => {
				if (gateway.state() === "connected") resume();
			},
			Math.min(Math.max(wait, 0), MAX_WAIT),
		);
		return () => clearTimeout(timer);
	}, [pause, resume]);

	return { chat, send, stop, reset, resume, resend, cancel };
}
