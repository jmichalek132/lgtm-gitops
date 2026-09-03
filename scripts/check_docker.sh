#!/usr/bin/env bash
# Run the exact CI check inside the pinned-toolchain image.
#
# The image never contains the repository or the denylist. The repository is
# a bind mount, so the run checks the tree as it sits on disk; the denylist,
# when PUBLISHABILITY_TERMS_FILE is set on the host, is bind-mounted
# read-only for this run and lands in no layer. When it is unset, Gate 2
# fails closed inside the container exactly as it does on the host.
#
# The container runs as the invoking user, not root, so pytest and helm
# caches written into the mount keep the host's ownership. HOME=/tmp because
# that uid has no home in the image and git refuses to run without one it
# can stat.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build --quiet -t observability-rules-check . >/dev/null

run_args=(--rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work" -w /work)
if [ -n "${PUBLISHABILITY_TERMS_FILE:-}" ]; then
  run_args+=(-v "$PUBLISHABILITY_TERMS_FILE:/run/publishability/denylist.yaml:ro")
  run_args+=(-e PUBLISHABILITY_TERMS_FILE=/run/publishability/denylist.yaml)
fi

exec docker run "${run_args[@]}" observability-rules-check make check
