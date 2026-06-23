from cmdb.config import PROJECT_ROOT


def run_migrations() -> None:
    """Upgrade the database to the latest Alembic revision.

    Uses absolute paths for both the ini file and ``script_location`` so it works
    no matter the current working directory. This matters when the MCP server is
    started via ``uv run --project <repo> cmdb mcp`` from a client whose cwd is
    not the repo: ``uv --project`` does not chdir, and a relative
    ``script_location`` would otherwise be resolved against the wrong directory.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(PROJECT_ROOT / "cmdb" / "db" / "migrations")
    )
    command.upgrade(cfg, "head")
