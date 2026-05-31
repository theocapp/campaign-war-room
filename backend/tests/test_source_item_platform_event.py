"""The SourceItem before_insert/before_update event sets `platform`.

The classifier branches themselves are covered by test_platform_classify.py.
These tests verify the *integration*: that the mapper event fires on every
ORM write path and persists the derived value, so no ingestion call site has
to remember to set it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _add(db, **kw):
    kw.setdefault("title", "t")
    kw.setdefault("source_type", "news")
    item = SourceItem(**kw)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_platform_set_on_insert_from_url(db):
    item = _add(db, source_url="https://nitter.net/RepBresnahan/status/1", source_name="Bresnahan X/Twitter")
    assert item.platform == "twitter"


def test_platform_none_for_plain_news(db):
    item = _add(db, source_url="https://apnews.com/article/abc", source_name="Associated Press")
    assert item.platform is None


def test_platform_from_name_when_url_absent(db):
    item = _add(db, source_url=None, source_name="Bluesky firehose (matched: bresnahan)")
    assert item.platform == "bluesky"


def test_bridged_bluesky_url_beats_mastodon_name(db):
    # source_name says Mastodon, but the post lives on Bluesky via brid.gy.
    item = _add(
        db,
        source_url="https://fed.brid.gy/r/https://bsky.app/profile/did:plc:x/post/y",
        source_name="Mastodon #PA08 via mastodon.social",
    )
    assert item.platform == "bluesky"


def test_platform_recomputed_on_update(db):
    # Insert as plain news (platform NULL), then point the URL at a real
    # platform — before_update must re-derive.
    item = _add(db, source_url="https://example.com/story", source_name="Example")
    assert item.platform is None

    item.source_url = "https://www.youtube.com/watch?v=abc"
    db.commit()
    db.refresh(item)
    assert item.platform == "youtube"


def test_platform_cleared_on_update_when_signal_removed(db):
    # If a row's platform signal goes away on update, the tag clears — the
    # event is the single source of truth, not a write-once stamp.
    item = _add(db, source_url="https://www.reddit.com/r/Scranton/comments/a/", source_name="Reddit r/Scranton")
    assert item.platform == "reddit"

    item.source_url = "https://example.com/plain"
    item.source_name = "Example News"
    db.commit()
    db.refresh(item)
    assert item.platform is None
