import uvicorn
from typer.testing import CliRunner

from cmdb.cli.main import app

runner = CliRunner()


def _capture_uvicorn(monkeypatch):
    calls = {}

    def _fake_run(fastapi_app, host, port):
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    return calls


def test_serve_uses_settings_when_no_flags(monkeypatch):
    # CMDB_HOST/CMDB_PORT (surfaced via settings) drive the bind when no flags given.
    monkeypatch.setattr("cmdb.cli.main.settings.host", "127.0.0.1")
    monkeypatch.setattr("cmdb.cli.main.settings.port", 9999)
    calls = _capture_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0, result.output
    assert calls == {"host": "127.0.0.1", "port": 9999}


def test_serve_flags_override_settings(monkeypatch):
    # Explicit --host/--port win over settings/env.
    monkeypatch.setattr("cmdb.cli.main.settings.host", "127.0.0.1")
    monkeypatch.setattr("cmdb.cli.main.settings.port", 9999)
    calls = _capture_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "1234"])

    assert result.exit_code == 0, result.output
    assert calls == {"host": "0.0.0.0", "port": 1234}
