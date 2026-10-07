// Dashboard plugin build: an IIFE that uses Hermes' React (window.__HERMES_PLUGIN_SDK__.React).
// The output is committed to orquehelx/dashboard/dist: whoever installs the plugin needs no Node.
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
