import { beforeEach, describe, expect, it } from "vitest";
import {
	type Chat,
	initial,
	mainAgentOf,
	type Pause,
	reduce,
} from "../src/chat";
import { setLanguage } from "../src/texts";

const steps = (...actions: Parameters<typeof reduce>[1][]): Chat =>
	actions.reduce(reduce, initial);

beforeEach(() => setLanguage("en"));

describe("chat", () => {
	it("sending adds the user's message and leaves the turn waiting", () => {
		const c = steps({ type: "sent", text: "hello" });
		expect(c.messages).toEqual([
			{ id: 1, role: "user", text: "hello", state: "done" },
		]);
		expect(c.turn).toBe("waiting");
	});

	it("the answer arrives in deltas and closes with message.complete", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{ type: "event", event: "message.start", payload: null },
			{ type: "event", event: "message.delta", payload: { text: "He" } },
			{ type: "event", event: "message.delta", payload: { text: "llo" } },
		);
		expect(c.messages[1]).toMatchObject({
			role: "agent",
			text: "Hello",
			state: "writing",
		});
		expect(c.turn).toBe("responding");
		const end = reduce(c, {
			type: "event",
			event: "message.complete",
			payload: { text: "Hello.", status: "complete" },
		});
		expect(end.messages[1]).toMatchObject({ text: "Hello.", state: "done" });
		expect(end.turn).toBe("idle");
	});

	it("without deltas (claude-subscription) the text arrives whole in message.complete", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{ type: "event", event: "message.start", payload: null },
			{
				type: "event",
				event: "message.complete",
				payload: { text: "Full answer", status: "complete" },
			},
		);
		expect(c.messages[1]).toMatchObject({ text: "Full answer", state: "done" });
	});

	it("message.complete without message.start still creates the answer", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{
				type: "event",
				event: "message.complete",
				payload: { text: "ok", status: "complete" },
			},
		);
		expect(c.messages.map((m) => m.role)).toEqual(["user", "agent"]);
	});

	it("activity shows what the agent does, without the thinking filler text", () => {
		let c = steps(
			{ type: "sent", text: "x" },
			{ type: "event", event: "message.start", payload: null },
		);
		c = reduce(c, {
			type: "event",
			event: "thinking.delta",
			payload: { text: "( •_•)>⌐■-■ synthesizing..." },
		});
		expect(c.activity).toBe("thinking");
		c = reduce(c, {
			type: "event",
			event: "tool.start",
			payload: { name: "delegate_to", args: { route: "opencode" } },
		});
		expect(c.activity).toBe("delegating to opencode");
		c = reduce(c, {
			type: "event",
			event: "tool.start",
			payload: { name: "terminal", args: {} },
		});
		expect(c.activity).toBe("using terminal");
		c = reduce(c, {
			type: "event",
			event: "tool.complete",
			payload: { name: "terminal" },
		});
		expect(c.activity).toBeNull();
	});

	it("activity follows the dashboard language", () => {
		setLanguage("es-419");
		const c = steps({
			type: "event",
			event: "tool.start",
			payload: { name: "delegate_to", args: { route: "agy" } },
		});
		expect(c.activity).toBe("delegando en agy");
	});

	it("an interrupted or failed turn is marked and frees the turn", () => {
		const base = steps(
			{ type: "sent", text: "x" },
			{ type: "event", event: "message.start", payload: null },
		);
		const cut = reduce(base, {
			type: "event",
			event: "message.complete",
			payload: { text: "", status: "interrupted" },
		});
		expect(cut.messages[1].state).toBe("interrupted");
		expect(cut.turn).toBe("idle");
		const broken = reduce(base, {
			type: "event",
			event: "message.complete",
			payload: { text: "", status: "error" },
		});
		expect(broken.messages[1].state).toBe("error");
	});

	it("a failed send leaves the error visible and the text to retry", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{ type: "failure", message: "gateway offline" },
		);
		expect(c.error).toEqual({ message: "gateway offline", text: "hello" });
		expect(c.turn).toBe("idle");
		expect(c.messages[0].state).toBe("error");
	});

	it("unknown events change nothing", () => {
		const c = steps({ type: "sent", text: "x" });
		expect(
			reduce(c, { type: "event", event: "session.usage", payload: {} }),
		).toBe(c);
	});

	it("a new conversation goes back to the initial state", () => {
		expect(steps({ type: "sent", text: "x" }, { type: "new" })).toEqual(
			initial,
		);
	});

	it("a failure with a half answer does not offer to resend the agent's text", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{ type: "event", event: "message.delta", payload: { text: "He" } },
			{ type: "failure", message: "gateway offline" },
		);
		expect(c.error).toEqual({ message: "gateway offline", text: null });
		expect(c.messages[0].state).toBe("done");
	});

	it("subagent events build the tree and a new conversation empties it", () => {
		const c = steps(
			{ type: "sent", text: "delegate" },
			{
				type: "event",
				event: "tool.start",
				payload: {
					tool_id: "t1",
					name: "delegate_to",
					args: { route: "opencode", goal: "x" },
				},
			},
			{
				type: "event",
				event: "subagent.start",
				payload: { subagent_id: "a", goal: "x", depth: 0 },
			},
		);
		expect(c.subagents.nodes.map((n) => [n.id, n.route, n.state])).toEqual([
			["a", "opencode", "running"],
		]);
		expect(c.activity).toBe("delegating to opencode");
		expect(reduce(c, { type: "new" })).toEqual(initial);
	});

	const PAUSE: Pause = {
		id: 7,
		session: "20260926_190000_ab12",
		provider: "claude-subscription-directsdk-experimental",
		model: "claude-haiku-4-5",
		reset_at: "2026-09-26T19:00:00+00:00",
		state: "paused",
		created: 1790000000,
	};

	it("a pause leaves the chat still with the pause in view", () => {
		const c = steps(
			{ type: "sent", text: "hello" },
			{
				type: "event",
				event: "message.complete",
				payload: { text: "Usage limit reached", status: "error" },
			},
			{ type: "paused", pause: PAUSE },
		);
		expect(c.turn).toBe("paused");
		expect(c.pause).toEqual(PAUSE);
		expect(c.activity).toBeNull();
	});

	it("resume or resend clears the pause and waits for the answer; cancel frees the turn", () => {
		const paused = steps(
			{ type: "sent", text: "hello" },
			{ type: "paused", pause: PAUSE },
		);
		expect(reduce(paused, { type: "resuming" })).toMatchObject({
			turn: "waiting",
			pause: null,
		});
		expect(reduce(paused, { type: "cancelled" })).toMatchObject({
			turn: "idle",
			pause: null,
		});
	});

	it("if another tab already resolved the pause, it says so and frees the turn", () => {
		const c = reduce(
			steps(
				{ type: "sent", text: "hello" },
				{
					type: "event",
					event: "message.complete",
					payload: { text: "Usage limit", status: "error" },
				},
				{ type: "paused", pause: PAUSE },
			),
			{ type: "failure", message: "already resolved" },
		);
		expect(c).toMatchObject({ turn: "idle", pause: null });
		expect(c.error?.message).toBe("already resolved");
	});

	it("reattaching a stored session loads its visible history", () => {
		const c = reduce(initial, {
			type: "history",
			messages: [
				{ role: "user", text: "hello" },
				{ role: "tool", text: "{}" },
				{ role: "assistant", text: "" },
				{ role: "assistant", text: "Usage limit reached" },
			],
		});
		expect(c.messages).toEqual([
			{ id: 1, role: "user", text: "hello", state: "done" },
			{ id: 2, role: "agent", text: "Usage limit reached", state: "done" },
		]);
	});
});

describe("the session's main agent", () => {
	it("a lazy session.create carries no provider; session.info does (real gateway frames)", () => {
		expect(
			mainAgentOf({ model: "model-that-does-not-exist", lazy: true }),
		).toBeNull();
		expect(
			mainAgentOf({
				model: "model-that-does-not-exist",
				provider: "antigravity-subscription-directsdk",
			}),
		).toEqual({
			provider: "antigravity-subscription-directsdk",
			model: "model-that-does-not-exist",
		});
		expect(mainAgentOf(null)).toBeNull();
		expect(mainAgentOf({ provider: "" })).toBeNull();
	});
});

describe("who answers", () => {
	const HAIKU = {
		provider: "claude-subscription-directsdk-experimental",
		model: "claude-haiku-4-5",
	};

	it("each agent answer carries the model of the main agent that gave it", () => {
		const c = steps(
			{ type: "main", main: HAIKU },
			{ type: "sent", text: "hello" },
			{
				type: "event",
				event: "message.complete",
				payload: { text: "hello", status: "complete" },
			},
		);
		expect(c.main).toEqual(HAIKU);
		expect(c.messages[1]).toMatchObject({
			role: "agent",
			model: "claude-haiku-4-5",
		});
	});

	it("a resend leaves a note in the thread", () => {
		const c = steps({
			type: "note",
			text: "turn resent from agy to claude · claude-haiku-4-5",
		});
		expect(c.messages).toEqual([
			{
				id: 1,
				role: "note",
				text: "turn resent from agy to claude · claude-haiku-4-5",
				state: "done",
			},
		]);
	});

	it("on pause, the answer that failed for quota is marked as error even if it comes from the history", () => {
		const c = steps(
			{
				type: "history",
				messages: [
					{ role: "user", text: "hello" },
					{ role: "assistant", text: "Your request was not processed." },
				],
			},
			{
				type: "paused",
				pause: {
					id: 1,
					session: "s",
					provider: "p",
					model: null,
					reset_at: null,
					state: "paused",
					created: 0,
				},
			},
		);
		expect(c.messages[1].state).toBe("error");
	});
});
