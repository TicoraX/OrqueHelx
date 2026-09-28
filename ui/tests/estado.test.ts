import { describe, expect, it } from "vitest";
import {
	etiquetaPrincipal,
	hora,
	nombreModelo,
	type RutaEstado,
	resumen,
	uso,
	ventana,
} from "../src/estado";

const AHORA = new Date("2026-09-25T15:00:00");
const ruta = (parcial: Partial<RutaEstado>): RutaEstado => ({
	nombre: "claude",
	proveedor: "claude-subscription-directsdk-experimental",
	modelo: "claude-haiku-4-5",
	acp: false,
	medicion: { estado: "sin_dato", error: null, ventanas: [] },
	agotada: null,
	...parcial,
});

describe("uso", () => {
	it.each([
		// Espacio no separable: el numero nunca queda en otra linea que su %.
		[56, "56 %"],
		[99.6, "99.6 %"],
		[99.96, "99.9 %"],
		[100, "100 %"],
		[null, "sin dato"],
		[Number.NaN, "sin dato"],
	])("%s -> %s", (v, texto) => expect(uso(v as number | null)).toBe(texto));
});

describe("hora", () => {
	it("sin hora dice sin dato", () => {
		expect(hora(null, AHORA)).toBe("sin dato");
		expect(hora("no-es-fecha", AHORA)).toBe("sin dato");
	});
	it("otro dia agrega la fecha", () =>
		expect(hora("2026-09-28T18:59:00", AHORA)).toContain("28/09"));
	it("hoy muestra solo la hora", () =>
		expect(hora("2026-09-25T17:19:00", AHORA)).toBe("17:19"));
});

describe("resumen", () => {
	it("la cifra principal es la ventana mas cargada", () => {
		const r = resumen(
			ruta({
				medicion: {
					estado: "medida",
					error: null,
					ventanas: [
						{
							etiqueta: "Current session",
							usado: 56,
							reinicio: "2026-09-25T17:19:00",
						},
						{
							etiqueta: "Current week",
							usado: 36,
							reinicio: "2026-09-28T18:59:00",
						},
					],
				},
			}),
			AHORA,
		);
		// La cifra dice que es consumo, no saldo; las ventanas conocidas de Claude salen en espanol.
		expect(r).toMatchObject({ tono: "normal", cifra: "56 % usado" });
		expect(r.lineas).toEqual([
			"sesión 56 % · reinicia 17:19",
			"semana 36 % · reinicia 28/09 18:59",
		]);
	});

	it("una ventana de familia conserva la familia y una desconocida sale tal cual", () => {
		expect(ventana("Current week (Opus)")).toBe("semana (Opus)");
		expect(ventana("5h")).toBe("5h");
	});

	it("agotada muestra cuando vuelve como cifra principal", () => {
		const r = resumen(
			ruta({
				agotada: {
					desde: AHORA.getTime() / 1000,
					reinicio: "2026-09-25T17:19:00",
				},
			}),
			AHORA,
		);
		expect(r).toMatchObject({ tono: "agotada", cifra: "vuelve 17:19" });
	});

	it("agotada sin reinicio medido no inventa hora", () => {
		const r = resumen(
			ruta({ agotada: { desde: AHORA.getTime() / 1000, reinicio: null } }),
			AHORA,
		);
		expect(r.cifra).toBe("vuelve sin dato");
	});

	it("sin medidor y con error dicen sin dato y la causa", () => {
		expect(resumen(ruta({}), AHORA)).toMatchObject({
			tono: "sin_dato",
			cifra: "sin dato",
		});
		const e = resumen(
			ruta({ medicion: { estado: "error", error: "sin red", ventanas: [] } }),
			AHORA,
		);
		expect(e).toMatchObject({
			tono: "error",
			cifra: "sin dato",
			lineas: ["no se pudo medir: sin red"],
		});
	});
});

describe("quien responde", () => {
	it("un solo nombre por modelo: sin el sufijo de fecha", () => {
		expect(nombreModelo("claude-haiku-4-5-20251001")).toBe("claude-haiku-4-5");
		expect(nombreModelo("flash")).toBe("flash");
		expect(nombreModelo(null)).toBeNull();
	});

	it("nombra al principal por ruta y modelo; sin ruta que lo cubra, por proveedor", () => {
		const rutas = [
			ruta({}),
			ruta({
				nombre: "agy",
				proveedor: "antigravity-subscription-directsdk",
				modelo: "flash",
			}),
		];
		expect(
			etiquetaPrincipal(
				{
					proveedor: "claude-subscription-directsdk-experimental",
					modelo: "claude-haiku-4-5-20251001",
				},
				rutas,
			),
		).toBe("claude · claude-haiku-4-5");
		expect(
			etiquetaPrincipal(
				{ proveedor: "openai-codex", modelo: "gpt-5.5" },
				rutas,
			),
		).toBe("openai-codex · gpt-5.5");
		expect(etiquetaPrincipal({ proveedor: null, modelo: "x" }, rutas)).toBe(
			"x",
		);
	});
});
