// "react" apunta aca en el build (vite.config.ts): el plugin usa el React de Hermes, no uno propio.
import type ReactNS from "react";
import { sdk } from "./sdk";

const React = sdk().React as typeof ReactNS;
export default React;
export const { useCallback, useEffect, useReducer, useRef, useState, useSyncExternalStore } = React;
