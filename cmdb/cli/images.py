import typer
from rich.console import Console
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.services import images as images_svc

app = typer.Typer(help="Container image vulnerability data")
console = Console()


@app.command("list")
def list_images() -> None:
    """List scanned images with their latest severity counts."""
    table = Table("Image", "Crit", "High", "Med", "Low", "Total", "Scanned", "Noisy")
    with get_session() as session:
        for image in images_svc.list_images(session):
            scan = images_svc.latest_scan(session, image)
            if scan:
                table.add_row(
                    image.ref,
                    str(scan.critical),
                    str(scan.high),
                    str(scan.medium),
                    str(scan.low),
                    str(scan.total),
                    scan.scanned_at.strftime("%Y-%m-%d"),
                    "yes" if image.expected_noisy else "",
                )
            else:
                table.add_row(
                    image.ref,
                    "-",
                    "-",
                    "-",
                    "-",
                    "-",
                    "never",
                    "yes" if image.expected_noisy else "",
                )
    console.print(table)


@app.command("noisy")
def set_noisy(
    ref: str = typer.Argument(..., help="Image ref, e.g. hexstrike:latest"),
    on: bool = typer.Option(
        False, "--on/--off", help="Mark the image as expected-noisy (or clear it)"
    ),
) -> None:
    """Flag an image as expected-noisy (excluded from 'needs attention' rollups)."""
    with get_session() as session:
        image = images_svc.set_noisy(session, ref, on)
        state = "expected-noisy" if image.expected_noisy else "normal"
    console.print(f"[green]{ref}[/green] is now {state}")
