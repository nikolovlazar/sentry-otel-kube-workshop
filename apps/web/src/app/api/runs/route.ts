import { parseStartRequest } from "@/lib/types";
import { startRun } from "@/lib/workshop";
import { callRpc } from "@/lib/grpc";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  let input: ReturnType<typeof parseStartRequest>;
  try {
    input = parseStartRequest(await request.json());
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Invalid request" },
      { status: 400 },
    );
  }
  try {
    const run = await startRun(input);
    const status =
      run.status === "failed"
        ? run.failureService === "web"
          ? 500
          : 502
        : 202;
    return Response.json(run, { status });
  } catch {
    return Response.json(
      {
        error:
          "Workflow state service unavailable. Check fulfillment readiness.",
      },
      { status: 503 },
    );
  }
}
export async function GET(request: Request) {
  const sessionId =
    new URL(request.url).searchParams.get("sessionId") ??
    process.env.WORKSHOP_SESSION_ID ??
    "local-workshop";
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(sessionId))
    return Response.json({ error: "Invalid session ID" }, { status: 400 });
  try {
    return Response.json(await callRpc("ListRuns", { sessionId }));
  } catch {
    return Response.json(
      { error: "Run history unavailable." },
      { status: 503 },
    );
  }
}
