// The conversation's subagent tree, built from the gateway's events (pure reducer).
//
//   tool.start delegate_to{route} -> pending (the route only travels here: subagent.* reports the parent's model)
//   subagent.start depth 0        -> node with the route of the oldest unassigned pending delegation
//   subagent.thinking/tool        -> activity
//   subagent.complete             -> final state in place
//   tool.complete delegate_to     -> if no subagent started: out_of_quota or rejection, node with that state

import { plain } from "./markdown";
import { clock, type Summary, type Tone, unbroken } from "./status";
import { language, type TextId, t } from "./texts";

/** Name of the plugin's tool (orquehelx/delegate.py). */
export const TOOL = "delegate_to";

type SubagentState =
	| "running"
	| "completed"
	| "interrupted"
	| "failed"
	| "out_of_quota";

export interface Subagent {
	/** Hermes' subagent_id, or the delegate_to tool_id if it never started. */
	id: string;
	parent: string | null;
	/** null for grandchildren: their delegate_to call happens inside the child and never reaches this session. */
	route: string | null;
	goal: string;
	state: SubagentState;
	activity: string | null;
	summary: string | null;
	duration: number | null;
	/** Only with out_of_quota: ISO of the reset, or null if the provider does not report it. */
	resetAt: string | null;
}

interface Pending {
	toolId: string;
	route: string;
	goal: string;
	subagent: string | null;
}

export interface Tree {
	nodes: Subagent[];
	pending: Pending[];
}

export const empty: Tree = { nodes: [], pending: [] };

type Payload = Record<string, unknown>;

const FINAL: Record<string, SubagentState> = {
	completed: "completed",
	interrupted: "interrupted",
};

const text = (v: unknown): string | null =>
	typeof v === "string" && v ? v : null;

function changeNode(
	tree: Tree,
	id: unknown,
	change: (n: Subagent) => Subagent,
): Tree {
	if (!tree.nodes.some((n) => n.id === id)) return tree;
	return {
		...tree,
		nodes: tree.nodes.map((n) => (n.id === id ? change(n) : n)),
	};
}

function start(tree: Tree, p: Payload): Tree {
	const id = text(p.subagent_id);
	if (!id || tree.nodes.some((n) => n.id === id)) return tree;
	const parent = text(p.parent_id);
	const i = parent ? -1 : tree.pending.findIndex((d) => d.subagent === null);
	const pending = i >= 0 ? tree.pending[i] : null;
	const node: Subagent = {
		id,
		parent,
		route: pending?.route ?? null,
		goal: text(p.goal) ?? pending?.goal ?? "",
		state: "running",
		activity: null,
		summary: null,
		duration: null,
		resetAt: null,
	};
	return {
		nodes: [...tree.nodes, node],
		pending: pending
			? tree.pending.map((d, j) => (j === i ? { ...d, subagent: id } : d))
			: tree.pending,
	};
}

function delegationEnd(tree: Tree, p: Payload): Tree {
	const pending = tree.pending.find((d) => d.toolId === p.tool_id);
	if (!pending) return tree;
	const rest = tree.pending.filter((d) => d !== pending);
	if (pending.subagent !== null) return { ...tree, pending: rest };
	// Hermes already parsed delegate_to's JSON; a result that is not an object carries no status or reason.
	const r = (
		p.result && typeof p.result === "object" ? p.result : {}
	) as Payload;
	const outOfQuota = r.status === "out_of_quota";
	const node: Subagent = {
		id: pending.toolId,
		parent: null,
		route: pending.route,
		goal: pending.goal,
		state: outOfQuota ? "out_of_quota" : "failed",
		activity: null,
		summary: outOfQuota ? null : text(r.error),
		duration: null,
		resetAt: outOfQuota ? text(r.reset_at) : null,
	};
	return { nodes: [...tree.nodes, node], pending: rest };
}

export function reduceTree(tree: Tree, event: string, payload: unknown): Tree {
	const p = (payload && typeof payload === "object" ? payload : {}) as Payload;
	switch (event) {
		case "tool.start": {
			const args = (p.args ?? {}) as Payload;
			const toolId = text(p.tool_id);
			const route = text(args.route);
			if (p.name !== TOOL || !toolId || !route) return tree;
			const pending = {
				toolId,
				route,
				goal: text(args.goal) ?? "",
				subagent: null,
			};
			return { ...tree, pending: [...tree.pending, pending] };
		}
		case "tool.complete":
			return p.name === TOOL ? delegationEnd(tree, p) : tree;
		case "subagent.start":
			return start(tree, p);
		case "subagent.thinking":
			return changeNode(tree, p.subagent_id, (n) => ({
				...n,
				activity: t("thinking"),
			}));
		case "subagent.tool":
			return changeNode(tree, p.subagent_id, (n) => ({
				...n,
				activity: t("using_tool", { tool: text(p.tool_name) ?? t("a_tool") }),
			}));
		case "subagent.complete":
			return changeNode(tree, p.subagent_id, (n) => ({
				...n,
				state: FINAL[String(p.status)] ?? "failed",
				activity: null,
				summary: text(p.summary),
				duration:
					typeof p.duration_seconds === "number" ? p.duration_seconds : null,
			}));
		default:
			return tree;
	}
}

const FIGURE: Record<SubagentState, [Tone, TextId]> = {
	running: ["normal", "running"],
	completed: ["normal", "completed"],
	interrupted: ["no_data", "note_stopped"],
	failed: ["error", "failed"],
	out_of_quota: ["exhausted", "out_of_quota"],
};

/** What a subagent's cell shows: same format as a route's. */
export function card(n: Subagent, now = new Date()): Summary {
	const [tone, id] = FIGURE[n.state];
	const figure = t(id);
	if (n.state === "out_of_quota")
		return {
			tone,
			figure,
			lines: [
				unbroken(
					n.resetAt
						? t("resets", { time: clock(n.resetAt, now) })
						: t("resets_unknown"),
				),
			],
		};
	// The summary comes in markdown; the cell clips it to 4 lines, so it goes without marks.
	const lines =
		n.state === "running"
			? [n.activity]
			: [n.summary === null ? null : plain(n.summary)];
	if (n.duration !== null) {
		const value = String(Math.round(n.duration * 10) / 10);
		lines.push(
			t("seconds", {
				value: language() === "es" ? value.replace(".", ",") : value,
			}),
		);
	}
	return { tone, figure, lines: lines.filter((l): l is string => l !== null) };
}

/** Depth-first walk with each node's level; a child with an unknown parent goes to the root. */
export function inOrder(nodes: Subagent[]): [Subagent, number][] {
	const ids = new Set(nodes.map((n) => n.id));
	const out: [Subagent, number][] = [];
	const visit = (parent: string | null, level: number) => {
		for (const n of nodes) {
			const root = n.parent === null || !ids.has(n.parent);
			if (parent === null ? root : n.parent === parent) {
				out.push([n, level]);
				visit(n.id, level + 1);
			}
		}
	};
	visit(null, 0);
	return out;
}
