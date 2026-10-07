import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * The EStiri palette is declared in OKLCH and shared across projects. This test reads the tokens from
 * styles.css as the browser sees them and computes the WCAG 2 contrast of every pair that carries text, in
 * both themes. A tone that moves and drops below AA turns the suite red before it reaches a page.
 */
// Copy of estiri-design/contraste.test.ts, with the selectors scoped to .ohx (see styles.css).
const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

/** `--x: value;` declarations of the first block that opens with `selector {`. */
function tokens(selector: string): Record<string, string> {
	const start = css.indexOf(`${selector} {`);
	if (start === -1) throw new Error(`no ${selector} block`);
	const body = css.slice(start, css.indexOf("}", start));
	return Object.fromEntries(
		[...body.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [
			m[1],
			m[2].trim(),
		]),
	);
}

/** oklch(L% C H) to linear sRGB, with Ottosson's matrix. */
function linearFromOklch(value: string): [number, number, number] {
	const m = value.match(/^oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)$/);
	if (!m) throw new Error(`not oklch: ${value}`);
	const L = Number(m[1]) / 100;
	const h = (Number(m[3]) * Math.PI) / 180;
	const a = Number(m[2]) * Math.cos(h);
	const b = Number(m[2]) * Math.sin(h);
	const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
	const clip = (x: number) => Math.min(1, Math.max(0, x));
	return [
		clip(4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s),
		clip(-1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s),
		clip(-0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s),
	];
}

/** Relative luminance. Ottosson's output is already linear, so the sRGB curve does not need undoing
 *  before weighting. */
const luminance = (v: string) => {
	const [r, g, b] = linearFromOklch(v);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

function contrast(a: string, b: string): number {
	const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
	return (x + 0.05) / (y + 0.05);
}

/** Pairs that carry text: [text, background]. Borders go apart, at 3:1. */
const TEXT: [string, string][] = [
	["--ink", "--ground"],
	["--ink", "--surface"],
	["--ink-2", "--ground"],
	["--ink-2", "--surface"],
	["--ink-2", "--raised"],
	["--accent", "--ground"],
	["--accent", "--surface"],
	["--accent-ink", "--accent"],
	["--figure", "--surface"],
	["--pass", "--ground"],
	["--fail", "--ground"],
	// Chat, tree and pause.
	["--ink", "--raised"], // the user's message
	["--ink", "--accent-soft"], // button on hover, selection
	["--pass", "--surface"], // "connected" in the header
	["--fail", "--surface"], // label of the pause sheet
	["--fail", "--raised"], // figure of the exhausted cell
	["--ink-2", "--accent-soft"], // mono labels over the selection
];
const BORDER: [string, string][] = [
	["--rule-strong", "--ground"],
	["--rule-strong", "--surface"],
];

const light = tokens(".ohx");
const dark = { ...light, ...tokens('.ohx[data-theme="dark"]') };

describe("palette contrast (styles.css tokens, scoped to .ohx)", () => {
	it("the computation reproduces the known WCAG extremes", () => {
		expect(contrast("oklch(100% 0 0)", "oklch(0% 0 0)")).toBeCloseTo(21, 1);
		expect(contrast("oklch(50% 0 0)", "oklch(50% 0 0)")).toBeCloseTo(1, 5);
	});

	for (const [theme, t] of [
		["light", light],
		["dark", dark],
	] as const) {
		it(`every text pair passes AA (4.5:1) in the ${theme} theme`, () => {
			for (const [fg, bg] of TEXT) {
				const c = contrast(t[fg], t[bg]);
				expect(
					c,
					`${fg} sobre ${bg}: ${c.toFixed(2)}:1`,
				).toBeGreaterThanOrEqual(4.5);
			}
		});
		it(`control borders pass 3:1 in the ${theme} theme`, () => {
			for (const [fg, bg] of BORDER) {
				const c = contrast(t[fg], t[bg]);
				expect(
					c,
					`${fg} sobre ${bg}: ${c.toFixed(2)}:1`,
				).toBeGreaterThanOrEqual(3);
			}
		});
	}

	it("the dark theme by preference and the one picked by hand are the same palette", () => {
		const start = css.indexOf("@media (prefers-color-scheme: dark)");
		const byPreference = css.slice(
			start,
			css.indexOf("}", css.indexOf("{", css.indexOf("{", start) + 1)),
		);
		for (const [k, v] of Object.entries(tokens('.ohx[data-theme="dark"]'))) {
			expect(
				byPreference,
				`${k} differs between the two dark blocks`,
			).toContain(`${k}: ${v};`);
		}
	});
});
