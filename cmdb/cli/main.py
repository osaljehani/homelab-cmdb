import typer

from cmdb.cli import hosts, import_, collect, k8s, generate, db as db_cli

app = typer.Typer(name="cmdb", help="Homelab CMDB", no_args_is_help=True)
app.add_typer(hosts.app, name="hosts")
app.add_typer(import_.app, name="import")
app.add_typer(collect.app, name="collect")
app.add_typer(k8s.app, name="k8s")
app.add_typer(generate.app, name="generate")
app.add_typer(db_cli.app, name="db")


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8080, help="Port"),
) -> None:
    """Start the web UI."""
    import uvicorn
    from cmdb.web.app import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port)


@app.command("mcp")
def mcp() -> None:
    """Run the MCP server (stdio transport) for LLM clients like Claude Code."""
    from cmdb.mcp.server import serve

    serve()


if __name__ == "__main__":
    app()
