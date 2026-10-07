import { beforeEach, describe, expect, it } from "vitest";
import {
	card,
	empty,
	inOrder,
	reduceTree,
	type Subagent,
	type Tree,
} from "../src/subagents";
import { setLanguage } from "../src/texts";

beforeEach(() => setLanguage("en"));

// Payloads trimmed from a real gateway session.
const start = (extra: Record<string, unknown> = {}) => ({
	goal: "Answer only: done",
	subagent_id: "sa-0-b4e3bba3",
	delegation_id: "deleg_8824125b",
	depth: 0,
	model: "claude-haiku-4-5",
	...extra,
});

const steps = (...events: [string, unknown][]): Tree =>
	events.reduce((tree, [type, p]) => reduceTree(tree, type, p), empty);

const delegate = (toolId: string, route: string, goal = "Answer only: done") =>
	[
		"tool.start",
		{ tool_id: toolId, name: "delegate_to", args: { route, goal } },
	] as [string, unknown];

describe("subagent tree", () => {
	it("the route comes from the delegate_to call, not from the model the subagent reports", () => {
		const tree = steps(delegate("t1", "opencode"), ["subagent.start", start()]);
		expect(tree.nodes).toEqual([
			{
				id: "sa-0-b4e3bba3",
				parent: null,
				route: "opencode",
				goal: "Answer only: done",
				state: "running",
				activity: null,
				summary: null,
				duration: null,
				resetAt: null,
			},
		]);
	});

	it("shows that it thinks or which tool it uses, without the filler text", () => {
		let tree = steps(delegate("t1", "opencode"), ["subagent.start", start()]);
		tree = reduceTree(
			tree,
			"subagent.thinking",
			start({ text: "(´･_･`) processing..." }),
		);
		expect(tree.nodes[0].activity).toBe("thinking");
		tree = reduceTree(
			tree,
			"subagent.tool",
			start({ tool_name: "read_file", text: "reading a.py" }),
		);
		expect(tree.nodes[0].activity).toBe("using read_file");
	});

	it("subagent.complete changes the state in place with summary and duration", () => {
		const tree = steps(
			delegate("t1", "opencode"),
			["subagent.start", start()],
			[
				"subagent.complete",
				start({
					status: "completed",
					summary: "done",
					duration_seconds: 22.08,
				}),
			],
		);
		expect(tree.nodes).toHaveLength(1);
		expect(tree.nodes[0]).toMatchObject({
			state: "completed",
			activity: null,
			summary: "done",
			duration: 22.08,
		});
	});

	it("an unknown final state shows as failed, not as completed", () => {
		const tree = steps(
			delegate("t1", "opencode"),
			["subagent.start", start()],
			["subagent.complete", start({ status: "timeout", summary: "" })],
		);
		expect(tree.nodes[0].state).toBe("failed");
		expect(tree.nodes[0].summary).toBeNull();
	});

	it("two delegations are assigned in call order", () => {
		const tree = steps(
			delegate("t1", "opencode", "one"),
			delegate("t2", "agy", "two"),
			["subagent.start", start({ subagent_id: "a", goal: "one" })],
			["subagent.start", start({ subagent_id: "b", goal: "two" })],
		);
		expect(tree.nodes.map((n) => [n.id, n.route])).toEqual([
			["a", "opencode"],
			["b", "agy"],
		]);
	});

	it("a grandchild hangs from its parent and does not take another delegation's route", () => {
		const tree = steps(
			delegate("t1", "opencode"),
			["subagent.start", start({ subagent_id: "a" })],
			[
				"subagent.start",
				start({ subagent_id: "a.1", parent_id: "a", depth: 1, goal: "sub" }),
			],
		);
		expect(tree.nodes[1]).toMatchObject({
			id: "a.1",
			parent: "a",
			route: null,
			goal: "sub",
		});
	});

	it("out_of_quota creates the node without pretending it ran, with the reset time", () => {
		const tree = steps(delegate("t1", "claude"), [
			"tool.complete",
			{
				tool_id: "t1",
				name: "delegate_to",
				result: {
					status: "out_of_quota",
					route: "claude",
					reset_at: "2026-09-26T19:00:00+00:00",
				},
			},
		]);
		expect(tree.nodes).toEqual([
			expect.objectContaining({
				id: "t1",
				route: "claude",
				state: "out_of_quota",
				resetAt: "2026-09-26T19:00:00+00:00",
			}),
		]);
		expect(tree.pending).toEqual([]);
	});

	it("a subagent that started and failed for quota ends as out_of_quota, not failed", () => {
		// Seen live: the child starts, fails (subagent.complete failed), and delegate_to then answers out_of_quota.
		const tree = steps(
			delegate("t1", "agy"),
			["subagent.start", start()],
			[
				"subagent.complete",
				start({ status: "failed", summary: "Broken pipe" }),
			],
			[
				"tool.complete",
				{
					tool_id: "t1",
					name: "delegate_to",
					result: {
						status: "out_of_quota",
						route: "agy",
						reset_at: "2026-09-26T19:00:00+00:00",
					},
				},
			],
		);
		expect(tree.nodes).toHaveLength(1);
		expect(tree.nodes[0]).toMatchObject({
			state: "out_of_quota",
			resetAt: "2026-09-26T19:00:00+00:00",
		});
		expect(tree.pending).toEqual([]);
	});

	it("a rejected delegation shows the reason", () => {
		const tree = steps(delegate("t1", "claude"), [
			"tool.complete",
			{
				tool_id: "t1",
				name: "delegate_to",
				result: { error: "unknown route 'x'" },
			},
		]);
		expect(tree.nodes[0]).toMatchObject({
			state: "failed",
			summary: "unknown route 'x'",
		});
	});

	it("tool.complete of a delegation that did run does not duplicate the node", () => {
		const tree = steps(
			delegate("t1", "opencode"),
			["subagent.start", start()],
			["subagent.complete", start({ status: "completed", summary: "done" })],
			[
				"tool.complete",
				{
					tool_id: "t1",
					name: "delegate_to",
					result: { results: [{ status: "completed" }] },
				},
			],
		);
		expect(tree.nodes).toHaveLength(1);
		expect(tree.pending).toEqual([]);
	});

	it("ignores other tools and events of unknown subagents", () => {
		const tree = steps(
			["tool.start", { tool_id: "t9", name: "read_file", args: {} }],
			[
				"subagent.complete",
				start({ subagent_id: "nobody", status: "completed" }),
			],
		);
		expect(tree).toEqual(empty);
	});
});

describe("subagent view", () => {
	const node = (extra: Partial<Subagent>): Subagent => ({
		id: "a",
		parent: null,
		route: "opencode",
		goal: "x",
		state: "running",
		activity: null,
		summary: null,
		duration: null,
		resetAt: null,
		...extra,
	});

	it("running shows the activity; finished, the summary and the duration", () => {
		expect(card(node({ activity: "using read_file" }))).toEqual({
			tone: "normal",
			figure: "running",
			lines: ["using read_file"],
		});
		expect(
			card(node({ state: "completed", summary: "done", duration: 22.08 })),
		).toEqual({
			tone: "normal",
			figure: "completed",
			lines: ["done", "22.1 s"],
		});
	});

	it("in Spanish the duration uses a decimal comma", () => {
		setLanguage("es");
		expect(card(node({ state: "completed", duration: 22.08 }))).toMatchObject({
			figure: "completado",
			lines: ["22,1 s"],
		});
	});

	it("the cell's summary is plain text, without markdown marks", () => {
		// A real opencode result: a code fence and inline code.
		const summary =
			"```python\ndef is_leap(year): return year % 4 == 0\n```\n**Gregorian** rule: `is_leap(2024)` → True";
		expect(card(node({ state: "completed", summary })).lines[0]).toBe(
			"def is_leap(year): return year % 4 == 0 Gregorian rule: is_leap(2024) → True",
		);
	});

	it("out of quota carries the exhausted-route tone and the reset time, or says it is unknown", () => {
		const now = new Date(2026, 8, 26, 12, 0);
		const resetAt = new Date(2026, 8, 26, 19, 5).toISOString();
		expect(card(node({ state: "out_of_quota", resetAt }), now)).toEqual({
			tone: "exhausted",
			figure: "out of quota",
			lines: ["resets at 19:05"],
		});
		expect(card(node({ state: "out_of_quota" }), now).lines).toEqual([
			"reset time unknown",
		]);
	});

	it("failed and interrupted do not look like success", () => {
		expect(card(node({ state: "failed", summary: "timeout" }))).toMatchObject({
			tone: "error",
			figure: "failed",
		});
		expect(card(node({ state: "interrupted" }))).toMatchObject({
			tone: "no_data",
			figure: "stopped",
		});
	});

	it("orders depth-first: each child goes under its parent with its level", () => {
		const nodes = [
			node({ id: "a" }),
			node({ id: "b" }),
			node({ id: "a.1", parent: "a" }),
			node({ id: "a.1.1", parent: "a.1" }),
		];
		expect(inOrder(nodes).map(([n, level]) => [n.id, level])).toEqual([
			["a", 0],
			["a.1", 1],
			["a.1.1", 2],
			["b", 0],
		]);
	});

	it("a child whose parent never arrived still shows, at the root", () => {
		expect(
			inOrder([node({ id: "h", parent: "lost" })]).map(([n, level]) => [
				n.id,
				level,
			]),
		).toEqual([["h", 0]]);
	});
});
