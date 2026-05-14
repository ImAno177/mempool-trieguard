# syntax=docker/dockerfile:1.7

FROM golang:1.24-bookworm AS go-builder
WORKDIR /src

COPY go.mod go.sum ./
RUN go mod download

COPY cmd ./cmd
COPY internal ./internal

RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/detector-cli ./cmd/detector-cli

FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY python/requirements.txt /app/python/requirements.txt
RUN pip install --no-cache-dir -r /app/python/requirements.txt

COPY --from=go-builder /out/server /usr/local/bin/server
COPY --from=go-builder /out/detector-cli /usr/local/bin/detector-cli

COPY configs ./configs
COPY web ./web
COPY python ./python
COPY scripts ./scripts
COPY README.md ./README.md

RUN mkdir -p /app/data /app/results

EXPOSE 8080

CMD ["server", "--config", "/app/configs/app.yaml"]
