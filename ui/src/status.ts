// Routes status as GET /api/plugins/orquehelx/status returns it, and how it reads on screen.
import { language, t } from "./texts";

export interface QuotaWindow {
	label: string;
	used: number | null;
	reset_at: string | null;
}

export interface Measurement {
	state: "measured" | "no_data" | "error";
	error: string | null;
	windows: QuotaWindow[];
}

export interface RouteStatus {
	name: string;
	provider: string;
	model: string | null;
	acp: boolean;
	measurement: Measurement;
	exhausted: { since: number; reset_at: string | null } | null;
}

export interface RoutesStatus {
	routes: RouteStatus[];
	/** Default model of the main agent in Hermes' config (before the first turn); null if there is none. */
	main: { provider: string | null; model: string | null } | null;
	error: string | null;
}

export type Tone = "normal" | "exhausted" | "error" | "no_data";

export interface Summary {
	tone: Tone;
	/** The cell's main figure: measured percentage, "no data" or the time it comes back. */
	figure: string;
	lines: string[];
}

/** Truncate to one decimal: 99.96 never shows as 100 (exhausted). */
export function usage(used: number | null): string {
	if (used === null || !Number.isFinite(used)) return t("no_data");
	// No-break space: the number never ends up on another line than its %.
	return `${Math.floor(used * 10) / 10} %`;
}

const MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");

/** Local time; with the date if it is not today. No time: "no data", never an estimate. */
export function clock(iso: string | null, now = new Date()): string {
	if (!iso) return t("no_data");
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return t("no_data");
	// Manual format: each browser's ICU formats "2-digit" differently (Node gives "28/9").
	const two = (n: number) => String(n).padStart(2, "0");
	const hm = `${two(d.getHours())}:${two(d.getMinutes())}`;
	if (d.toDateString() === now.toDateString()) return hm;
	return language() === "es"
		? `${two(d.getDate())}/${two(d.getMonth() + 1)} ${hm}`
		: `${MONTHS[d.getMonth()]} ${d.getDate()} ${hm}`;
}

export function summary(route: RouteStatus, now = new Date()): Summary {
	if (route.exhausted) {
		const since = clock(
			new Date(route.exhausted.since * 1000).toISOString(),
			now,
		);
		return {
			tone: "exhausted",
			// Only the date and time stay together: the figure is large, so "back at" may wrap before them.
			figure: route.exhausted.reset_at
				? t("back_at", { time: unbroken(clock(route.exhausted.reset_at, now)) })
				: t("back_unknown"),
			lines: [t("out_since", { time: since })],
		};
	}
	const m = route.measurement;
	if (m.state === "error")
		return {
			tone: "error",
			figure: t("no_data"),
			lines: [t("measure_failed", { error: String(m.error) })],
		};
	if (m.state === "no_data" || m.windows.length === 0) {
		return {
			tone: "no_data",
			figure: t("no_data"),
			lines: [t("provider_silent")],
		};
	}
	// The main figure is the busiest window: it is the one that cuts the route first.
	const top = m.windows.reduce((a, b) =>
		(b.used ?? -1) > (a.used ?? -1) ? b : a,
	);
	return {
		tone: "normal",
		// "used": the percentage is consumption, not balance.
		figure: unbroken(t("used", { value: usage(top.used) })),
		lines: m.windows.map(
			(w) =>
				// "resets" never ends up on another line than its time: if it must wrap, it wraps at the "·".
				`${windowLabel(w.label)} ${usage(w.used)}${w.reset_at ? ` · ${unbroken(t("resets", { time: clock(w.reset_at, now) }))}` : ""}`,
		),
	};
}

const WINDOWS: [RegExp, "window_session" | "window_week"][] = [
	[/^current session$/i, "window_session"],
	[/^current week$/i, "window_week"],
];

/** Window label: Claude's known ones become short words, the model family stays; the rest, as is. */
export function windowLabel(label: string): string {
	const family = label.match(/^(.*?)\s*(\([^)]*\))$/);
	const base = family ? family[1] : label;
	const known = WINDOWS.find(([re]) => re.test(base))?.[1];
	if (!known) return label;
	return family ? `${t(known)} ${family[2]}` : t(known);
}

/** A phrase that never wraps ("resets 19:05", "back Sep 28 00:50"): no-break spaces. */
export const unbroken = (phrase: string) => phrase.replaceAll(" ", " ");

/** One name per model on the whole screen: Hermes reports the dated id (claude-haiku-4-5-20251001), the
 * config and the routes the short one. */
export function modelName(model: string | null | undefined): string | null {
	return model ? model.replace(/-\d{8}$/, "") : null;
}

/** "route · model" of the main agent: the route that uses its provider, or the provider if none covers it. */
export function mainAgentLabel(
	p: { provider: string | null; model: string | null },
	routes: RouteStatus[],
): string {
	const model = modelName(p.model);
	const route = routes.find((r) => r.provider === p.provider);
	return [route?.name ?? p.provider, model].filter(Boolean).join(" · ");
}
