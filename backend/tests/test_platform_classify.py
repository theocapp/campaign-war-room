"""Tests for platform_classify.derive_platform.

Cases are drawn from real rows in the noctua corpus (2026-05-31 audit):
Twitter arrives via nitter.net RSS, "Mastodon" hashtag rows are sometimes
bridged Bluesky posts at bsky.brid.gy, etc.
"""
from __future__ import annotations

from app.services.platform_classify import (
    derive_platform, TWITTER, BLUESKY, REDDIT, YOUTUBE, MASTODON,
    FACEBOOK, INSTAGRAM,
)


def test_nitter_url_is_twitter():
    assert derive_platform(
        "https://nitter.net/PaigeGCognetti/status/2060413474811904307#m",
        "Paige Cognetti X/Twitter (@PaigeGCognetti)",
    ) == TWITTER


def test_other_nitter_instances_are_twitter():
    assert derive_platform("https://nitter.poast.org/RepBresnahan/status/1", None) == TWITTER
    assert derive_platform("https://twiiit.com/someone/status/1", None) == TWITTER


def test_x_and_twitter_dot_com_are_twitter():
    assert derive_platform("https://x.com/PaigeGCognetti/status/1", None) == TWITTER
    assert derive_platform("https://twitter.com/PaigeGCognetti/status/1", None) == TWITTER


def test_bridged_bluesky_classifies_as_bluesky_not_mastodon():
    # source_name says "Mastodon ... via mastodon.social" but the post lives
    # on Bluesky via the brid.gy bridge. URL is ground truth.
    assert derive_platform(
        "https://bsky.brid.gy/r/https://bsky.app/profile/x/post/y",
        "Mastodon #PA08 via mastodon.social",
    ) == BLUESKY


def test_fed_bridgy_bluesky_origin_is_bluesky():
    # The fediverse bridge wraps a bsky.app origin in the path; name says
    # Mastodon but the post lives on Bluesky.
    assert derive_platform(
        "https://fed.brid.gy/r/https://bsky.app/profile/did:plc:abc/post/xyz",
        "Mastodon #PA08 via mastodon.social",
    ) == BLUESKY


def test_bsky_app_is_bluesky():
    assert derive_platform("https://bsky.app/profile/did:plc:abc/post/xyz", None) == BLUESKY


def test_true_mastodon_instance_is_mastodon():
    assert derive_platform("https://c.im/@renespronk/116356284248340682", "Mastodon #PA08 via mastodon.social") == MASTODON
    assert derive_platform("https://mastodon.social/@user/123", None) == MASTODON


def test_reddit():
    assert derive_platform("https://www.reddit.com/r/Scranton/comments/abc/", "Reddit r/Scranton") == REDDIT
    assert derive_platform("https://redd.it/abc123", None) == REDDIT


def test_youtube():
    assert derive_platform("https://www.youtube.com/watch?v=abc", "YouTube: Cognetti") == YOUTUBE
    assert derive_platform("https://youtu.be/abc", None) == YOUTUBE


def test_facebook_instagram():
    assert derive_platform("https://www.facebook.com/somepage/posts/1", None) == FACEBOOK
    assert derive_platform("https://www.instagram.com/p/abc/", None) == INSTAGRAM


def test_plain_news_is_none():
    assert derive_platform("https://www.politicspa.com/pa-08-race-toss-up/12345/", "PoliticsPA") is None
    assert derive_platform("https://apnews.com/article/abc", "Associated Press") is None


def test_google_news_redirect_is_none_without_platform_signal():
    # A Google News wrapper that hasn't resolved to a platform domain and
    # whose name carries no platform marker is just news.
    assert derive_platform("https://news.google.com/rss/articles/CBMi...", "Google News: Rob Bresnahan") is None


def test_name_fallback_when_url_missing():
    assert derive_platform(None, "Rob Bresnahan X/Twitter profile") == TWITTER
    assert derive_platform("", "Bluesky firehose (matched: bresnahan)") == BLUESKY
    assert derive_platform(None, "Mastodon #Cognetti via mastodon.world") == MASTODON


def test_url_beats_name_for_bridged_content():
    # Even if the name screams Mastodon, a bsky URL wins.
    assert derive_platform("https://bsky.brid.gy/r/https://bsky.app/x", "Mastodon thing") == BLUESKY


def test_garbage_inputs():
    assert derive_platform(None, None) is None
    assert derive_platform("", "") is None
    assert derive_platform("not a url", None) is None
