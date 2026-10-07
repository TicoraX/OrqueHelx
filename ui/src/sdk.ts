// The part of Hermes' plugin SDK that OrqueHelx uses (Hermes' web/src/plugins/sdk.d.ts).
import type ReactNS from "react";

export interface HermesPluginSDK {
	React: typeof ReactNS;
	fetchJSON: <T = unknown>(url: string, init?: RequestInit) => Promise<T>;
	buildWsUrl: (
		path: string,
		params?: Record<string, string>,
	) => Promise<string>;
	/** The dashboard's i18n context; typed loosely by Hermes. OrqueHelx only reads `locale`. */
	useI18n?: () => unknown;
}

interface PluginRegistry {
	register(
		name: string,
		component: ReactNS.ComponentType<Record<string, never>>,
	): void;
	/** The implementation's order (slots.ts), not the one in its sdk.d.ts, which declares (slot, name). */
	registerSlot(
		plugin: string,
		slot: string,
		component: ReactNS.ComponentType,
	): void;
}

declare global {
	interface Window {
		__HERMES_PLUGIN_SDK__?: HermesPluginSDK;
		__HERMES_PLUGINS__?: PluginRegistry;
	}
}

/** The SDK only exists inside Hermes' dashboard; outside it, the plugin does nothing. */
export function sdk(): HermesPluginSDK {
	const s = window.__HERMES_PLUGIN_SDK__;
	if (!s)
		throw new Error(
			"OrqueHelx needs Hermes' dashboard (window.__HERMES_PLUGIN_SDK__ is missing)",
		);
	return s;
}

/** The dashboard's language (its own picker), or null if this Hermes does not expose it. */
export function useLocale(): string | null {
	const i18n = sdk().useI18n?.() as { locale?: unknown } | undefined;
	return typeof i18n?.locale === "string" ? i18n.locale : null;
}
