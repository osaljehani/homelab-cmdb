from pathlib import Path
from typing import Generator
from fastapi import Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from cmdb.db.session import get_db

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db_dep() -> Generator[Session, None, None]:
    """FastAPI dependency   yields a DB session."""
    yield from get_db()
