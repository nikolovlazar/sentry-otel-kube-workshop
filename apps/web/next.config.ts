import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";
import path from "node:path";
const nextConfig: NextConfig = {
  output: "standalone",
  productionBrowserSourceMaps: true,
  outputFileTracingRoot: path.resolve(process.cwd(), "../.."),
  serverExternalPackages: ["@grpc/grpc-js", "@grpc/proto-loader"],
};
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG_SLUG,
  project: process.env.SENTRY_PROJECT_SLUG,
  tunnelRoute: "/monitoring",
  // The npm build wrapper uploads once, then waits for server-side processing.
  sourcemaps: { disable: "disable-upload", deleteSourcemapsAfterUpload: false },
  release: { name: process.env.SENTRY_RELEASE, create: false, finalize: false },
  silent: true,
  telemetry: false,
});
