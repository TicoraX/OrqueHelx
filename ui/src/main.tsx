// Registers OrqueHelx in Hermes' dashboard. The /orquehelx tab only marks that it is open: the screen is
// drawn in the "overlay" slot, at the app root, to cover the whole window (inside Hermes' <main>, anything
// fixed stays under its sidebar).
import "./styles.css";
import React, { useEffect, useSyncExternalStore } from "react";
import { App } from "./App";
import { active, mark, subscribe } from "./tab";

function Tab() {
	useEffect(() => {
		mark(true);
		return () => mark(false);
	}, []);
	return null;
}

function Layer() {
	return useSyncExternalStore(subscribe, active) ? <App /> : null;
}

window.__HERMES_PLUGINS__?.register("orquehelx", Tab);
// Hermes' real order: (plugin, slot, component) (web/src/plugins/slots.ts); its sdk.d.ts declares it reversed.
window.__HERMES_PLUGINS__?.registerSlot("orquehelx", "overlay", Layer);
