import { callRpc } from "@/lib/grpc";
import { validRunId } from "@/lib/types";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  if (!validRunId(runId))
    return Response.json({ error: "Invalid run ID" }, { status: 400 });
  try {
    return Response.json(await callRpc("GetRun", { runId }));
  } catch (error) {
    return Response.json(
      { error: "Run unavailable." },
      { status: (error as { code?: number }).code === 5 ? 404 : 503 },
    );
  }
}
