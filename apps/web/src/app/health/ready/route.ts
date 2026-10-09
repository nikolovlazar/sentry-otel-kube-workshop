import { ready } from "@/lib/grpc";
export const dynamic = "force-dynamic";
export async function GET() {
  try {
    await Promise.all([
      ready(),
      fetch(
        new URL(
          "/health/ready",
          process.env.CATALOG_URL ?? "http://localhost:8000",
        ),
        { cache: "no-store", signal: AbortSignal.timeout(2500) },
      ).then((response) => {
        if (!response.ok) throw new Error("Catalog not ready");
      }),
    ]);
    return Response.json({ status: "ready" });
  } catch {
    return Response.json({ status: "unavailable" }, { status: 503 });
  }
}
