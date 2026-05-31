"""Unit tests for app.services.article_body_recovery.

The recovery flow can't be end-to-end tested against the live Google News
endpoint (Google geo-locks responses and breaks decoder libraries
periodically). We mock the HTTP boundary and assert on control flow:

- URL classification (is_google_news_redirect)
- Base64 extraction from the various Google News URL shapes
- Decoder graceful-degradation when params are missing
- Recovery short-circuits when raw_text is already long enough
- Recovery returns the resolved_url even when body fetch fails so
  downstream paths (YouTube transcript fetcher) can still use it
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services import article_body_recovery as rec


# ── is_google_news_redirect ────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://news.google.com/articles/CBMiVkFV...",
    "https://news.google.com/rss/articles/CBMihwFB...",
    "https://news.google.com/read/CBMi...",
    "https://news.google.com/rss/read/CBMi...",
])
def test_classify_google_news_url(url):
    assert rec.is_google_news_redirect(url) is True


@pytest.mark.parametrize("url", [
    None,
    "",
    "https://www.cnn.com/2026/05/29/politics/article.html",
    "https://news.google.com/",                       # no article path
    "https://news.google.com/topics/foo",             # not an article
    "https://www.google.com/news",                    # not the news subdomain
    "https://www.timesleader.com/news/1743868/foo",
    "not even a url",
])
def test_classify_non_google_news_url(url):
    assert rec.is_google_news_redirect(url) is False


# ── _extract_base64_id ────────────────────────────────────────────────────

def test_base64_extraction_strips_trailing_query():
    url = "https://news.google.com/rss/articles/CBMiVkFVX3lxTFBURGJxd?oc=5"
    assert rec._extract_base64_id(url) == "CBMiVkFVX3lxTFBURGJxd"


def test_base64_extraction_handles_both_path_shapes():
    a = "https://news.google.com/articles/CBMi_short"
    b = "https://news.google.com/rss/articles/CBMi_short"
    assert rec._extract_base64_id(a) == "CBMi_short"
    assert rec._extract_base64_id(b) == "CBMi_short"


def test_base64_extraction_returns_none_for_non_article_url():
    assert rec._extract_base64_id("https://news.google.com/topics/x") is None
    assert rec._extract_base64_id("not a url") is None


# ── _fetch_decoding_params ────────────────────────────────────────────────

def _mock_httpx_response(status_code: int, text: str):
    """Build a stub httpx.Response without invoking real network code."""
    request = httpx.Request("GET", "https://example.test/")
    return httpx.Response(status_code=status_code, text=text, request=request)


def test_fetch_decoding_params_extracts_sig_and_ts():
    html = '<div data-n-a-sg="SIG_X" data-n-a-ts="123456" jscontroller="ZX"></div>'
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)):
        sig, ts = rec._fetch_decoding_params("CBMi_test")
    assert sig == "SIG_X"
    assert ts == "123456"


def test_fetch_decoding_params_returns_none_when_attributes_missing():
    # This is the post-2026-05-30 reality: page loads but no sig/ts attrs.
    html = '<html><body>no data attributes here</body></html>'
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)):
        sig, ts = rec._fetch_decoding_params("CBMi_test")
    assert sig is None
    assert ts is None


def test_fetch_decoding_params_returns_none_when_consent_redirect():
    # Consent.google.com gate — 200 status but the body is the consent page.
    html = '<html><body class="consent">consent page</body></html>'
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)):
        sig, ts = rec._fetch_decoding_params("CBMi_test")
    assert sig is None
    assert ts is None


def test_fetch_decoding_params_returns_none_on_http_error():
    def boom(*args, **kwargs):
        raise httpx.TimeoutException("simulated")
    with patch("app.services.article_body_recovery.httpx.get", side_effect=boom):
        sig, ts = rec._fetch_decoding_params("CBMi_test")
    assert sig is None
    assert ts is None


# ── _decode_via_batchexecute ───────────────────────────────────────────────

def test_decode_via_batchexecute_parses_publisher_url():
    # Real Google response is `)]}'\n\n` then a JSON array with the
    # `wrb.fr` row carrying our payload + trailing metadata rows.
    # The decoder slices off the last 2 metadata rows ([:-2]).
    body = (
        ")]}'\n\n"
        '['
        '["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.cnn.com/2026/article-x\\",1]",null,null,null,"generic"],'
        '["di",15],'
        '["af.httprm",14,"-1234",10]'
        ']'
    )
    with patch("app.services.article_body_recovery.httpx.post",
               return_value=_mock_httpx_response(200, body)):
        url = rec._decode_via_batchexecute("CBMi_test", "SIG_X", "123456")
    assert url == "https://www.cnn.com/2026/article-x"


def test_decode_via_batchexecute_returns_none_on_malformed_response():
    with patch("app.services.article_body_recovery.httpx.post",
               return_value=_mock_httpx_response(200, "garbage")):
        url = rec._decode_via_batchexecute("CBMi_test", "SIG_X", "123456")
    assert url is None


def test_decode_via_batchexecute_returns_none_on_http_error():
    def boom(*args, **kwargs):
        raise httpx.HTTPError("simulated")
    with patch("app.services.article_body_recovery.httpx.post", side_effect=boom):
        url = rec._decode_via_batchexecute("CBMi_test", "SIG_X", "123456")
    assert url is None


# ── resolve_google_news_url (end-to-end with mocks) ────────────────────────

def test_resolve_returns_none_when_decoding_params_missing():
    """The current Google behavior — page exists but no sig/ts attrs."""
    rec.resolve_google_news_url.cache_clear()
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, "<html>no attrs</html>")):
        result = rec.resolve_google_news_url(
            "https://news.google.com/rss/articles/CBMiTEST?oc=5"
        )
    assert result is None


def test_resolve_returns_publisher_url_when_full_chain_succeeds():
    rec.resolve_google_news_url.cache_clear()
    html = '<div data-n-a-sg="SIG_X" data-n-a-ts="123456"></div>'
    decode_body = (
        ")]}'\n\n"
        '['
        '["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.nbcnews.com/news/article-y\\",1]",null,null,null,"generic"],'
        '["di",15],'
        '["af.httprm",14,"-1234",10]'
        ']'
    )
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)):
        with patch("app.services.article_body_recovery.httpx.post",
                   return_value=_mock_httpx_response(200, decode_body)):
            result = rec.resolve_google_news_url(
                "https://news.google.com/articles/CBMiUNIQ_TOKEN_FOR_CACHE"
            )
    assert result == "https://www.nbcnews.com/news/article-y"


def test_resolve_returns_none_for_non_gnews_url():
    rec.resolve_google_news_url.cache_clear()
    result = rec.resolve_google_news_url("https://www.cnn.com/some/article")
    assert result is None


# ── recover_body integration ───────────────────────────────────────────────

def test_recover_short_circuits_on_long_raw_text():
    """We don't waste an HTTP roundtrip when raw_text is already long."""
    long_text = "x" * (rec.RECOVERY_THRESHOLD_CHARS + 1)
    body, url = rec.recover_body(
        "https://news.google.com/rss/articles/CBMiTEST",
        long_text,
    )
    assert body is None
    assert url is None


def test_recover_attempts_direct_fetch_for_publisher_url():
    """When rss_link is already a publisher URL and raw_text is short,
    we try a body fetch directly — no Google News decode step."""
    with patch("app.services.article_body_recovery.fetch_publisher_body",
               return_value="Recovered article body content here.") as fetch:
        body, url = rec.recover_body(
            "https://www.timesleader.com/news/1743868/foo",
            "short rss snippet",
        )
    assert body == "Recovered article body content here."
    assert url is None  # no decode happened
    fetch.assert_called_once_with("https://www.timesleader.com/news/1743868/foo")


def test_recover_returns_resolved_url_even_when_body_fetch_fails():
    """The YouTube transcript path needs the underlying youtube.com URL
    even if we can't fetch the article body. Make sure we surface it."""
    rec.resolve_google_news_url.cache_clear()
    with patch("app.services.article_body_recovery.resolve_google_news_url",
               return_value="https://www.youtube.com/watch?v=AbCdEfGhIjK"):
        with patch("app.services.article_body_recovery.fetch_publisher_body",
                   return_value=None):
            body, url = rec.recover_body(
                "https://news.google.com/rss/articles/CBMiYTSAMPLE",
                "short rss snippet",
            )
    assert body is None
    assert url == "https://www.youtube.com/watch?v=AbCdEfGhIjK"


def test_recover_returns_none_when_gnews_decode_fails():
    rec.resolve_google_news_url.cache_clear()
    with patch("app.services.article_body_recovery.resolve_google_news_url",
               return_value=None):
        body, url = rec.recover_body(
            "https://news.google.com/rss/articles/CBMiZUNDECODABLE",
            "short rss snippet",
        )
    assert body is None
    assert url is None


# ── Title-based publisher search ─────────────────────────────────────────

def test_recover_falls_back_to_publisher_search_when_decoder_fails():
    """When the Google News decoder returns None but publisher_domain +
    title are supplied, recover_body should call the title-search path."""
    rec.resolve_google_news_url.cache_clear()
    with patch("app.services.article_body_recovery.resolve_google_news_url",
               return_value=None):
        with patch("app.services.article_body_recovery.search_publisher_for_article",
                   return_value="https://timesleader.com/news/12345/veterans-bill"):
            with patch("app.services.article_body_recovery.fetch_publisher_body",
                       return_value="Recovered body via title search.") as fetch:
                body, url = rec.recover_body(
                    "https://news.google.com/rss/articles/CBMiDECODER_FAIL",
                    "short stub",
                    publisher_domain="timesleader.com",
                    title="Bresnahan introduces veterans bill - Times Leader",
                )
    assert body == "Recovered body via title search."
    assert url == "https://timesleader.com/news/12345/veterans-bill"
    fetch.assert_called_once_with("https://timesleader.com/news/12345/veterans-bill")


def test_recover_skips_publisher_search_when_no_publisher_or_title():
    """If publisher_domain isn't known, we don't have anywhere to search.
    Return None instead of trying anyway."""
    rec.resolve_google_news_url.cache_clear()
    with patch("app.services.article_body_recovery.resolve_google_news_url",
               return_value=None):
        # No publisher_domain
        body, url = rec.recover_body(
            "https://news.google.com/rss/articles/CBMi_NO_PUB",
            "short stub",
            publisher_domain=None,
            title="Some title here",
        )
    assert body is None
    assert url is None


def test_search_normalize_strips_publisher_suffix():
    assert rec._normalize_search_title("Bresnahan honors mayor - Times Leader") == "Bresnahan honors mayor"
    assert rec._normalize_search_title("Title without suffix") == "Title without suffix"
    assert rec._normalize_search_title("") == ""
    assert rec._normalize_search_title(None) == ""


def test_search_title_similarity_handles_punctuation_variance():
    a = "Bresnahan honors Mayor Walter Mitchell on retirement"
    b = "Bresnahan honors Mayor Walter Mitchell on his retirement"
    assert rec._title_similarity(a, b) >= 0.85


def test_extract_candidate_links_filters_non_article_paths():
    """Search result HTML has many links; we want only article URLs."""
    html = """
    <a href="/">Home</a>
    <a href="/category/news/">News category</a>
    <a href="/tag/bresnahan/">Tag page</a>
    <a href="/news/12345/bresnahan-veterans-bill">First result</a>
    <a href="/news/67890/another-article">Second result</a>
    <a href="https://twitter.com/example">External</a>
    <a href="/wp-admin/login">Admin</a>
    """
    out = rec._extract_candidate_links_from_html(html, "timesleader.com")
    assert "https://timesleader.com/news/12345/bresnahan-veterans-bill" in out
    assert "https://timesleader.com/news/67890/another-article" in out
    # Non-article paths excluded
    assert not any("/category/" in u for u in out)
    assert not any("/tag/" in u for u in out)
    assert not any("/wp-admin/" in u for u in out)
    # External domains excluded
    assert not any("twitter.com" in u for u in out)


def test_extract_candidate_links_caps_at_max():
    """Respect the per-strategy candidate cap so we don't fetch 50 URLs."""
    links = "\n".join(
        f'<a href="/news/{i}/article-{i}">item {i}</a>' for i in range(20)
    )
    out = rec._extract_candidate_links_from_html(links, "timesleader.com")
    assert len(out) == rec._MAX_CANDIDATES_PER_STRATEGY


def test_verify_candidate_url_accepts_close_title_match():
    """Verification should pass when the page title matches the
    expected title with similarity >= threshold."""
    # Realistic HTML with an actual extractable body.
    body_paragraphs = "<p>" + " ".join(["meaningful content"] * 40) + "</p>"
    html = f"""
    <html><head><title>Bresnahan introduces veterans bill - Times Leader</title></head>
    <body><article><h1>Bresnahan introduces veterans bill</h1>{body_paragraphs}</article></body></html>
    """
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)) as get_call:
        # Need to also set content-type
        get_call.return_value.headers = {"content-type": "text/html"}
        result = rec._verify_candidate_url(
            "https://timesleader.com/news/123/veterans-bill",
            "Bresnahan introduces veterans bill - Times Leader",
        )
    assert result is not None
    assert "meaningful content" in result


def test_verify_candidate_url_rejects_mismatched_title():
    """Verification rejects when titles diverge — guards against the
    publisher's search returning an unrelated article that shared keywords."""
    body_paragraphs = "<p>" + " ".join(["healthcare bill discussion"] * 40) + "</p>"
    html = f"""
    <html><head><title>Cognetti unveils healthcare proposal</title></head>
    <body><article><h1>Cognetti unveils healthcare proposal</h1>{body_paragraphs}</article></body></html>
    """
    with patch("app.services.article_body_recovery.httpx.get",
               return_value=_mock_httpx_response(200, html)) as get_call:
        get_call.return_value.headers = {"content-type": "text/html"}
        result = rec._verify_candidate_url(
            "https://timesleader.com/news/999/some-unrelated",
            "Bresnahan introduces veterans bill",
        )
    assert result is None


def test_search_publisher_returns_none_when_search_fails():
    """If both WP and generic /search? endpoints fail, fall through to
    sitemap. If that also fails (or yields no candidates), return None."""
    def boom(*args, **kwargs):
        raise httpx.HTTPError("403 Forbidden")
    with patch("app.services.article_body_recovery.httpx.get", side_effect=boom):
        result = rec.search_publisher_for_article(
            "citizensvoice.com",  # the WAF-blocked one
            "Bresnahan announces something with substantive content",
        )
    assert result is None


def test_search_publisher_returns_none_for_empty_inputs():
    assert rec.search_publisher_for_article("", "Some title") is None
    assert rec.search_publisher_for_article("timesleader.com", "") is None
    assert rec.search_publisher_for_article("timesleader.com", None) is None
