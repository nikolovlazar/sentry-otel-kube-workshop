// Initialize the provider and Sentry's errors-only bridge before application modules.
await import("./telemetry.js");
await import("./worker.js");
export {};
