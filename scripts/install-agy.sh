#!/usr/bin/env bash
set -euo pipefail

agy_binary="$PWD/node_modules/.bin/agy"
if [[ -x "$agy_binary" ]]; then
  "$agy_binary" update
else
  agy_installer="$(mktemp)"
  trap 'rm -f "$agy_installer"' EXIT
  curl --fail --silent --show-error --location --max-time 60 \
    https://antigravity.google/cli/install.sh --output "$agy_installer"
  bash "$agy_installer" --dir "$PWD/node_modules/.bin"
fi
"$agy_binary" --version
