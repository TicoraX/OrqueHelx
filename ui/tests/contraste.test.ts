import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * La paleta EStiri se declara en OKLCH y se comparte entre proyectos. Esta
 * prueba lee los tokens de global.css tal como los ve el navegador y calcula el
 * contraste WCAG 2 de cada par que lleva texto, en los dos temas. Un tono que se
 * mueve y baja de AA pone la suite en rojo antes de que llegue a una pagina.
 */
// Copia de estiri-design/contraste.test.ts, con los selectores acotados a .ohx (ver estilos.css).
const css = readFileSync(
	new URL("../src/estilos.css", import.meta.url),
	"utf8",
);

/** Declaraciones `--x: valor;` del primer bloque que abre con `selector {`. */
function tokens(selector: string): Record<string, string> {
	const inicio = css.indexOf(`${selector} {`);
	if (inicio === -1) throw new Error(`no hay bloque ${selector}`);
	const cuerpo = css.slice(inicio, css.indexOf("}", inicio));
	return Object.fromEntries(
		[...cuerpo.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((m) => [
			m[1],
			m[2].trim(),
		]),
	);
}

/** oklch(L% C H) a sRGB lineal, con la matriz de Ottosson. */
function linealDesdeOklch(valor: string): [number, number, number] {
	const m = valor.match(/^oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)$/);
	if (!m) throw new Error(`no es oklch: ${valor}`);
	const L = Number(m[1]) / 100;
	const h = (Number(m[3]) * Math.PI) / 180;
	const a = Number(m[2]) * Math.cos(h);
	const b = Number(m[2]) * Math.sin(h);
	const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const mm = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
	const recorte = (x: number) => Math.min(1, Math.max(0, x));
	return [
		recorte(4.0767416621 * l - 3.3077115913 * mm + 0.2309699292 * s),
		recorte(-1.2684380046 * l + 2.6097574011 * mm - 0.3413193965 * s),
		recorte(-0.0041960863 * l - 0.7034186147 * mm + 1.707614701 * s),
	];
}

/** Luminancia relativa. La salida de Ottosson ya es lineal, asi que no hay que
 *  deshacer la curva de sRGB antes de ponderar. */
const luminancia = (v: string) => {
	const [r, g, b] = linealDesdeOklch(v);
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

function contraste(a: string, b: string): number {
	const [x, y] = [luminancia(a), luminancia(b)].sort((p, q) => q - p);
	return (x + 0.05) / (y + 0.05);
}

/** Pares que llevan texto: [texto, fondo]. Los bordes van aparte, a 3:1. */
const TEXTO: [string, string][] = [
	["--ink", "--ground"],
	["--ink", "--surface"],
	["--ink-2", "--ground"],
	["--ink-2", "--surface"],
	["--ink-2", "--raised"],
	["--accent", "--ground"],
	["--accent", "--surface"],
	["--accent-ink", "--accent"],
	["--cifra", "--surface"],
	["--pass", "--ground"],
	["--fail", "--ground"],
];
const BORDE: [string, string][] = [
	["--rule-strong", "--ground"],
	["--rule-strong", "--surface"],
];

const claro = tokens(".ohx");
const oscuro = { ...claro, ...tokens('.ohx[data-theme="dark"]') };

describe("contraste de la paleta (tokens de estilos.css, acotados a .ohx)", () => {
	it("el calculo reproduce los extremos conocidos de WCAG", () => {
		expect(contraste("oklch(100% 0 0)", "oklch(0% 0 0)")).toBeCloseTo(21, 1);
		expect(contraste("oklch(50% 0 0)", "oklch(50% 0 0)")).toBeCloseTo(1, 5);
	});

	for (const [tema, t] of [
		["claro", claro],
		["oscuro", oscuro],
	] as const) {
		it(`todo par de texto pasa AA (4.5:1) en tema ${tema}`, () => {
			for (const [fg, bg] of TEXTO) {
				const c = contraste(t[fg], t[bg]);
				expect(
					c,
					`${fg} sobre ${bg}: ${c.toFixed(2)}:1`,
				).toBeGreaterThanOrEqual(4.5);
			}
		});
		it(`los bordes de control pasan 3:1 en tema ${tema}`, () => {
			for (const [fg, bg] of BORDE) {
				const c = contraste(t[fg], t[bg]);
				expect(
					c,
					`${fg} sobre ${bg}: ${c.toFixed(2)}:1`,
				).toBeGreaterThanOrEqual(3);
			}
		});
	}

	it("el tema oscuro por preferencia y el elegido a mano son la misma paleta", () => {
		const inicio = css.indexOf("@media (prefers-color-scheme: dark)");
		const porPreferencia = css.slice(
			inicio,
			css.indexOf("}", css.indexOf("{", css.indexOf("{", inicio) + 1)),
		);
		for (const [k, v] of Object.entries(tokens('.ohx[data-theme="dark"]'))) {
			expect(
				porPreferencia,
				`${k} difiere entre los dos bloques oscuros`,
			).toContain(`${k}: ${v};`);
		}
	});
});
