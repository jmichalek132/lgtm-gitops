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
# The dashboard uid-change check reads BASE_REF; without it rulecheck skips
# that check, so a host BASE_REF must reach the container.
if [ -n "${BASE_REF:-}" ]; then
  run_args+=(-e "BASE_REF=$BASE_REF")
fi
if [ -n "${PUBLISHABILITY_TERMS_FILE:-}" ]; then
  # The mount relocates the file outside /work and dereferences a symlink, so
  # privatescan's in-repository and symlink checks would pass vacuously inside
  # the container. Enforce both on the host path before mounting. The path
  # itself is never printed, matching privatescan's own diagnostics.
  terms=$PUBLISHABILITY_TERMS_FILE
  if [ -L "$terms" ] || [ ! -f "$terms" ]; then
    echo 'check-docker: <denylist-path> must be an existing regular file, not a symlink' >&2
    exit 1
  fi
  terms=$(cd "$(dirname "$terms")" && pwd -P)/$(basename "$terms")
  case "$terms" in
    "$(pwd -P)"/*)
      echo 'check-docker: <denylist-path> is inside the repository' >&2
      exit 1 ;;
  esac
  # --mount, unlike -v, refuses a missing source instead of creating a
  # directory there.
  run_args+=(--mount "type=bind,source=$terms,target=/run/publishability/denylist.yaml,readonly")
  run_args+=(-e PUBLISHABILITY_TERMS_FILE=/run/publishability/denylist.yaml)
fi

exec docker run "${run_args[@]}" observability-rules-check make check
