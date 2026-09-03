#!/usr/bin/env bash
# Local toolchain setup: one venv, four pinned binaries, no system installs.
#
# Pins are PARSED from .github/workflows/ci.yaml rather than duplicated here.
# That file is the source of truth the README points at; a second copy of a
# version string is a copy that drifts silently. Checksums are fail-closed: a
# platform variant without a pinned line in tools/checksums-local.txt is an
# error, never an unverified download.
#
# Two knobs exist so the Dockerfile can run this script verbatim:
#   SETUP_DEST       install dir for the binaries (default: <repo>/.venv/bin)
#   SETUP_SKIP_VENV  set to 1 to skip venv creation and pip install
set -euo pipefail
cd "$(dirname "$0")/.."

pin() {
  local value
  value=$(sed -n 's/^  '"$1"': "\(.*\)"$/\1/p' .github/workflows/ci.yaml)
  if [ -z "$value" ]; then
    echo "setup: could not parse $1 from .github/workflows/ci.yaml" >&2
    exit 1
  fi
  printf '%s' "$value"
}

HELM_VERSION=$(pin HELM_VERSION)
PROMTOOL_VERSION=$(pin PROMTOOL_VERSION)
PROMRUVAL_VERSION=$(pin PROMRUVAL_VERSION)
LOKITOOL_VERSION=$(pin LOKITOOL_VERSION)

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
case "$arch" in
  x86_64) arch=amd64 ;;
  aarch64 | arm64) arch=arm64 ;;
esac
case "$os-$arch" in
  darwin-arm64 | linux-amd64 | linux-arm64) ;;
  *)
    cat >&2 <<EOF
setup: no pinned checksums for $os-$arch, refusing to install unverified
binaries. Install these yourself and put them on PATH:
  helm $HELM_VERSION, promtool $PROMTOOL_VERSION, promruval $PROMRUVAL_VERSION, lokitool $LOKITOOL_VERSION
EOF
    exit 1
    ;;
esac

if [ "${SETUP_SKIP_VENV:-0}" != 1 ]; then
  if [ ! -x .venv/bin/pip ]; then
    python3 -m venv .venv
  fi
  .venv/bin/pip install --quiet -r requirements.txt
fi

dest=${SETUP_DEST:-$PWD/.venv/bin}
mkdir -p "$dest"

# Idempotence by stamp, not by probing each tool's --version flag: the four
# tools disagree about version-flag spelling and output shape, and the stamp
# covers the platform too, so a copied-around .venv is reinstalled rather
# than trusted.
stamp="$dest/.tool-pins"
want="$HELM_VERSION $PROMTOOL_VERSION $PROMRUVAL_VERSION $LOKITOOL_VERSION $os-$arch"
if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ] &&
  [ -x "$dest/helm" ] && [ -x "$dest/promtool" ] &&
  [ -x "$dest/promruval" ] && [ -x "$dest/lokitool" ]; then
  echo "setup: pinned tools already installed in $dest"
  exit 0
fi

sha_check() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum --check --strict "$1"
  else
    shasum -a 256 --check --strict "$1"
  fi
}

helm_file="helm-$HELM_VERSION-$os-$arch.tar.gz"
prom_file="prometheus-$PROMTOOL_VERSION.$os-$arch.tar.gz"
ruval_file="promruval_${PROMRUVAL_VERSION}_${os}_${arch}.tar.gz"
loki_file="lokitool-$os-$arch.zip"

repo_root=$PWD
workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT
cd "$workdir"

curl -sSfL --retry 3 -o "$helm_file" "https://get.helm.sh/$helm_file"
curl -sSfL --retry 3 -o "$prom_file" \
  "https://github.com/prometheus/prometheus/releases/download/v$PROMTOOL_VERSION/$prom_file"
curl -sSfL --retry 3 -o "$ruval_file" \
  "https://github.com/fusakla/promruval/releases/download/v$PROMRUVAL_VERSION/$ruval_file"
curl -sSfL --retry 3 -o "$loki_file" \
  "https://github.com/grafana/loki/releases/download/v$LOKITOOL_VERSION/$loki_file"

# Exactly one pinned line per artifact, selected by full artifact name. Zero
# lines means the manifest does not know this pin (a version bump without a
# manifest update, or an unlisted platform); more than one would make the
# verification ambiguous. Both refuse rather than guess.
manifest=selected-checksums.txt
: >"$manifest"
for f in "$helm_file" "$prom_file" "$ruval_file" "$loki_file"; do
  line=$(grep -F "  $f" "$repo_root/tools/checksums-local.txt" || true)
  if [ "$(printf '%s\n' "$line" | grep -c .)" != 1 ]; then
    echo "setup: expected exactly one pinned checksum for $f in tools/checksums-local.txt" >&2
    exit 1
  fi
  printf '%s\n' "$line" >>"$manifest"
done
sha_check "$manifest"

tar -xzf "$helm_file"
install -m 0755 "$os-$arch/helm" "$dest/helm"
tar -xzf "$prom_file"
install -m 0755 "prometheus-$PROMTOOL_VERSION.$os-$arch/promtool" "$dest/promtool"
tar -xzf "$ruval_file"
install -m 0755 promruval "$dest/promruval"
unzip -q "$loki_file"
install -m 0755 "lokitool-$os-$arch" "$dest/lokitool"

printf '%s' "$want" >"$stamp"
echo "setup: installed helm $HELM_VERSION, promtool $PROMTOOL_VERSION, promruval $PROMRUVAL_VERSION, lokitool $LOKITOOL_VERSION into $dest"
if [ "${SETUP_SKIP_VENV:-0}" != 1 ]; then
  echo "setup: 'source .venv/bin/activate' (or put .venv/bin first on PATH), then 'make check'"
fi
