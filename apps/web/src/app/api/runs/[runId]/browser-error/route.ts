import { callRpc } from "@/lib/grpc";
import { validRunId } from "@/lib/types";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(
  request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  if (!validRunId(runId))
    return Response.json({ error: "Invalid run ID" }, { status: 400 });
  let errorEventId: unknown;
  try {
    ({ errorEventId } = await request.json());
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (typeof errorEventId !== "string" || !/^[0-9a-f]{32}$/i.test(errorEventId))
    return Response.json({ error: "Invalid Sentry event ID" }, { status: 400 });
  try {
    const run = await callRpc("GetRun", { runId });
    if (run.scenario !== "browser-error")
      return Response.json(
        { error: "Run is not a browser-error scenario" },
        { status: 409 },
      );
    return Response.json(
      await callRpc("RecordFailure", {
        runId,
        failureService: "browser",
        errorCode: "BROWSER_DEMO_ERROR",
        errorEventId,
      }),
    );
  } catch {
    return Response.json(
      { error: "Could not record browser failure." },
      { status: 503 },
    );
  }
}
