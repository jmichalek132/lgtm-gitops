#!/usr/bin/env bash
# Run the exact CI check inside the pinned-toolchain image.
#
# The image never contains the repository: it is a bind mount, so the run
# checks the tree as it sits on disk.
#
# The container runs as the invoking user, not root, so pytest and helm
# caches written into the mount keep the host's ownership. HOME=/tmp because
# that uid has no home in the image and git refuses to run without one it
# can stat.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build --quiet -t observability-rules-check . >/dev/null

run_args=(--rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/work" -w /work)
# The dashboard uid-change check reads BASE_REF; without it rulecheck skips
# that check, so a host BASE_REF must reach the container.
if [ -n "${BASE_REF:-}" ]; then
  run_args+=(-e "BASE_REF=$BASE_REF")
fi

exec docker run "${run_args[@]}" observability-rules-check make check
