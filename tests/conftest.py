import json
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cmdb.domain.models import Base


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def blade14_facts() -> dict:
    path = Path(__file__).parent / "fixtures" / "blade14.json"
    return json.loads(path.read_text())


@pytest.fixture
def blade14_facts_alt() -> dict:
    """Same machine_id as blade14, different hostname — for upsert tests."""
    path = Path(__file__).parent / "fixtures" / "blade14.json"
    data = json.loads(path.read_text())
    data["ansible_facts"]["ansible_hostname"] = "blade14-renamed"
    return data
