FROM golang:1.25.6-alpine@sha256:98e6cffc31ccc44c7c15d83df1d69891efee8115a5bb7ede2bf30a38af3e3c92 AS build
WORKDIR /src
COPY services/fulfillment/go.mod services/fulfillment/go.sum ./
RUN go mod download
COPY services/fulfillment/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /fulfillment .

FROM alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8
RUN apk add --no-cache ca-certificates && addgroup -g 10001 app && adduser -D -u 10001 -G app app
COPY --from=build /fulfillment /usr/local/bin/fulfillment
USER 10001:10001
EXPOSE 50051 8081
ENTRYPOINT ["/usr/local/bin/fulfillment"]
