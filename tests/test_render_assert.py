import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_assert


def test_source_findings_accepts_an_exact_match():
    files = {"rules/payments/mimir/a-alerts.yaml"}
    assert render_assert.source_findings(files, files) == []


def test_source_findings_reports_a_file_that_never_rendered():
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/a-alerts.yaml", "rules/payments/loki/b-alerts.yaml"},
        rendered={"rules/payments/mimir/a-alerts.yaml"},
    )
    assert len(findings) == 1
    assert "rules/payments/loki/b-alerts.yaml" in findings[0]
    assert "absent from every rendered ConfigMap" in findings[0]


def test_source_findings_reports_a_rendered_source_that_is_not_in_the_repository():
    # A symlinked directory under rules/ renders a ConfigMap whose source lies
    # outside the repository. check_layout rejects the symlink, but stage 6 must
    # not depend on another stage to notice that it rendered something it cannot
    # account for.
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/a-alerts.yaml"},
        rendered={"rules/payments/mimir/a-alerts.yaml", "rules/payments/mimir/linked/x-alerts.yaml"},
    )
    assert len(findings) == 1
    assert "rules/payments/mimir/linked/x-alerts.yaml" in findings[0]
    assert "not a deployable rule file in the repository" in findings[0]


def test_source_findings_reports_both_directions_at_once():
    findings = render_assert.source_findings(
        expected={"rules/payments/mimir/missing-alerts.yaml"},
        rendered={"rules/payments/mimir/extra-alerts.yaml"},
    )
    assert len(findings) == 2
    joined = "\n".join(findings)
    assert "missing-alerts.yaml" in joined
    assert "extra-alerts.yaml" in joined


def test_source_findings_does_not_expect_test_fixtures_to_render(tmp_path):
    # deployable_sources is the definition of "expected", so the exclusion of
    # -tests.yaml lives in one place rather than being restated per caller.
    for rel in ("rules/p/mimir/a-alerts.yaml", "rules/p/mimir/a-alerts-tests.yaml"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("groups: []\n")
    assert render_assert.deployable_sources(tmp_path) == {"rules/p/mimir/a-alerts.yaml"}


def test_render_takes_the_tenant_from_values_yaml_like_argocd(monkeypatch):
    # The Applications override only target, so validation must not pin a
    # tenant: a longer tenant in values.yaml could push a generated name past
    # 253 bytes in deployment while a hardcoded short one passes validation.
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return Done()

    monkeypatch.setattr(render_assert.subprocess, "run", fake_run)
    render_assert.render("mimir")
    assert not any("tenant=" in a for a in seen["argv"])
    assert "target=mimir" in seen["argv"]


def test_main_rejects_a_configmap_too_big_for_last_applied(monkeypatch, capsys):
    # 300 KiB is far under the 1 MiB ConfigMap limit and still unsyncable by
    # client-side apply, whose last-applied annotation is capped at 256 KiB.
    a = "rules/p/mimir/big-rules.yaml"
    doc = {"kind": "ConfigMap",
           "metadata": {"name": "platform-p-mimir-big-rules",
                        "annotations": {"observability-rules/source-path": a}},
           "data": {"p-mimir-big-rules.yaml": "#" + "x" * (300 * 1024)}}
    monkeypatch.setattr(render_assert, "render", lambda t: [doc] if t == "mimir" else [])
    monkeypatch.setattr(render_assert, "deployable_sources", lambda root: {a})
    assert render_assert.main() == 1
    assert "last-applied-configuration" in capsys.readouterr().err


def cm(name, source, payload="groups: []\n", target="mimir", key=None):
    key = key or source.removeprefix("rules/").replace("/", "-")
    return {
        "kind": "ConfigMap",
        "metadata": {"name": name, "labels": {"rules": target},
                     "annotations": {"observability-rules/source-path": source}},
        "data": {key: payload.rstrip("\n")},
    }


def run_main(monkeypatch, tmp_path, docs_by_target, sources, content="groups: []\n"):
    for source in sources:
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    monkeypatch.setattr(render_assert, "ROOT", tmp_path)
    monkeypatch.setattr(render_assert, "render", lambda t: docs_by_target.get(t, []))
    monkeypatch.setattr(render_assert, "deployable_sources", lambda root: set(sources))
    return render_assert.main()


def test_main_accepts_a_clean_render(monkeypatch, tmp_path):
    a = "rules/p/mimir/a-alerts.yaml"
    assert run_main(monkeypatch, tmp_path, {"mimir": [cm("platform-a", a)]}, {a}) == 0


def test_main_rejects_two_paths_flattening_to_one_data_key(monkeypatch, tmp_path, capsys):
    # The design names this assertion as THE guard against flattening
    # collisions: checkout/latency-alerts.yaml and checkout-latency-alerts.yaml.
    a = "rules/payments/mimir/checkout/latency-alerts.yaml"
    b = "rules/payments/mimir/checkout-latency-alerts.yaml"
    docs = {"mimir": [cm("platform-x1", a), cm("platform-x2", b)]}
    assert run_main(monkeypatch, tmp_path, docs, {a, b}) == 1
    assert "duplicate data key 'payments-mimir-checkout-latency-alerts.yaml'" in capsys.readouterr().err


def test_main_rejects_a_duplicate_configmap_name(monkeypatch, tmp_path, capsys):
    a, b = "rules/p/mimir/a-alerts.yaml", "rules/p/mimir/b-alerts.yaml"
    docs = {"mimir": [cm("platform-same", a), cm("platform-same", b)]}
    assert run_main(monkeypatch, tmp_path, docs, {a, b}) == 1
    assert "duplicate ConfigMap name 'platform-same'" in capsys.readouterr().err


def test_main_rejects_an_oversized_payload(monkeypatch, tmp_path, capsys):
    a = "rules/p/mimir/a-alerts.yaml"
    big = "#" + "x" * render_assert.CONFIGMAP_LIMIT + "\n"
    docs = {"mimir": [cm("platform-a", a, big)]}
    assert run_main(monkeypatch, tmp_path, docs, {a}, content=big) == 1
    assert "ConfigMap limit" in capsys.readouterr().err


def test_main_rejects_a_corrupted_payload(monkeypatch, tmp_path, capsys):
    a = "rules/p/mimir/a-alerts.yaml"
    docs = {"mimir": [cm("platform-a", a, "groups: [\n")]}
    assert run_main(monkeypatch, tmp_path, docs, {a}) == 1
    assert "not valid YAML" in capsys.readouterr().err


def test_main_rejects_an_empty_data_map(monkeypatch, tmp_path, capsys):
    a = "rules/p/mimir/a-alerts.yaml"
    doc = cm("platform-a", a)
    doc["data"] = {}
    assert run_main(monkeypatch, tmp_path, {"mimir": [doc]}, {a}) == 1
    assert "expected exactly" in capsys.readouterr().err


def test_main_rejects_a_valid_payload_that_is_not_the_source_file(monkeypatch, tmp_path, capsys):
    a = "rules/p/mimir/a-alerts.yaml"
    docs = {"mimir": [cm("platform-a", a, "groups: []\n")]}
    real = "groups:\n  - name: g\n    rules: []\n"
    assert run_main(monkeypatch, tmp_path, docs, {a}, content=real) == 1
    assert "differs from" in capsys.readouterr().err


def test_main_rejects_a_wrong_routing_label(monkeypatch, tmp_path, capsys):
    a = "rules/p/mimir/a-alerts.yaml"
    docs = {"mimir": [cm("platform-a", a, target="loki")]}
    assert run_main(monkeypatch, tmp_path, docs, {a}) == 1
    assert "routing label" in capsys.readouterr().err
