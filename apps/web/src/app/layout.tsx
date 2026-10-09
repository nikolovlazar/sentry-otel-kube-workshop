import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Connected Context · Sentry OTel Workshop",
  description:
    "Follow one request through Kubernetes, OpenTelemetry, and Sentry.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
