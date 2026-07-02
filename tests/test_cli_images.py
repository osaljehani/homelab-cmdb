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
