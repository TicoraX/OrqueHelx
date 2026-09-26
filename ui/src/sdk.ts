// Superficie del SDK de plugins de Hermes que usa OrqueHelx (web/src/plugins/sdk.d.ts de Hermes).
import type ReactNS from "react";

export interface HermesPluginSDK {
	React: typeof ReactNS;
	fetchJSON: <T = unknown>(url: string, init?: RequestInit) => Promise<T>;
	buildWsUrl: (
		path: string,
		params?: Record<string, string>,
	) => Promise<string>;
}

interface PluginRegistry {
	register(
		name: string,
		component: ReactNS.ComponentType<Record<string, never>>,
	): void;
	/** Orden de la implementacion (slots.ts), no el de su sdk.d.ts, que declara (slot, name). */
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

/** El SDK existe solo dentro del dashboard de Hermes; fuera de el, el plugin no hace nada. */
export function sdk(): HermesPluginSDK {
	const s = window.__HERMES_PLUGIN_SDK__;
	if (!s)
		throw new Error(
			"OrqueHelx necesita el dashboard de Hermes (falta window.__HERMES_PLUGIN_SDK__)",
		);
	return s;
}
