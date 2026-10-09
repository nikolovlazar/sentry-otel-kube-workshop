import * as Sentry from "@sentry/nextjs";
import type { PublicConfig } from "./types";
let initialized: Promise<PublicConfig> | undefined;
export function initializeBrowserSentry(): Promise<PublicConfig> {
  return (initialized ??= fetch("/api/public-config", {
    cache: "no-store",
  }).then(async (response) => {
    if (!response.ok)
      throw new Error("Public telemetry configuration unavailable.");
    const config: PublicConfig = await response.json();
    Sentry.init({
      dsn: config.dsn,
      environment: config.environment,
      release: config.release,
      tracesSampleRate: 1,
      propagateTraceparent: true,
      tracePropagationTargets: [/^\//, window.location.origin],
      integrations: (defaults) => [
        ...defaults.filter((integration) => integration.name !== "Dedupe"),
        Sentry.browserTracingIntegration(),
      ],
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
        genAI: { inputs: false, outputs: false },
        databaseQueryData: false,
        queues: false,
        stackFrameVariables: false,
      },
    });
    return config;
  }));
}
