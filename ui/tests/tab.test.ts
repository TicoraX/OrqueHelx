import { describe, expect, it, vi } from "vitest";
import { active, mark, subscribe } from "../src/tab";

describe("active tab", () => {
	it("starts inactive and notifies subscribers on change", () => {
		expect(active()).toBe(false);
		const listener = vi.fn();
		const unsubscribe = subscribe(listener);
		mark(true);
		expect(active()).toBe(true);
		expect(listener).toHaveBeenCalledTimes(1);
		unsubscribe();
		mark(false);
		expect(active()).toBe(false);
		expect(listener).toHaveBeenCalledTimes(1);
	});

	it("marking the same value does not notify", () => {
		const listener = vi.fn();
		subscribe(listener);
		mark(false);
		expect(listener).not.toHaveBeenCalled();
	});
});
