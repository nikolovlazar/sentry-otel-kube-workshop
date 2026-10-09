export const dynamic = "force-dynamic";
export async function GET() {
  return Response.json(
    {
      dsn: process.env.SENTRY_BROWSER_DSN ?? process.env.SENTRY_DSN ?? "",
      orgSlug: process.env.SENTRY_ORG_SLUG ?? "",
      baseUrl: process.env.SENTRY_BASE_URL ?? "https://sentry.io",
      release: process.env.SENTRY_RELEASE ?? "development",
      environment: process.env.WORKSHOP_ENVIRONMENT ?? "workshop-local",
      sessionId: process.env.WORKSHOP_SESSION_ID ?? "local-workshop",
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
