import { initializeBrowserSentry } from "./lib/browser-sentry";
import * as Sentry from "@sentry/nextjs";
void initializeBrowserSentry().catch(() =>
  console.warn("Workshop browser telemetry could not initialize."),
);
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
