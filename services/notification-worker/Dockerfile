FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS build
WORKDIR /workspace/services/notification-worker
COPY services/notification-worker/package*.json ./
RUN npm ci
COPY services/notification-worker/ ./
RUN npm run build
FROM node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20 AS runtime
WORKDIR /app
ENV NODE_ENV=production PORT=8080 WORKSHOP_PROTO_PATH=/app/proto/workshop.proto
COPY --from=build /workspace/services/notification-worker/package*.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /workspace/services/notification-worker/dist ./dist
COPY proto ./proto
USER node
EXPOSE 8080
CMD ["node","dist/index.js"]
