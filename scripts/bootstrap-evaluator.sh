#!/usr/bin/env sh
set -eu

image="${EVALUATOR_IMAGE:-portfolio-arena-evaluator:local}"
volume="${EVALUATOR_CODEX_VOLUME:-portfolio-arena-codex}"

podman build --format docker --file Dockerfile.evaluator --tag "$image" .
podman volume create "$volume" >/dev/null
podman run --rm --interactive --tty \
  --volume "$volume:/var/lib/codex" \
  --entrypoint codex \
  "$image" login --device-auth
podman run --rm \
  --volume "$volume:/var/lib/codex" \
  --entrypoint codex \
  "$image" login status

printf '%s\n' "Codex login saved in Podman volume: $volume"
