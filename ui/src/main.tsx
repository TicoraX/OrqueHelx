// Registra OrqueHelx en el dashboard de Hermes. La pestaña /orquehelx solo marca que esta abierta: la
// pantalla se dibuja en el slot "overlay", en la raiz de la app, para cubrir toda la ventana (dentro del
// <main> de Hermes, lo fijo queda por debajo de su barra lateral).
import "./estilos.css";
import React, { useEffect, useSyncExternalStore } from "react";
import { App } from "./App";
import { activa, marcar, suscribir } from "./pestana";

function Pestana() {
	useEffect(() => {
		marcar(true);
		return () => marcar(false);
	}, []);
	return null;
}

function Capa() {
	return useSyncExternalStore(suscribir, activa) ? <App /> : null;
}

window.__HERMES_PLUGINS__?.register("orquehelx", Pestana);
// Orden real de Hermes: (plugin, slot, componente) (web/src/plugins/slots.ts); su sdk.d.ts lo declara al reves.
window.__HERMES_PLUGINS__?.registerSlot("orquehelx", "overlay", Capa);
