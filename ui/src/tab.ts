// The /orquehelx tab lives inside Hermes' <main>, which clips anything fixed under its sidebar. The screen
// is drawn in the "overlay" slot (app root) while the tab is mounted; this is the only state they share.

let isActive = false;
const listeners = new Set<() => void>();

export function active(): boolean {
	return isActive;
}

export function mark(value: boolean): void {
	if (value === isActive) return;
	isActive = value;
	for (const listener of listeners) listener();
}

export function subscribe(listener: () => void): () => void {
	listeners.add(listener);
	return () => listeners.delete(listener);
}
