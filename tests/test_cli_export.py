import json
from contextlib import contextmanager

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cmdb.cli.main import app as cli_app
from cmdb.domain.models import Host
from cmdb.domain.services.ansible import import_host
from cmdb.web.app import app as web_app
from cmdb.web.deps import get_db_dep

runner = CliRunner()


def _patch_session(db, monkeypatch):
    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.export.get_session", _fake_session)


def test_export_to_stdout_and_file(db, host_facts, tmp_path, monkeypatch):
    _patch_session(db, monkeypatch)
    import_host(db, host_facts)

    r = runner.invoke(cli_app, ["export"])
    assert r.exit_code == 0, r.output
    dump = json.loads(r.output)
    assert dump["tables"]["hosts"][0]["hostname"] == "testhost"

    out = tmp_path / "dump.json"
    r = runner.invoke(cli_app, ["export", str(out)])
    assert r.exit_code == 0, r.output
    assert json.loads(out.read_text())["version"] == 1


def test_export_yaml(db, host_facts, tmp_path, monkeypatch):
    _patch_session(db, monkeypatch)
    import_host(db, host_facts)
    out = tmp_path / "dump.yaml"
    r = runner.invoke(cli_app, ["export", str(out), "--format", "yaml"])
    assert r.exit_code == 0, r.output
    import yaml

    assert yaml.safe_load(out.read_text())["version"] == 1


def test_restore_cli_round_trip(db, host_facts, tmp_path, monkeypatch):
    _patch_session(db, monkeypatch)
    import_host(db, host_facts)
    out = tmp_path / "dump.json"
    runner.invoke(cli_app, ["export", str(out)])

    # fresh empty DB for the restore
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from cmdb.domain.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db2 = sessionmaker(bind=engine, autoflush=False)()
    _patch_session(db2, monkeypatch)

    r = runner.invoke(cli_app, ["restore", str(out)])
    assert r.exit_code == 0, r.output
    assert db2.query(Host).one().hostname == "testhost"

    # non-empty without --force fails cleanly
    r = runner.invoke(cli_app, ["restore", str(out)])
    assert r.exit_code == 1
    assert "not empty" in r.output
    db2.close()


def test_web_download_export(db, host_facts):
    web_app.dependency_overrides[get_db_dep] = lambda: db
    try:
        import_host(db, host_facts)
        client = TestClient(web_app)
        r = client.get("/generate/download/cmdb.json")
        assert r.status_code == 200
        assert r.headers["content-disposition"].endswith("filename=cmdb.json")
        assert r.json()["tables"]["hosts"][0]["hostname"] == "testhost"
    finally:
        web_app.dependency_overrides.clear()
