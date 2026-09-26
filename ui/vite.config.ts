// Build del plugin de dashboard: un IIFE que usa el React de Hermes (window.__HERMES_PLUGIN_SDK__.React).
// El resultado se commitea en orquehelx/dashboard/dist: quien instala el plugin no necesita Node.
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
	resolve: {
		alias: { react: fileURLToPath(new URL("./src/react.ts", import.meta.url)) },
	},
	oxc: {
		jsx: {
			runtime: "classic",
			pragma: "React.createElement",
			pragmaFrag: "React.Fragment",
		},
	},
	build: {
		outDir: "../orquehelx/dashboard/dist",
		emptyOutDir: true,
		assetsInlineLimit: 0,
		lib: {
			entry: "src/main.tsx",
			formats: ["iife"],
			name: "OrqueHelx",
			fileName: () => "index.js",
			cssFileName: "style",
		},
	},
	test: { include: ["tests/**/*.test.ts"] },
});
