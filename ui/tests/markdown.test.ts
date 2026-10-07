import { describe, expect, it } from "vitest";
import { blocks, plain, spans } from "../src/markdown";

describe("plain", () => {
	it("removes marks nested inside bold and italics", () => {
		expect(plain("**use `delegate_to`** and *done*\n\n- one\n- two")).toBe(
			"use delegate_to and done one; two",
		);
	});
});

describe("spans", () => {
	it("plain text stays the same", () => {
		expect(spans("hello world")).toEqual([{ t: "text", v: "hello world" }]);
	});

	it("bold, italics and inline code", () => {
		expect(spans("**Explanation**: use `delegate_to` and *done*")).toEqual([
			{ t: "bold", v: "Explanation" },
			{ t: "text", v: ": use " },
			{ t: "code", v: "delegate_to" },
			{ t: "text", v: " and " },
			{ t: "italic", v: "done" },
		]);
	});

	it("snake_case and arithmetic do not become italics", () => {
		expect(spans("is_leap_year_fast and 2 * 3 * 4")).toEqual([
			{ t: "text", v: "is_leap_year_fast and 2 * 3 * 4" },
		]);
	});

	it("nothing is interpreted inside inline code", () => {
		expect(spans("`**a**`")).toEqual([{ t: "code", v: "**a**" }]);
	});

	it("only http(s) links; any other scheme stays text", () => {
		expect(spans("[repo](https://github.com/TicoraX/OrqueHelx)")).toEqual([
			{ t: "link", v: "repo", href: "https://github.com/TicoraX/OrqueHelx" },
		]);
		expect(spans("[x](javascript:alert(1))")).toEqual([
			{ t: "text", v: "[x](javascript:alert(1))" },
		]);
	});
});

describe("blocks", () => {
	it("paragraphs split by a blank line; single breaks are kept", () => {
		expect(blocks("one\ntwo\n\nthree")).toEqual([
			{ t: "paragraph", lines: [spans("one"), spans("two")] },
			{ t: "paragraph", lines: [spans("three")] },
		]);
	});

	it("code block with language, its content not interpreted", () => {
		expect(
			blocks("before\n```python\ndef f(a_b): return **a\n```\nafter"),
		).toEqual([
			{ t: "paragraph", lines: [spans("before")] },
			{ t: "code", language: "python", v: "def f(a_b): return **a" },
			{ t: "paragraph", lines: [spans("after")] },
		]);
	});

	it("an unclosed code block (streaming) shows anyway", () => {
		expect(blocks("```\nprint(1)")).toEqual([
			{ t: "code", language: "", v: "print(1)" },
		]);
	});

	it("bulleted and numbered lists", () => {
		expect(blocks("- one\n* **two**\n\n1. a\n2. b")).toEqual([
			{
				t: "list",
				ordered: false,
				start: 1,
				items: [spans("one"), spans("**two**")],
			},
			{ t: "list", ordered: true, start: 1, items: [spans("a"), spans("b")] },
		]);
	});

	it("a numbered list cut by a paragraph keeps its numbering", () => {
		expect(blocks("1. a\n\nnote\n\n3. c")[2]).toEqual({
			t: "list",
			ordered: true,
			start: 3,
			items: [spans("c")],
		});
	});

	it("a heading becomes a highlighted line", () => {
		expect(blocks("## Summary\ntext")).toEqual([
			{ t: "heading", spans: spans("Summary") },
			{ t: "paragraph", lines: [spans("text")] },
		]);
	});
});
