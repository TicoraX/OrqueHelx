import { describe, expect, it } from "vitest";
import { bloques, tramos } from "../src/markdown";

describe("tramos", () => {
	it("texto plano queda igual", () => {
		expect(tramos("hola mundo")).toEqual([{ t: "texto", v: "hola mundo" }]);
	});

	it("negrita, cursiva y codigo en linea", () => {
		expect(tramos("**Explicacion**: usa `delegar` y *listo*")).toEqual([
			{ t: "negrita", v: "Explicacion" },
			{ t: "texto", v: ": usa " },
			{ t: "codigo", v: "delegar" },
			{ t: "texto", v: " y " },
			{ t: "cursiva", v: "listo" },
		]);
	});

	it("snake_case y aritmetica no se vuelven cursiva", () => {
		expect(tramos("es_bisiesto_rapido y 2 * 3 * 4")).toEqual([
			{ t: "texto", v: "es_bisiesto_rapido y 2 * 3 * 4" },
		]);
	});

	it("dentro del codigo en linea no se interpreta nada", () => {
		expect(tramos("`**a**`")).toEqual([{ t: "codigo", v: "**a**" }]);
	});

	it("solo enlaces http(s); cualquier otro esquema queda como texto", () => {
		expect(tramos("[repo](https://github.com/TicoraX/OrqueHelx)")).toEqual([
			{ t: "enlace", v: "repo", href: "https://github.com/TicoraX/OrqueHelx" },
		]);
		expect(tramos("[x](javascript:alert(1))")).toEqual([
			{ t: "texto", v: "[x](javascript:alert(1))" },
		]);
	});
});

describe("bloques", () => {
	it("parrafos separados por linea en blanco; saltos simples se conservan", () => {
		expect(bloques("uno\ndos\n\ntres")).toEqual([
			{ t: "parrafo", lineas: [tramos("uno"), tramos("dos")] },
			{ t: "parrafo", lineas: [tramos("tres")] },
		]);
	});

	it("bloque de codigo con lenguaje, sin interpretar su contenido", () => {
		expect(
			bloques("antes\n```python\ndef f(a_b): return **a\n```\ndespues"),
		).toEqual([
			{ t: "parrafo", lineas: [tramos("antes")] },
			{ t: "codigo", lenguaje: "python", v: "def f(a_b): return **a" },
			{ t: "parrafo", lineas: [tramos("despues")] },
		]);
	});

	it("un bloque de codigo sin cerrar (streaming) se muestra igual", () => {
		expect(bloques("```\nprint(1)")).toEqual([
			{ t: "codigo", lenguaje: "", v: "print(1)" },
		]);
	});

	it("listas con vinetas y numeradas", () => {
		expect(bloques("- uno\n* **dos**\n\n1. a\n2. b")).toEqual([
			{
				t: "lista",
				ordenada: false,
				items: [tramos("uno"), tramos("**dos**")],
			},
			{ t: "lista", ordenada: true, items: [tramos("a"), tramos("b")] },
		]);
	});

	it("un titulo se vuelve una linea destacada", () => {
		expect(bloques("## Resumen\ntexto")).toEqual([
			{ t: "titulo", tramos: tramos("Resumen") },
			{ t: "parrafo", lineas: [tramos("texto")] },
		]);
	});
});
