// Every string the user reads, in English (default) and Spanish. The language follows the dashboard's own
// language picker (useI18n().locale from Hermes' plugin SDK): App sets it on each render, before its children.

const EN = {
	// Header and connection
	gateway: "gateway",
	connecting: "connecting",
	connected: "connected",
	offline: "offline",
	retry: "Retry",
	back_to_hermes: "Back to Hermes",
	connection_lost: "the connection with the gateway was lost",
	gateway_offline: "gateway offline",
	gateway_error: "the gateway answered with an error",
	// Routes rail
	routes: "Routes",
	measure_again: "Measure again",
	measure_again_sr: "Measure the quotas again",
	status_failed: "Could not read the routes status: {error}",
	measuring: "Measuring quotas…",
	no_routes_before: "No routes. Add them under",
	no_routes_after: "in Hermes' config.yaml.",
	no_data: "no data",
	back_at: "back {time}",
	out_since: "out of quota since {time}",
	measure_failed: "could not measure: {error}",
	provider_silent: "the provider does not report usage",
	used: "{value} used",
	resets: "resets {time}",
	window_session: "session",
	window_week: "week",
	// Conversation
	conversation: "Conversation",
	answers: "answers",
	new_conversation: "New conversation",
	no_messages: "No messages yet. Write below to start.",
	send_failed: "Could not send: {error}",
	role_user: "you",
	role_agent: "agent",
	role_note: "note",
	note_writing: "writing",
	note_stopped: "stopped",
	note_no_answer: "no answer",
	note_not_sent: "not sent",
	failure_summary: "The provider did not answer this turn. See Hermes' details",
	thinking: "thinking",
	a_tool: "a tool",
	using_tool: "using {tool}",
	delegating_to: "delegating to {route}",
	// Composer
	composer_label: "Message for the agent",
	composer_hint:
		"Write to the agent. Enter sends, Shift+Enter adds a line break.",
	composer_paused: "Paused for quota: resume, resend or cancel above to go on.",
	composer_offline: "No connection with Hermes.",
	waiting_answer: "waiting for an answer",
	stop: "Stop",
	send: "Send",
	// Quota pause
	paused_title: "Paused · out of quota",
	provider: "provider",
	model: "model",
	resets_label: "resets",
	resumes_at: "The turn resumes on its own at {time}, with the same model.",
	reset_unknown:
		"The provider does not report when the quota comes back. Resume whenever you want or pass the turn to another route.",
	resume_now: "Resume now",
	resend_route_label: "Route to resend the turn to",
	resend: "Resend",
	cancel: "Cancel",
	pause_register_failed: "could not record the quota pause: {error}",
	resume_session_failed:
		"A turn is paused, but its session could not be resumed: {error}. Reload the page to try again.",
	pause_resolved_elsewhere: "This pause was already resolved in another tab.",
	pause_no_session: "the pause has no session",
	retry_missing: "Hermes did not return the turn to retry",
	retry_rejected:
		"the pause was resolved, but Hermes did not accept the retry: {error}. Write again to go on.",
	turn_resent: "turn resent from {from} to {route} · {model}",
	// Subagents
	subagents: "Subagents",
	subagent: "subagent",
	no_subagents:
		"No subagents in this conversation yet. They show up here when the agent delegates to a route.",
	running: "running",
	completed: "completed",
	failed: "failed",
	out_of_quota: "out of quota",
	seconds: "{value} s",
} as const;

export type TextId = keyof typeof EN;

const ES: Record<TextId, string> = {
	gateway: "gateway",
	connecting: "conectando",
	connected: "conectado",
	offline: "sin conexión",
	retry: "Reintentar",
	back_to_hermes: "Volver a Hermes",
	connection_lost: "se perdió la conexión con el gateway",
	gateway_offline: "gateway sin conexión",
	gateway_error: "el gateway respondió con error",
	routes: "Rutas",
	measure_again: "Medir de nuevo",
	measure_again_sr: "Medir de nuevo las cuotas",
	status_failed: "No se pudo leer el estado de las rutas: {error}",
	measuring: "Midiendo cuotas…",
	no_routes_before: "No hay rutas. Agrégalas en",
	no_routes_after: "del config.yaml de Hermes.",
	no_data: "sin dato",
	back_at: "vuelve {time}",
	out_since: "sin cuota desde {time}",
	measure_failed: "no se pudo medir: {error}",
	provider_silent: "el proveedor no informa consumo",
	used: "{value} usado",
	resets: "reinicia {time}",
	window_session: "sesión",
	window_week: "semana",
	conversation: "Conversación",
	answers: "responde",
	new_conversation: "Nueva conversación",
	no_messages: "Todavía no hay mensajes. Escribe abajo para empezar.",
	send_failed: "No se pudo enviar: {error}",
	role_user: "tú",
	role_agent: "agente",
	role_note: "nota",
	note_writing: "escribiendo",
	note_stopped: "detenido",
	note_no_answer: "sin respuesta",
	note_not_sent: "no enviado",
	failure_summary:
		"El proveedor no respondió este turno. Ver el detalle de Hermes",
	thinking: "pensando",
	a_tool: "una herramienta",
	using_tool: "usando {tool}",
	delegating_to: "delegando en {route}",
	composer_label: "Mensaje para el agente",
	composer_hint:
		"Escribe al agente. Enter envía, Shift+Enter hace un salto de línea.",
	composer_paused:
		"En pausa por cuota: reanuda, reenvía o cancela arriba para seguir.",
	composer_offline: "Sin conexión con Hermes.",
	waiting_answer: "esperando respuesta",
	stop: "Detener",
	send: "Enviar",
	paused_title: "En pausa · sin cuota",
	provider: "proveedor",
	model: "modelo",
	resets_label: "reinicia",
	resumes_at: "El turno se reanuda solo a las {time}, con el mismo modelo.",
	reset_unknown:
		"El proveedor no informa cuándo vuelve la cuota. Reanuda cuando quieras o pasa el turno a otra ruta.",
	resume_now: "Reanudar ahora",
	resend_route_label: "Ruta para reenviar el turno",
	resend: "Reenviar",
	cancel: "Cancelar",
	pause_register_failed: "no se pudo registrar la pausa por cuota: {error}",
	resume_session_failed:
		"Hay un turno en pausa, pero no se pudo retomar su sesión: {error}. Recarga la página para intentarlo de nuevo.",
	pause_resolved_elsewhere: "Esta pausa ya se resolvió en otra pestaña.",
	pause_no_session: "la pausa no tiene sesión",
	retry_missing: "Hermes no devolvió el turno a reintentar",
	retry_rejected:
		"la pausa se resolvió, pero Hermes no aceptó el reintento: {error}. Escribe de nuevo para continuar.",
	turn_resent: "turno reenviado de {from} a {route} · {model}",
	subagents: "Subagentes",
	subagent: "subagente",
	no_subagents:
		"Todavía no hay subagentes en esta conversación. Aparecen aquí cuando el agente delega en una ruta.",
	running: "corriendo",
	completed: "completado",
	failed: "fallido",
	out_of_quota: "sin cuota",
	seconds: "{value} s",
};

export const TEXTS = { en: EN as Record<TextId, string>, es: ES };
export type Language = keyof typeof TEXTS;

let current: Language = "en";

/** Any locale starting with "es" (es, es-419, es-MX) is Spanish; everything else is English. */
export function setLanguage(locale: unknown): void {
	current = String(locale ?? "")
		.toLowerCase()
		.startsWith("es")
		? "es"
		: "en";
}

export const language = (): Language => current;

export function t(
	id: TextId,
	values: Record<string, string | number> = {},
): string {
	return TEXTS[current][id].replaceAll(/\{(\w+)\}/gu, (whole, key: string) =>
		key in values ? String(values[key]) : whole,
	);
}
