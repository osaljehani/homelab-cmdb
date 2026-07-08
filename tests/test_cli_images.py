import json

from typer.testing import CliRunner

from cmdb.cli.main import app
from cmdb.domain.services.images import get_image

runner = CliRunner()


def _envelope():
    return {
        "scanned_at": "2026-07-01T04:00:00Z",
        "trivy_version": "0.72.0",
        "images": [
            {
                "ArtifactName": "nginx:latest",
                "Metadata": {"ImageID": "sha256:x"},
                "Results": [
                    {
                        "Target": "nginx",
                        "Vulnerabilities": [
                            {"VulnerabilityID": "CVE-1", "Severity": "HIGH"}
                        ],
                    }
                ],
            }
        ],
    }


def test_import_trivy_and_list(db, tmp_path, monkeypatch):
    # Route the CLI's get_session to the in-memory test session.
    from contextlib import contextmanager

    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.import_.get_session", _fake_session)
    monkeypatch.setattr("cmdb.cli.images.get_session", _fake_session)

    f = tmp_path / "scan.json"
    f.write_text(json.dumps(_envelope()))

    r = runner.invoke(app, ["import", "trivy", str(f)])
    assert r.exit_code == 0, r.output
    assert "1 images" in r.output

    r = runner.invoke(app, ["images", "list"])
    assert r.exit_code == 0
    assert "nginx:latest" in r.output

    r = runner.invoke(app, ["images", "noisy", "nginx:latest", "--on"])
    assert r.exit_code == 0
    assert get_image(db, "nginx:latest").expected_noisy is True


def _import_one(db, tmp_path, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.import_.get_session", _fake_session)
    monkeypatch.setattr("cmdb.cli.images.get_session", _fake_session)
    f = tmp_path / "scan.json"
    f.write_text(json.dumps(_envelope()))
    runner.invoke(app, ["import", "trivy", str(f)])


def test_rm_deletes_image_and_reports_counts(db, tmp_path, monkeypatch):
    _import_one(db, tmp_path, monkeypatch)
    assert get_image(db, "nginx:latest") is not None

    r = runner.invoke(app, ["images", "rm", "nginx:latest", "--yes"])
    assert r.exit_code == 0, r.output
    assert "nginx:latest" in r.output
    assert get_image(db, "nginx:latest") is None


def test_rm_missing_image_errors(db, tmp_path, monkeypatch):
    _import_one(db, tmp_path, monkeypatch)
    r = runner.invoke(app, ["images", "rm", "ghost:1", "--yes"])
    assert r.exit_code != 0
    assert "not found" in r.output


def _envelope_at(ts, images):
    return {
        "scanned_at": ts,
        "trivy_version": "0.72.0",
        "images": [
            {
                "ArtifactName": ref,
                "Metadata": {"ImageID": "sha256:x"},
                "Results": [{"Target": ref, "Vulnerabilities": []}],
            }
            for ref in images
        ],
    }


def test_list_shows_status_and_stale_columns(db, tmp_path, monkeypatch):
    from contextlib import contextmanager

    from rich.console import Console

    from cmdb.domain.models import Container, Host

    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.import_.get_session", _fake_session)
    monkeypatch.setattr("cmdb.cli.images.get_session", _fake_session)
    # Wide console so table cells don't wrap mid-word in assertions.
    monkeypatch.setattr("cmdb.cli.images.console", Console(width=200))

    f1 = tmp_path / "s1.json"
    f1.write_text(json.dumps(_envelope_at("2026-07-01T04:00:00Z", ["nginx:latest", "old:1"])))
    f2 = tmp_path / "s2.json"
    f2.write_text(json.dumps(_envelope_at("2026-07-05T04:00:00Z", ["nginx:latest"])))
    runner.invoke(app, ["import", "trivy", str(f1)])
    runner.invoke(app, ["import", "trivy", str(f2)])

    host = Host(machine_id="m1", hostname="testhost")
    db.add(host)
    db.flush()
    db.add(Container(host_id=host.id, name="web", image="nginx:latest", state="running"))
    db.flush()

    r = runner.invoke(app, ["images", "list"])
    assert r.exit_code == 0, r.output
    nginx_line = next(l for l in r.output.splitlines() if "nginx:latest" in l)
    old_line = next(l for l in r.output.splitlines() if "old:1" in l)
    assert "running" in nginx_line
    assert "registry-only" in old_line
    assert "yes" in old_line  # stale: dropped from the newest docker run
    assert "yes" not in nginx_line
