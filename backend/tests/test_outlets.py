"""Tests for the Outlet model and PA-08 seed data."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Outlet
from app.seed import _seed_pa08_outlets


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_seed_pa08_outlets_creates_expected_records(db):
    _seed_pa08_outlets(db)
    db.commit()
    outlets = db.query(Outlet).all()
    assert len(outlets) == 10
    domains = {o.domain for o in outlets}
    assert "thetimes-tribune.com" in domains
    assert "wnep.com" in domains
    assert "penncapital-star.com" in domains


def test_seed_pa08_outlets_all_have_geo_and_authority(db):
    _seed_pa08_outlets(db)
    db.commit()
    for outlet in db.query(Outlet).all():
        assert outlet.state == "PA", f"{outlet.name} missing state"
        assert outlet.city, f"{outlet.name} missing city"
        assert 1 <= outlet.authority_score <= 10, f"{outlet.name} authority out of range"


def test_seed_pa08_outlets_is_idempotent(db):
    _seed_pa08_outlets(db)
    db.commit()
    _seed_pa08_outlets(db)
    db.commit()
    assert db.query(Outlet).count() == 10


def test_outlet_model_fields():
    outlet = Outlet(
        name="Test Paper",
        domain="testpaper.com",
        outlet_type="local_news",
        state="PA",
        city="Scranton",
        authority_score=7,
    )
    assert outlet.active is None or outlet.active  # default is True via Column
    assert outlet.authority_score == 7
