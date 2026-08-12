#!/usr/bin/env python3
"""Assertions against rendered chart output, not against source files.

These catch the failure modes that only exist after templating: a file silently
excluded from the render, two paths colliding on one generated name, a ConfigMap
over the 1MiB limit, and template-induced YAML corruption.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

TARGETS = ("mimir", "loki", "prometheus")
CONFIGMAP_LIMIT = 1024 * 1024
HEADROOM = 16 * 1024

ROOT = Path(__file__).resolve().parents[1]


def render(target: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "t", str(ROOT),
         "--set", f"target={target}", "--set", "tenant=platform"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        # A target with no rules legitimately fails closed; that is not an error here.
        if "matched no deployable rule files" in out.stderr:
            return []
        print(f"helm template failed for target={target}:\n{out.stderr}", file=sys.stderr)
        raise SystemExit(1)
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def deployable_sources(root: Path) -> set[str]:
    """Every rule file the chart is expected to render, as repo-relative paths."""
    return {
        str(p.relative_to(root))
        for p in (root / "rules").rglob("*.yaml")
        if p.is_file() and not p.name.endswith("-tests.yaml")
    }


def source_findings(expected: set[str], rendered: set[str]) -> list[str]:
    """Reconcile the repository against the render in BOTH directions.

    Comparing only expected - rendered catches a file that silently vanished from
    the output. It cannot catch the opposite: output that came from somewhere the
    repository does not account for, which is what a symlinked directory under
    rules/ produces. check_layout rejects that symlink today, but a render
    assertion that depends on another stage to notice is not an assertion.
    """
    findings = []
    for missing in sorted(expected - rendered):
        findings.append(
            f"{missing}: present in the repository but absent from every rendered ConfigMap. "
            f"A silently unrendered file is indistinguishable from one that works."
        )
    for extra in sorted(rendered - expected):
        findings.append(
            f"{extra}: rendered into a ConfigMap but is not a deployable rule file in the "
            f"repository. The chart is shipping content this repository cannot account for."
        )
    return findings


def main() -> int:
    findings: list[str] = []
    rendered_sources: set[str] = set()
    names: dict[str, str] = {}
    keys: dict[str, str] = {}

    for target in TARGETS:
        for doc in render(target):
            if doc.get("kind") != "ConfigMap":
                findings.append(f"{target}: rendered a {doc.get('kind')}, expected only ConfigMaps")
                continue

            name = doc["metadata"]["name"]
            if name in names:
                findings.append(f"duplicate ConfigMap name '{name}' (also in {names[name]})")
            names[name] = target

            source = doc["metadata"].get("annotations", {}).get("observability-rules/source-path")
            if not source:
                findings.append(f"{name}: missing observability-rules/source-path annotation")
            else:
                rendered_sources.add(source)

            for key, payload in doc.get("data", {}).items():
                if key in keys:
                    findings.append(
                        f"duplicate data key '{key}' in {name} (also in {keys[key]}). "
                        f"These would overwrite each other in the ruler directory."
                    )
                keys[key] = name

                size = len(payload.encode())
                if size > CONFIGMAP_LIMIT - HEADROOM:
                    findings.append(
                        f"{name}: data key '{key}' is {size} bytes, within {HEADROOM} "
                        f"of the {CONFIGMAP_LIMIT} ConfigMap limit"
                    )

                try:
                    yaml.safe_load(payload)
                except yaml.YAMLError as exc:
                    findings.append(
                        f"{name}: extracted payload for '{key}' is not valid YAML, "
                        f"which means templating corrupted it: {exc}"
                    )

    findings.extend(source_findings(deployable_sources(ROOT), rendered_sources))

    for f in findings:
        print(f"  {f}", file=sys.stderr)
    if findings:
        print(f"[render] {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("[render] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
