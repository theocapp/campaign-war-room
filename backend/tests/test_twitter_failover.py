"""Twitter/Nitter self-healing failover.

The failure mode under test: a Nitter feed is pinned to one instance at
registration time and never revisited, so when that instance dies the feed
goes silently quiet. refresh_stale_twitter_feeds() re-probes each feed's
current instance and migrates dead ones to a working host.

Network probing is stubbed — these tests must never hit a real Nitter host.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import RssFeed
from app.services import twitter_scraper as ts


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_is_nitter_feed_url_recognizes_managed_hosts():
    assert ts._is_nitter_feed_url("https://nitter.net/RepBresnahan/rss")
    assert ts._is_nitter_feed_url("https://nitter.poast.org/x/rss")
    assert ts._is_nitter_feed_url("https://twiiit.com/x/rss")
    # A host no longer in NITTER_INSTANCES but clearly a nitter mirror still
    # counts — that's the stale-feed case we must still recognize to heal it.
    assert ts._is_nitter_feed_url("https://nitter.dead-instance.example/x/rss")


def test_is_nitter_feed_url_rejects_non_nitter():
    assert not ts._is_nitter_feed_url("https://www.youtube.com/feeds/videos.xml?x")
    assert not ts._is_nitter_feed_url("https://example.com/rss")
    assert not ts._is_nitter_feed_url(None)
    assert not ts._is_nitter_feed_url("")


def test_username_extraction_from_feed_url():
    assert ts._username_from_nitter_url("https://nitter.net/PaigeGCognetti/rss") == "PaigeGCognetti"
    assert ts._username_from_nitter_url("https://x.example/Rep_Bresnahan/rss") == "Rep_Bresnahan"
    assert ts._username_from_nitter_url("https://nitter.net/PaigeGCognetti") is None


def test_healthy_feed_left_untouched(db, monkeypatch):
    db.add(RssFeed(name="Cognetti X", url="https://nitter.net/PaigeGCognetti/rss", source_type="social"))
    db.commit()
    # Current instance still serves the feed.
    monkeypatch.setattr(ts, "_probe_nitter_instance", lambda host, user: f"https://{host}/{user}/rss")
    monkeypatch.setattr(ts, "resolve_nitter_rss", lambda user: pytest.fail("should not re-resolve a healthy feed"))

    stats = ts.refresh_stale_twitter_feeds(db)
    assert stats == {"checked": 1, "healthy": 1, "migrated": 0, "dead": 0}
    assert db.query(RssFeed).one().url == "https://nitter.net/PaigeGCognetti/rss"


def test_stale_feed_migrates_to_working_instance(db, monkeypatch):
    db.add(RssFeed(name="Cognetti X", url="https://nitter.net/PaigeGCognetti/rss", source_type="social"))
    db.commit()
    # Current instance (nitter.net) is dark; a different one resolves.
    monkeypatch.setattr(ts, "_probe_nitter_instance", lambda host, user: None)
    monkeypatch.setattr(
        ts, "resolve_nitter_rss",
        lambda user: f"https://nitter.poast.org/{user}/rss",
    )

    stats = ts.refresh_stale_twitter_feeds(db)
    assert stats == {"checked": 1, "healthy": 0, "migrated": 1, "dead": 0}
    assert db.query(RssFeed).one().url == "https://nitter.poast.org/PaigeGCognetti/rss"


def test_stale_feed_with_no_working_instance_is_left_as_is(db, monkeypatch):
    db.add(RssFeed(name="Cognetti X", url="https://nitter.net/PaigeGCognetti/rss", source_type="social"))
    db.commit()
    # Everything is down — re-resolution returns nothing.
    monkeypatch.setattr(ts, "_probe_nitter_instance", lambda host, user: None)
    monkeypatch.setattr(ts, "resolve_nitter_rss", lambda user: None)

    stats = ts.refresh_stale_twitter_feeds(db)
    assert stats == {"checked": 1, "healthy": 0, "migrated": 0, "dead": 1}
    # URL preserved so it can recover on a later run.
    assert db.query(RssFeed).one().url == "https://nitter.net/PaigeGCognetti/rss"


def test_non_nitter_feeds_are_ignored(db, monkeypatch):
    db.add(RssFeed(name="YouTube", url="https://www.youtube.com/feeds/videos.xml?channel_id=x", source_type="news"))
    db.add(RssFeed(name="Plain", url="https://example.com/rss", source_type="news"))
    db.commit()
    monkeypatch.setattr(ts, "_probe_nitter_instance", lambda host, user: pytest.fail("should not probe non-nitter feeds"))

    stats = ts.refresh_stale_twitter_feeds(db)
    assert stats == {"checked": 0, "healthy": 0, "migrated": 0, "dead": 0}


def test_env_override_of_instance_list(monkeypatch):
    monkeypatch.setenv("NITTER_INSTANCES", "nitter.example.org, nitter.poast.org ,")
    assert ts._load_instances() == ["nitter.example.org", "nitter.poast.org"]
    monkeypatch.delenv("NITTER_INSTANCES", raising=False)
    # Falls back to the bundled defaults when unset/empty.
    assert ts._load_instances() == ts._DEFAULT_NITTER_INSTANCES
