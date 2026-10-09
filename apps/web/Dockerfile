FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS build
WORKDIR /workspace/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
COPY proto /workspace/proto
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1 WORKSHOP_PROTO_PATH=/app/proto/workshop.proto
COPY --from=build /workspace/apps/web/.next/standalone ./
# Standalone tracing omits server chunk maps; retain the exact builder assets for symbolication.
COPY --from=build /workspace/apps/web/.next/server ./apps/web/.next/server
COPY --from=build /workspace/apps/web/.next/static ./apps/web/.next/static
COPY --from=build /workspace/proto ./proto
USER node
EXPOSE 3000
CMD ["node","apps/web/server.js"]
