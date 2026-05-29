"""Short-lived probe of the Bluesky firehose. Connects, watches N seconds,
prints stats, exits. No SourceItem writes — overrides the commit step so
we can inspect what WOULD be ingested without polluting the DB.

Run: python -m app.scripts.bluesky_firehose_probe [seconds]
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Load .env so the running app's secrets are available.
env_file = _BACKEND / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


async def main(duration_s: int):
    import websockets
    from app.db import SessionLocal
    from app.services.bluesky_firehose import (
        _JETSTREAM_URL, _build_keyword_set, _post_matches, _event_to_fields,
    )

    print(f"Connecting to jetstream for {duration_s} seconds…")
    with SessionLocal() as db:
        kws = _build_keyword_set(db)
    # Optional: a comma-separated SANITY_KWS env var injects high-volume
    # test keywords so we can validate the parse/match/write pipeline
    # when the real campaign keywords are too quiet.
    extra = os.environ.get("SANITY_KWS", "")
    if extra:
        sanity = {k.strip().lower() for k in extra.split(",") if k.strip()}
        kws = kws | sanity
        print(f"⚠ SANITY_KWS injected: {sanity}")
    print(f"Keyword set ({len(kws)}): {sorted(kws)}")
    print()

    seen = 0
    matched_samples: list[dict] = []
    parse_errors = 0
    by_keyword: dict[str, int] = {}
    started = time.time()

    try:
        async with websockets.connect(
            _JETSTREAM_URL, ping_interval=20, max_size=2**20,
        ) as ws:
            print(f"[{time.strftime('%H:%M:%S')}] connected")
            async for raw in ws:
                seen += 1
                if time.time() - started > duration_s:
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    parse_errors += 1
                    continue
                text = ((event.get("commit") or {}).get("record") or {}).get("text") or ""
                matched = _post_matches(text, kws)
                if not matched:
                    continue
                fields = _event_to_fields(event, matched)
                if not fields:
                    continue
                by_keyword[matched] = by_keyword.get(matched, 0) + 1
                if len(matched_samples) < 8:
                    matched_samples.append({
                        "matched": matched,
                        "title": fields["title"][:100],
                        "source_url": fields["source_url"],
                        "published_at": str(fields["published_at"]),
                        "text_preview": fields["raw_text"][:180].replace("\n", " "),
                    })
    except Exception as exc:
        print(f"WS error after {seen} events: {exc}")

    elapsed = time.time() - started
    print()
    print(f"=== {elapsed:.1f}s probe complete ===")
    print(f"Events seen:    {seen}  (~{seen/elapsed:.0f}/s)")
    print(f"Parse errors:   {parse_errors}")
    print(f"Matched posts:  {sum(by_keyword.values())}")
    print(f"By keyword:     {by_keyword}")
    print()
    for s in matched_samples:
        print(f"  match='{s['matched']}'  @ {s['published_at']}")
        print(f"    title: {s['title']}")
        print(f"    text:  {s['text_preview']}")
        print(f"    url:   {s['source_url']}")
        print()


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    asyncio.run(main(secs))
