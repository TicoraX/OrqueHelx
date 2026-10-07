import { afterEach, describe, expect, it } from "vitest";
import { language, setLanguage, TEXTS, t } from "../src/texts";

const placeholders = (s: string) =>
	[...s.matchAll(/\{(\w+)\}/gu)].map((m) => m[1]).sort();

afterEach(() => setLanguage("en"));

describe("texts", () => {
	it("both languages have the same keys and the same placeholders", () => {
		expect(Object.keys(TEXTS.es).sort()).toEqual(Object.keys(TEXTS.en).sort());
		for (const [id, text] of Object.entries(TEXTS.en))
			expect(placeholders(TEXTS.es[id as keyof typeof TEXTS.es]), id).toEqual(
				placeholders(text),
			);
	});

	it.each([
		["es", "es"],
		["es-419", "es"],
		["ES-mx", "es"],
		["en", "en"],
		["fr", "en"],
		[null, "en"],
		[undefined, "en"],
	])("locale %s -> %s", (locale, lang) => {
		setLanguage(locale);
		expect(language()).toBe(lang);
	});

	it("fills placeholders and leaves unknown ones visible", () => {
		expect(t("delegating_to", { route: "agy" })).toBe("delegating to agy");
		expect(t("delegating_to")).toBe("delegating to {route}");
	});
});
