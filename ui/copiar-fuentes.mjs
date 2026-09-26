// Copia a public/fonts solo los woff2 latinos que usa estiri. En modo libreria Vite incrusta las
// fuentes dentro del CSS (353 KB con todos los alfabetos); servidas aparte pesan una fraccion.
import { copyFileSync, mkdirSync } from "node:fs";

const FUENTES = [
	"@fontsource/ibm-plex-serif/files/ibm-plex-serif-latin-500-normal.woff2",
	"@fontsource-variable/ibm-plex-sans/files/ibm-plex-sans-latin-wght-normal.woff2",
	"@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2",
	"@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2",
];

mkdirSync("public/fonts", { recursive: true });
for (const f of FUENTES)
	copyFileSync(`node_modules/${f}`, `public/fonts/${f.split("/").pop()}`);
