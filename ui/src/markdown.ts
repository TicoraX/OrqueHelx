// Markdown in the agent's answers to a plain structure; App.tsx draws it with React elements, never with
// innerHTML, so the model's text is never interpreted as HTML.
// ponytail: a subset (paragraphs, code, lists, headings, bold, italics, http[s] links); tables and quotes
// come out as text. If more is needed, a real parser (marked + sanitizing) costs ~20 KB gzip.

export type Span =
	| { t: "text" | "bold" | "italic" | "code"; v: string }
	| { t: "link"; v: string; href: string };

export type Block =
	| { t: "paragraph"; lines: Span[][] }
	| { t: "code"; language: string; v: string }
	| { t: "list"; ordered: boolean; start: number; items: Span[][] }
	| { t: "heading"; spans: Span[] };

// Order of alternatives = priority: code wins over everything else.
// Italics with _ only between word boundaries, so snake_case stays text.
const INLINE =
	/`([^`]+)`|\*\*([^*]+)\*\*|\*(?!\s)([^*]+?)(?<!\s)\*|(?<!\w)_(?!\s)([^_]+?)(?<!\s)_(?!\w)|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gu;

export function spans(text: string): Span[] {
	const out: Span[] = [];
	let from = 0;
	for (const m of text.matchAll(INLINE)) {
		if (m.index > from) out.push({ t: "text", v: text.slice(from, m.index) });
		if (m[1] !== undefined) out.push({ t: "code", v: m[1] });
		else if (m[2] !== undefined) out.push({ t: "bold", v: m[2] });
		else if (m[3] !== undefined || m[4] !== undefined)
			out.push({ t: "italic", v: (m[3] ?? m[4]) as string });
		else out.push({ t: "link", v: m[5] as string, href: m[6] as string });
		from = m.index + m[0].length;
	}
	if (from < text.length) out.push({ t: "text", v: text.slice(from) });
	return out;
}

const BULLET = /^\s*[-*+]\s+(.*)$/u;
const NUMBER = /^\s*\d+[.)]\s+(.*)$/u;
const HEADING = /^#{1,6}\s+(.*)$/u;
const FENCE = /^\s*```(\S*)/u;
const CLOSE = /^\s*```\s*$/u;

export function blocks(text: string): Block[] {
	const out: Block[] = [];
	const lines = text.split(/\r?\n/u);
	for (let i = 0; i < lines.length; ) {
		const line = lines[i] as string;
		const fence = line.match(FENCE);
		if (fence) {
			// Without a closing fence (an answer still arriving) the rest is code.
			const end = lines.findIndex((l, j) => j > i && CLOSE.test(l));
			const until = end === -1 ? lines.length : end;
			out.push({
				t: "code",
				language: fence[1] ?? "",
				v: lines.slice(i + 1, until).join("\n"),
			});
			i = until + 1;
			continue;
		}
		if (!line.trim()) {
			i++;
			continue;
		}
		const heading = line.match(HEADING);
		if (heading) {
			out.push({ t: "heading", spans: spans(heading[1] as string) });
			i++;
			continue;
		}
		const pattern = [BULLET, NUMBER].find((p) => p.test(line));
		if (pattern) {
			// A numbered list cut by a paragraph keeps its number (<ol start>).
			const start = Number.parseInt(line, 10) || 1;
			const items: Span[][] = [];
			for (; i < lines.length && pattern.test(lines[i] as string); i++)
				items.push(spans((lines[i] as string).match(pattern)?.[1] as string));
			out.push({ t: "list", ordered: pattern === NUMBER, start, items });
			continue;
		}
		const paragraph: Span[][] = [];
		for (; i < lines.length; i++) {
			const l = lines[i] as string;
			if (
				!l.trim() ||
				FENCE.test(l) ||
				HEADING.test(l) ||
				BULLET.test(l) ||
				NUMBER.test(l)
			)
				break;
			paragraph.push(spans(l));
		}
		out.push({ t: "paragraph", lines: paragraph });
	}
	return out;
}

// Bold and italics can carry marks inside (`**use `x`**`): flatten them again.
const flatLine = (ss: Span[]): string =>
	ss
		.map((x) =>
			x.t === "bold" || x.t === "italic" ? flatLine(spans(x.v)) : x.v,
		)
		.join("");

/** The same markdown on a single line of text, without marks: for clipped summaries. */
export function plain(text: string): string {
	return blocks(text)
		.map((b) => {
			if (b.t === "code") return b.v;
			if (b.t === "heading") return flatLine(b.spans);
			if (b.t === "list") return b.items.map(flatLine).join("; ");
			return b.lines.map(flatLine).join(" ");
		})
		.join(" ")
		.replaceAll(/\s+/gu, " ")
		.trim();
}
