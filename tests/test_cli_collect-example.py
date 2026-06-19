import contextlib

from typer.testing import CliRunner

from cmdb.cli import collect as cli_collect
from cmdb.domain.models import ImportLog, ImportSource

runner = CliRunner()


def _fake_log(**kw):
    return ImportLog(source=ImportSource.COLLECT, filename="x", **kw)


def test_cli_collect_ports(monkeypatch):
    called = {}

    def fake_collect_ports(session, inventory, limit, source):
        called["ran"] = True
        return _fake_log(listening_ports_upserted=3)

    # get_session is a context manager yielding a session
    monkeypatch.setattr(cli_collect, "collect_ports", fake_collect_ports)
    monkeypatch.setattr(cli_collect, "get_session", lambda: contextlib.nullcontext(None))
    result = runner.invoke(cli_collect.app, ["ports"])
    assert result.exit_code == 0
    assert called.get("ran")
    assert "3" in result.stdout


def test_cli_collect_tailscale(monkeypatch):
    def fake(session, inventory, limit, source):
        return _fake_log(tailscale_services_upserted=2, hosts_upserted=1)

    monkeypatch.setattr(cli_collect, "collect_tailscale", fake)
    monkeypatch.setattr(cli_collect, "get_session", lambda: contextlib.nullcontext(None))
    result = runner.invoke(cli_collect.app, ["tailscale"])
    assert result.exit_code == 0
    assert "2" in result.stdout
