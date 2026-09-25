from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Generator
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from cmdb.db.session import get_db
from cmdb.domain.services.security import host_posture

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

try:
    ASSET_VERSION = version("homelab-cmdb")
except PackageNotFoundError:
    ASSET_VERSION = "dev"

def _auth_context(request) -> dict:
    """Expose the authenticated principal to every template.

    Set on the scope by cmdb.web.auth.middleware, so it is absent in `none` mode
    and on the public login page -- templates must treat it as optional.
    """
    return {"principal": getattr(request.state, "principal", None)}


templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[_auth_context]
)
templates.env.globals["host_posture"] = host_posture
templates.env.globals["asset_version"] = ASSET_VERSION


def get_db_dep() -> Generator[Session, None, None]:
    """FastAPI dependency yields a DB session."""
    yield from get_db()
