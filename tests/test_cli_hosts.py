from contextlib import contextmanager

from typer.testing import CliRunner

from cmdb.cli.main import app
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import get_host

runner = CliRunner()


def _patch_session(db, monkeypatch):
    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.hosts.get_session", _fake_session)


def test_note_set_show_clear(db, host_facts, monkeypatch):
    _patch_session(db, monkeypatch)
    import_host(db, host_facts)

    r = runner.invoke(app, ["hosts", "note", "testhost", "rack 2"])
    assert r.exit_code == 0, r.output
    assert get_host(db, "testhost").notes == "rack 2"

    r = runner.invoke(app, ["hosts", "note", "testhost"])
    assert "rack 2" in r.output

    r = runner.invoke(app, ["hosts", "note", "testhost", "--clear"])
    assert r.exit_code == 0
    assert get_host(db, "testhost").notes is None


def test_note_unknown_host_errors(db, monkeypatch):
    _patch_session(db, monkeypatch)
    r = runner.invoke(app, ["hosts", "note", "ghost"])
    assert r.exit_code != 0


def test_set_and_unset_field(db, host_facts, monkeypatch):
    _patch_session(db, monkeypatch)
    import_host(db, host_facts)

    r = runner.invoke(app, ["hosts", "set-field", "testhost", "owner", "alice"])
    assert r.exit_code == 0, r.output
    assert get_host(db, "testhost").custom_fields == {"owner": "alice"}

    r = runner.invoke(app, ["hosts", "unset-field", "testhost", "owner"])
    assert r.exit_code == 0
    assert get_host(db, "testhost").custom_fields == {}
