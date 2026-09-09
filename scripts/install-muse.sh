#!/usr/bin/env bash
set -euo pipefail

muse_installer="$(mktemp)"
trap 'rm -f "$muse_installer"' EXIT
curl --fail --silent --show-error --location --max-time 60 \
  https://dev.meta.ai/install.sh --output "$muse_installer"
MUSE_INSTALL_DIR="$PWD/node_modules/.bin" MUSE_NO_MODIFY_PATH=1 bash "$muse_installer"
"$PWD/node_modules/.bin/muse" --version
