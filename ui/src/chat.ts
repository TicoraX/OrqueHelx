// Conversation state: Hermes gateway events -> messages, turn and visible activity.
// Pure reducer: same input, same output; the effects (session, sending) live in useChat.
//
//   sent -> turn "waiting" -> message.start -> "responding" -> message.delta* -> message.complete -> "idle"
//   claude-subscription sends no message.delta: the text arrives whole in message.complete.

import { empty, reduceTree, TOOL, type Tree } from "./subagents";
import { t } from "./texts";

/** "note": a system entry in the thread (a resend), nobody says it. */
type Role = "user" | "agent" | "note";
type MessageState = "done" | "writing" | "interrupted" | "error";

export interface Message {
	id: number;
	role: Role;
	text: string;
	state: MessageState;
	/** Only on the agent's answers: the main agent's model that gave it (after a resend, it changes). */
	model?: string | null;
}

/** A quota pause of the main agent, as /api/plugins/orquehelx/pauses returns it. */
export interface Pause {
	id: number;
	/** Hermes' stored_session_id: survives a page reload. */
	session: string;
	provider: string;
	model: string | null;
	/** ISO of the measured reset, or null if the provider does not report it. */
	reset_at: string | null;
	state: string;
	created: number;
}

/** Provider and model of the session's main agent. */
export interface MainAgent {
	provider: string;
	model: string | null;
}

export interface Chat {
	messages: Message[];
	/** "paused": out of quota; the composer stays still until resume, resend or cancel. */
	turn: "idle" | "waiting" | "responding" | "paused";
	pause: Pause | null;
	/** What the agent is doing now ("thinking", "delegating to opencode"); null if nothing. */
	activity: string | null;
	/** A failure: the message and, if it happened while sending, the text to retry without retyping it. */
	error: { message: string; text: string | null } | null;
	subagents: Tree;
	/** Who answers: provider and model of the main agent, or null if Hermes has not said yet. */
	main: MainAgent | null;
}

type Action =
	| { type: "sent"; text: string }
	| { type: "event"; event: string; payload: unknown }
	| { type: "failure"; message: string }
	| { type: "paused"; pause: Pause }
	| { type: "resuming" }
	| { type: "cancelled" }
	| { type: "history"; messages: { role?: unknown; text?: unknown }[] }
	| { type: "main"; main: MainAgent | null }
	| { type: "note"; text: string }
	| { type: "new" };

export const initial: Chat = {
	messages: [],
	turn: "idle",
	pause: null,
	activity: null,
	error: null,
	subagents: empty,
	main: null,
};

type Payload = Record<string, unknown> | null;

const nextId = (c: Chat) => (c.messages.at(-1)?.id ?? 0) + 1;

/** The answer in progress: the last message if it is the agent's and still writing; otherwise a new one. */
function withAnswer(c: Chat, change: (m: Message) => Message): Message[] {
	const last = c.messages.at(-1);
	if (last?.role === "agent" && last.state === "writing") {
		return [...c.messages.slice(0, -1), change(last)];
	}
	return [
		...c.messages,
		change({
			id: nextId(c),
			role: "agent",
			text: "",
			state: "writing",
			model: c.main?.model ?? null,
		}),
	];
}

function toolActivity(p: Payload): string {
	const name = typeof p?.name === "string" && p.name ? p.name : t("a_tool");
	const args = (p?.args ?? {}) as Record<string, unknown>;
	return name === TOOL && typeof args.route === "string"
		? t("delegating_to", { route: args.route })
		: t("using_tool", { tool: name });
}

const FINAL: Record<string, MessageState> = {
	interrupted: "interrupted",
	error: "error",
};

function onEvent(c: Chat, event: string, p: Payload): Chat {
	switch (event) {
		case "message.start":
			return { ...c, turn: "responding", messages: withAnswer(c, (m) => m) };
		case "message.delta":
			return {
				...c,
				turn: "responding",
				messages: withAnswer(c, (m) => ({
					...m,
					text: m.text + String(p?.text ?? ""),
				})),
			};
		case "thinking.delta":
		case "reasoning.delta":
			// The thinking text is the CLI's animated filler ("synthesizing..."): it only shows that it thinks.
			return { ...c, activity: t("thinking") };
		case "tool.start":
			return { ...c, activity: toolActivity(p) };
		case "tool.complete":
			return { ...c, activity: null };
		case "message.complete": {
			const state = FINAL[String(p?.status)] ?? "done";
			const final = typeof p?.text === "string" ? p.text : "";
			return {
				...c,
				turn: "idle",
				activity: null,
				messages: withAnswer(c, (m) => ({
					...m,
					text: final || m.text,
					state,
				})),
			};
		}
		default:
			return c;
	}
}

export function reduce(c: Chat, a: Action): Chat {
	switch (a.type) {
		case "sent":
			return {
				...c,
				turn: "waiting",
				error: null,
				messages: [
					...c.messages,
					{ id: nextId(c), role: "user", text: a.text, state: "done" },
				],
			};
		case "event": {
			const tree = reduceTree(c.subagents, a.event, a.payload);
			const next = onEvent(c, a.event, a.payload as Payload);
			return tree === c.subagents ? next : { ...next, subagents: tree };
		}
		case "failure": {
			// Only a failed send leaves the user's last message; if there was already an answer (a failure while
			// stopping, or a dropped connection), there is nothing to resend.
			const last = c.messages.at(-1);
			if (last?.role !== "user") {
				return {
					...c,
					turn: "idle",
					pause: null,
					activity: null,
					error: { message: a.message, text: null },
				};
			}
			return {
				...c,
				turn: "idle",
				pause: null,
				activity: null,
				messages: [...c.messages.slice(0, -1), { ...last, state: "error" }],
				error: { message: a.message, text: last.text },
			};
		}
		case "paused": {
			// The answer before the pause is the turn that failed for quota, even if it comes from the history.
			const last = c.messages.at(-1);
			const messages =
				last?.role === "agent"
					? [...c.messages.slice(0, -1), { ...last, state: "error" as const }]
					: c.messages;
			return { ...c, turn: "paused", pause: a.pause, activity: null, messages };
		}
		case "main":
			return { ...c, main: a.main };
		case "note":
			return {
				...c,
				messages: [
					...c.messages,
					{ id: nextId(c), role: "note", text: a.text, state: "done" },
				],
			};
		case "resuming":
			return { ...c, turn: "waiting", pause: null, error: null };
		case "cancelled":
			return { ...c, turn: "idle", pause: null };
		case "history":
			return { ...initial, main: c.main, messages: fromHistory(a.messages) };
		case "new":
			return initial;
	}
}

const ROLE: Record<string, Role> = { user: "user", assistant: "agent" };

/** Hermes transcript (session.resume) -> visible messages: only user and agent with text. */
function fromHistory(rows: { role?: unknown; text?: unknown }[]): Message[] {
	const messages: Message[] = [];
	for (const r of rows) {
		const role = ROLE[String(r.role)];
		if (role && typeof r.text === "string" && r.text) {
			messages.push({
				id: messages.length + 1,
				role,
				text: r.text,
				state: "done",
			});
		}
	}
	return messages;
}

/** A lazy session.create carries no provider until the agent is built; it arrives in session.info. */
export function mainAgentOf(
	info: Record<string, unknown> | null | undefined,
): MainAgent | null {
	if (typeof info?.provider !== "string" || !info.provider) return null;
	return {
		provider: info.provider,
		model: typeof info.model === "string" ? info.model : null,
	};
}
