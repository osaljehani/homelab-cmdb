"""Migrations and DB-path resolution are independent of the launch directory.

Regression coverage for the MCP server being started via
`uv run --project <repo> cmdb mcp` from a cwd that is not the repo: Alembic's
relative `script_location` and the relative default DB path must both resolve
against the repo root, not the current working directory.
"""

from sqlalchemy import create_engine, inspect


def test_run_migrations_works_from_any_cwd(tmp_path, monkeypatch):
    import cmdb.config

    db_file = tmp_path / "from-elsewhere.db"
    # env.py reads the settings singleton (loaded once at process start), so point
    # it at the temp DB rather than relying on env-var re-read.
    monkeypatch.setattr(cmdb.config.settings, "db_path", str(db_file))
    # Launch from a directory that is not the repo root.
    monkeypatch.chdir(tmp_path)

    from cmdb.db import run_migrations

    run_migrations()  # must not raise on a relative script_location

    engine = create_engine(f"sqlite:///{db_file}")
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "alembic_version" in tables
    assert "hosts" in tables


def test_relative_db_path_resolves_against_repo_root(monkeypatch):
    monkeypatch.setenv("CMDB_DB_PATH", "data/cmdb.db")

    from cmdb.config import PROJECT_ROOT, Settings

    url = Settings().db_url
    assert url == f"sqlite:///{PROJECT_ROOT / 'data' / 'cmdb.db'}"


def test_absolute_db_path_is_left_unchanged(monkeypatch):
    monkeypatch.setenv("CMDB_DB_PATH", "/var/lib/cmdb/cmdb.db")

    from cmdb.config import Settings

    assert Settings().db_url == "sqlite:////var/lib/cmdb/cmdb.db"
