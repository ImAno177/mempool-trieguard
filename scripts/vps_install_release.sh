#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-ImAno177/mempool-trieguard}"
VERSION="${VERSION:-latest}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/mempool-trieguard}"
ASSET_NAME="${ASSET_NAME:-server-linux-amd64}"

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/configs" "$INSTALL_DIR/results" "$INSTALL_DIR/logs" "$INSTALL_DIR/data"

if [[ "$VERSION" == "latest" ]]; then
  base_url="https://github.com/$REPO/releases/latest/download"
else
  base_url="https://github.com/$REPO/releases/download/$VERSION"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

curl -fsSL "$base_url/$ASSET_NAME" -o "$tmp_dir/server"
if curl -fsSL "$base_url/$ASSET_NAME.sha256" -o "$tmp_dir/server.sha256"; then
  (cd "$tmp_dir" && sha256sum -c server.sha256)
fi

install -m 0755 "$tmp_dir/server" "$INSTALL_DIR/server"
echo "Installed $REPO $VERSION to $INSTALL_DIR/server"
