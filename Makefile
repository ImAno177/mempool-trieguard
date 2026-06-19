GO ?= go
PYTHON ?= python
DIST_DIR ?= dist
BIN_DIR ?= bin
SERVER_LINUX ?= $(DIST_DIR)/server-linux-amd64
DETECTOR_CLI ?= $(BIN_DIR)/detector-cli

.PHONY: test verify build build-linux clean

test:
	$(GO) test ./...

verify:
	$(PYTHON) -m py_compile python/benchmark_pipeline.py scripts/build_active_protected_accounts.py
	$(GO) test ./...
	$(GO) build -o $(DETECTOR_CLI) ./cmd/detector-cli
	$(GO) build -o $(BIN_DIR)/server ./cmd/server

build:
	$(GO) build -o $(BIN_DIR)/server ./cmd/server
	$(GO) build -o $(DETECTOR_CLI) ./cmd/detector-cli

build-linux:
	mkdir -p $(DIST_DIR)
	GOOS=linux GOARCH=amd64 CGO_ENABLED=0 $(GO) build -trimpath -ldflags="-s -w" -o $(SERVER_LINUX) ./cmd/server
	sha256sum $(SERVER_LINUX) > $(SERVER_LINUX).sha256

clean:
	rm -rf $(DIST_DIR)
