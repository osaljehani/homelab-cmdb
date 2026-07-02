import typer

from cmdb.cli import hosts, import_, collect, k8s, generate, db as db_cli, images
from cmdb.config import settings

app = typer.Typer(name="cmdb", help="Homelab CMDB", no_args_is_help=True)
app.add_typer(hosts.app, name="hosts")
app.add_typer(import_.app, name="import")
app.add_typer(collect.app, name="collect")
app.add_typer(k8s.app, name="k8s")
app.add_typer(generate.app, name="generate")
app.add_typer(db_cli.app, name="db")
app.add_typer(images.app, name="images")


@app.command("serve")
def serve(
    host: str | None = typer.Option(None, help="Bind host (default: CMDB_HOST or 0.0.0.0)"),
    port: int | None = typer.Option(None, help="Port (default: CMDB_PORT or 8080)"),
) -> None:
    """Start the web UI."""
    import uvicorn
    from cmdb.web.app import app as fastapi_app

    # Precedence: explicit --host/--port flag > CMDB_HOST/CMDB_PORT (via settings) > default.
    uvicorn.run(
        fastapi_app,
        host=host if host is not None else settings.host,
        port=port if port is not None else settings.port,
    )


@app.command("mcp")
def mcp() -> None:
    """Run the MCP server (stdio transport) for LLM clients like Claude Code."""
    from cmdb.mcp.server import serve

    serve()


if __name__ == "__main__":
    app()
