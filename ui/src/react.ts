// "react" points here in the build (vite.config.ts): the plugin uses Hermes' React, not its own.
import type ReactNS from "react";
import { sdk } from "./sdk";

const React = sdk().React as typeof ReactNS;
export default React;
export const {
	useCallback,
	useEffect,
	useReducer,
	useRef,
	useState,
	useSyncExternalStore,
} = React;
