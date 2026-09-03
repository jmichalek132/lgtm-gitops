# The CI toolchain as an image, for `make check-docker`. Python matches CI's
# setup-python pin; the four tools are installed by the same scripts/setup.sh
# a contributor runs on the host, verified against the same pinned checksums,
# with versions parsed from the same ci.yaml. Nothing version-shaped lives in
# this file, so a pin bump is one edit in ci.yaml plus the checksum manifest.
#
# The repository is NOT copied in. `make check-docker` bind-mounts it at run
# time, so the image stays reusable across commits and the denylist never
# touches a layer.
FROM python:3.12-slim

# git: check discovery shells out to `git ls-files`, and the dashboard check
# diffs uids against a base commit. make: the contract is `make check`.
# curl/unzip/ca-certificates: setup.sh's downloads.
RUN apt-get update && \
    apt-get install -y --no-install-recommends git make curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/build/requirements.txt
RUN pip install --no-cache-dir -r /opt/build/requirements.txt

COPY scripts/setup.sh /opt/build/scripts/setup.sh
COPY .github/workflows/ci.yaml /opt/build/.github/workflows/ci.yaml
COPY tools/checksums-local.txt /opt/build/tools/checksums-local.txt
RUN SETUP_SKIP_VENV=1 SETUP_DEST=/usr/local/bin /opt/build/scripts/setup.sh
