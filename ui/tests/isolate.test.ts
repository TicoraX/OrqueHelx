import { describe, expect, it } from "vitest";
import { type DomNode, isolate } from "../src/isolate";

/** Fake node tree: html > (head, body > (app > (nav, main), layer > ohx)). */
function tree() {
	const node = (tagName: string, ...children: DomNode[]): DomNode => {
		const n: DomNode = { tagName, parentElement: null, children, inert: false };
		for (const c of children) c.parentElement = n;
		return n;
	};
	const ohx = node("DIV");
	const nav = node("NAV");
	const main = node("MAIN");
	const app = node("DIV", nav, main);
	const layer = node("DIV", ohx);
	const head = node("HEAD");
	const body = node("BODY", app, layer);
	node("HTML", head, body);
	return { ohx, nav, main, app, layer, head, body };
}

describe("isolate the screen over the dashboard", () => {
	it("makes everything that does not hold the screen inert and restores it on exit", () => {
		const n = tree();
		const release = isolate(n.ohx);
		expect(n.app.inert).toBe(true);
		expect([n.layer.inert, n.ohx.inert, n.body.inert, n.head.inert]).toEqual([
			false,
			false,
			false,
			false,
		]);
		release();
		expect(n.app.inert).toBe(false);
	});

	it("does not wake up what Hermes already had inert", () => {
		const n = tree();
		n.app.inert = true;
		isolate(n.ohx)();
		expect(n.app.inert).toBe(true);
	});
});
