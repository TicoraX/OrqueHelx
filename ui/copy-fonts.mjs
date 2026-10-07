// Copy to public/fonts only the Latin woff2 files estiri uses. In library mode Vite inlines fonts into the
// CSS (353 KB with every script); served on their own they weigh a fraction.
import { copyFileSync, mkdirSync } from "node:fs";

const FONTS = [
	"@fontsource/ibm-plex-serif/files/ibm-plex-serif-latin-500-normal.woff2",
	"@fontsource-variable/ibm-plex-sans/files/ibm-plex-sans-latin-wght-normal.woff2",
	"@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2",
	"@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2",
];

mkdirSync("public/fonts", { recursive: true });
for (const f of FONTS)
	copyFileSync(`node_modules/${f}`, `public/fonts/${f.split("/").pop()}`);
