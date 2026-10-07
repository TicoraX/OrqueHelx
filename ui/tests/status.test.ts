import { beforeEach, describe, expect, it } from "vitest";
import {
	clock,
	mainAgentLabel,
	modelName,
	type RouteStatus,
	summary,
	usage,
	windowLabel,
} from "../src/status";
import { setLanguage } from "../src/texts";

const NOW = new Date("2026-09-25T15:00:00");
const route = (part: Partial<RouteStatus>): RouteStatus => ({
	name: "claude",
	provider: "claude-subscription-directsdk-experimental",
	model: "claude-haiku-4-5",
	acp: false,
	measurement: { state: "no_data", error: null, windows: [] },
	exhausted: null,
	...part,
});
// No-break space: the number never ends up on another line than its %, nor "resets" away from its time.
const nb = (s: string) => s.replaceAll(" ", " ");

beforeEach(() => setLanguage("en"));

describe("usage", () => {
	it.each([
		[56, nb("56 %")],
		[99.6, nb("99.6 %")],
		[99.96, nb("99.9 %")],
		[100, nb("100 %")],
		[null, "no data"],
		[Number.NaN, "no data"],
	])("%s -> %s", (v, text) => expect(usage(v as number | null)).toBe(text));
});

describe("clock", () => {
	it("without a time says no data", () => {
		expect(clock(null, NOW)).toBe("no data");
		expect(clock("not-a-date", NOW)).toBe("no data");
	});
	it("another day adds the date, in each language's order", () => {
		expect(clock("2026-09-28T18:59:00", NOW)).toBe("Sep 28 18:59");
		setLanguage("es");
		expect(clock("2026-09-28T18:59:00", NOW)).toBe("28/09 18:59");
	});
	it("today shows only the time", () =>
		expect(clock("2026-09-25T17:19:00", NOW)).toBe("17:19"));
});

describe("summary", () => {
	const measured = route({
		measurement: {
			state: "measured",
			error: null,
			windows: [
				{ label: "Current session", used: 56, reset_at: "2026-09-25T17:19:00" },
				{ label: "Current week", used: 36, reset_at: "2026-09-28T18:59:00" },
			],
		},
	});

	it("the main figure is the busiest window", () => {
		const s = summary(measured, NOW);
		// The figure says it is consumption, not balance; Claude's known windows get short names.
		expect(s).toMatchObject({ tone: "normal", figure: nb("56 % used") });
		expect(s.lines).toEqual([
			`session ${nb("56 %")} · ${nb("resets at 17:19")}`,
			`week ${nb("36 %")} · ${nb("resets at Sep 28 18:59")}`,
		]);
	});

	it("in Spanish the same figures read in Spanish", () => {
		setLanguage("es-419");
		const s = summary(measured, NOW);
		expect(s.figure).toBe(nb("56 % usado"));
		expect(s.lines[0]).toBe(`sesión ${nb("56 %")} · ${nb("reinicia 17:19")}`);
	});

	it("a family window keeps the family and an unknown one stays as is", () => {
		expect(windowLabel("Current week (Opus)")).toBe("week (Opus)");
		expect(windowLabel("5h")).toBe("5h");
	});

	it("exhausted shows when it comes back as the main figure", () => {
		const s = summary(
			route({
				exhausted: {
					since: NOW.getTime() / 1000,
					reset_at: "2026-09-25T17:19:00",
				},
			}),
			NOW,
		);
		expect(s).toMatchObject({ tone: "exhausted", figure: "back at 17:19" });
	});

	it("exhausted with no measured reset does not invent a time", () => {
		const s = summary(
			route({ exhausted: { since: NOW.getTime() / 1000, reset_at: null } }),
			NOW,
		);
		expect(s.figure).toBe("return time unknown");
	});

	it("no meter and an error say no data and the cause", () => {
		expect(summary(route({}), NOW)).toMatchObject({
			tone: "no_data",
			figure: "no data",
		});
		const e = summary(
			route({
				measurement: { state: "error", error: "no network", windows: [] },
			}),
			NOW,
		);
		expect(e).toMatchObject({
			tone: "error",
			figure: "no data",
			lines: ["could not measure: no network"],
		});
	});
});

describe("who answers", () => {
	it("one name per model: without the date suffix", () => {
		expect(modelName("claude-haiku-4-5-20251001")).toBe("claude-haiku-4-5");
		expect(modelName("flash")).toBe("flash");
		expect(modelName(null)).toBeNull();
	});

	it("names the main agent by route and model; with no route covering it, by provider", () => {
		const routes = [
			route({}),
			route({
				name: "agy",
				provider: "antigravity-subscription-directsdk",
				model: "flash",
			}),
		];
		expect(
			mainAgentLabel(
				{
					provider: "claude-subscription-directsdk-experimental",
					model: "claude-haiku-4-5-20251001",
				},
				routes,
			),
		).toBe("claude · claude-haiku-4-5");
		expect(
			mainAgentLabel({ provider: "openai-codex", model: "gpt-5.5" }, routes),
		).toBe("openai-codex · gpt-5.5");
		expect(mainAgentLabel({ provider: null, model: "x" }, routes)).toBe("x");
	});
});
