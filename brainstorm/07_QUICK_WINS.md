# Quick Wins — <30 minutes each

Pre-packaged small changes pulled from the other docs. Each: what to do, file(s) to edit, expected impact. Pick a Saturday morning and knock out as many as you want.

---

## ⏱ 5-minute wins

### QW-1. Delete the 2 orphan `frame_stage_history` rows
```sql
DELETE FROM frame_stage_history 
WHERE frame_id NOT IN (SELECT id FROM narrative_frames);
```
That's the entire change. Run in `sqlite3 war_room.db` directly or via `/api/admin` if you build a one-off route.

### QW-2. Rename "Frame" → "Narrative" in nav + page titles
**Files:** `Layout.tsx` line 124 (already "Narratives" — good), but check page H1s in `Narratives.tsx`, `NarrativeDetail.tsx`. Search for the word "Frame" in JSX text content.

### QW-3. Better empty-state copy on Review Queue
**File:** `pages/ReviewQueue.tsx`. Find the empty state, replace with:
> 🎉 Inbox zero! You've reviewed all articles from today's ingest.

### QW-4. Rename `1W` → `7-DAY` in Dashboard card headers
**File:** `pages/Dashboard.tsx` line 152.
Change `['METRIC', 'TOTAL', '1W', '']` → `['METRIC', 'TOTAL', '7-DAY', '']`.

### QW-5. Add tooltip text for stage chips
**File:** `pages/Dashboard.tsx` or wherever `stageLabel` is rendered.
Wrap each chip in `<span title="Multiple outlets, gaining velocity">spreading</span>` etc.

---

## ⏱ 15-30 minute wins

### QW-6. Add a "Head-to-Head" filter chip to Dashboard
**File:** `pages/Dashboard.tsx` filter section.
Add a chip that filters to `candidate_mentioned=1 AND opponent_mentioned=1`.
Backend already returns this data; just add the client-side filter logic.

Expected impact: 105 critical articles become a 1-click view.

### QW-7. Local-only filter chip
**File:** `pages/Dashboard.tsx` + `pages/ReviewQueue.tsx`.
Add a chip "PA-08 only" that filters `district_mentioned=1 OR geo_relevance='district'`.

### QW-8. Hide noise categories from review queue by default
**File:** `pages/ReviewQueue.tsx`.
On mount, default `content_category` filter to exclude: `sports, entertainment, food, weather, generic_crime, irrelevant`.
Give user a "Show all" toggle.

Expected: review queue size drops from ~13K → ~2K of actually-relevant articles.

### QW-9. Add `momentum_signal=none` for frames without trend match (fixes the "all viral" bug)
**File:** `services/frame_momentum.py:182`
Change:
```python
terms_for_trend = matched_terms or all_terms
```
To:
```python
if not matched_terms:
    # No matched trend terms — skip; mark frame as unknown signal
    f.momentum_signal = "no_trend_signal"
    f.momentum_data = json.dumps({"reason": "no matching trend terms"})
    continue
terms_for_trend = matched_terms
```

Then re-run the momentum job manually:
```bash
python -c "from app.db import SessionLocal; from app.services.frame_momentum import analyze_all_frames; analyze_all_frames(SessionLocal())"
```

Expected: 13 viral frames become 2-3 viral + 10 "no_trend_signal", which is the truth.

### QW-10. Make stage chips clickable filters
**File:** `pages/Dashboard.tsx` + `pages/Narratives.tsx`.
Wrap each stage chip in `onClick={() => setFilterStage(frame.stage)}`. So clicking "mainstream" on one card filters all narratives to mainstream.

### QW-11. Add `tooltip="Updated 12s ago"` to dashboard auto-refresh
**File:** `pages/Dashboard.tsx`.
Add a small "Updated X ago" string in the top-right, updated by the existing 60s refresh interval.

### QW-12. Show extraction_quality_label badge on review queue cards
**File:** `pages/ReviewQueue.tsx`.
For articles with `extraction_quality_label = 'poor'`, show a small "incomplete extraction" warning badge. Filters trust.

### QW-13. Promote the "AI noticed N emerging narratives" banner with `count > 0`
**File:** `pages/Narratives.tsx`.
The Session A work surfaces the banner. Verify it's actually showing 6 cards from the live API. If not — log + debug.

### QW-14. Delete the 13 unused tables (KG + legacy)
This is Session C territory, but the migration itself is one file with 16 DROP TABLE statements + verifications. Once you've reviewed the design lessons (see ROADMAP note from Session C planning), this is a fast cleanup.

```sql
-- After verifying no FK references
DROP TABLE kg_alerts;
DROP TABLE kg_claim_entities;
DROP TABLE kg_claim_issues;
DROP TABLE kg_claims;
DROP TABLE kg_edges;
DROP TABLE kg_entities;
DROP TABLE kg_entity_aliases;
DROP TABLE kg_events;
DROP TABLE kg_issues;
DROP TABLE kg_narrative_claims;
DROP TABLE kg_narratives;
DROP TABLE kg_sources;
DROP TABLE narratives;
DROP TABLE narrative_mentions;
DROP TABLE generated_talking_points;  -- last 1 row from May 8
-- candidate_narratives, candidate_message_libraries, canvassing_notes — all 0 rows
DROP TABLE candidate_narratives;
DROP TABLE candidate_message_libraries;
DROP TABLE canvassing_notes;
DROP TABLE manual_captures;
DROP TABLE manual_source_reminders;
```

Frees ~2,000 rows of stale data + simplifies the schema view in your tools.

**Caveat:** Don't drop `generated_talking_points` if you plan to revive talking-point generation as a feature (#4 in features doc). Re-use the schema instead of dropping it.

### QW-15. Add `?owner=opponent&stage=emerging` URL persistence to filter state
**File:** `pages/Narratives.tsx`.
Use `useSearchParams` from react-router-dom. Sync filter state to URL. ~15 lines.

Benefits: bookmarkable views, browser back/forward, sharable links.

### QW-16. Add YouTube channel RSS feeds (PA broadcast)
**No code change — UI only:**
Go to Monitors page, add these as RSS feeds:
- `https://www.youtube.com/feeds/videos.xml?channel_id=UCsT0YIqwnpJCM-mx7-gSA4Q` (Cognetti official)
- `https://www.youtube.com/feeds/videos.xml?channel_id=UC...` (Bresnahan — need to find ID)
- Channel IDs for WNEP, WBRE, WVIA, ABC27 (verify each)

Expected: cleaner YouTube ingestion than current keyword search.

### QW-17. Add the Bresnahan press feed
Check if `https://bresnahan.house.gov/feed` works. If yes, add it as an RSS source. If no, add `bresnahan.house.gov/press-releases` as a webpage monitor.

Direct from-the-source statements; beats Google News round-trip.

### QW-18. Disable the "Suggest frames" button after click + show last-run time
**File:** `pages/Narratives.tsx`.
The button currently is permanently clickable. Track the last-suggested timestamp in localStorage. Disable for 1hr after click. Show "Last suggested 12 min ago" as helper text.

Prevents accidental LLM-burning double-clicks.

### QW-19. Add "view as compact" toggle on Narratives + Dashboard
**File:** `pages/Dashboard.tsx`.
Add a toggle button. When compact: replace the 400px-tall cards with 60px rows showing just name + stage + 7-day count.

Lets you scan 20 narratives at once instead of 3.

### QW-20. Add `cluster_opponent_activities` as columns on Opponents page
**File:** `pages/Opponents.tsx`.
You have 102 attacks + 154 claims + 30 promises in `cluster_opponent_activities`. Add three sub-tabs on each opponent's detail view: Attacks / Claims / Promises. Currently this data may not be visible.

---

## ⏱ <30 min on the DB / backend side

### QW-21. Add a debug-only stats endpoint
**File:** `routes/admin.py` (new endpoint).
```python
@router.get("/admin/db-stats")
def db_stats(db: Session = Depends(get_db)):
    return {
        "source_items_total": db.query(SourceItem).count(),
        "source_items_unlinked": db.query(SourceItem).filter(SourceItem.outlet_id.is_(None)).count(),
        "narrative_frames": db.query(NarrativeFrame).filter(NarrativeFrame.active==True).count(),
        "candidate_frames_pending": ...,
        "story_clusters": ...,
        # etc.
    }
```

Replaces "let me SSH and run SQL queries" with a single API call. Useful for production health checks.

### QW-22. Backfill `source_items.urgency` for old rows
Old articles may not have urgency set. Run a one-off:
```sql
UPDATE source_items
SET urgency = CASE
  WHEN race_relevance_label = 'critical' THEN 'high'
  WHEN race_relevance_label IN ('high','medium') THEN 'medium'
  ELSE 'low'
END
WHERE urgency IS NULL;
```

After this, the "high urgency" filter in QW-1ish works for everything.

### QW-23. Add a confirm guard to admin endpoints that touch the DB
**File:** `routes/admin.py`.
The ROADMAP already mentions: `reset_workspace`, `reanalyze-sources`, `rescore-articles`, `discover-outlets`, `auto-review`, `rescore-stop` are LLM-cost-burning and DB-modifying. Some have confirm-string guards; some don't.

Add a `require_confirm_string("CONFIRM_RESCORE")` decorator to the ones that don't. 10 lines.

This is mandatory before deploying to anywhere besides localhost.

### QW-24. Add a "monitor_recent_sentiment" derived column on outlets
Cache the last 30-day avg sentiment of articles per outlet:
```sql
ALTER TABLE outlets ADD COLUMN recent_sentiment_cognetti FLOAT;
ALTER TABLE outlets ADD COLUMN recent_sentiment_bresnahan FLOAT;
```

Refresh nightly. Powers the "Outlet Bias Index" feature (#15).

Use case: you can immediately see "which outlets are reliably hostile/friendly" without computing live.

---

## Suggested batch sequence (1 weekend, ~4 hours)

If you want to ship a visibly-improved version on Saturday:

**Morning (2 hrs):**
1. QW-1, QW-2, QW-3, QW-4, QW-5 — copy/typography (1 hr)
2. QW-6, QW-7, QW-8 — three filter chips (1 hr)

**Afternoon (2 hrs):**
3. QW-9 — fix frame_momentum (~30 min)
4. QW-15 — URL-persistent filters (30 min)
5. QW-18 — disable suggest button (10 min)
6. QW-22 — backfill urgency (10 min)
7. QW-16/QW-17 — add YouTube + Bresnahan feeds (40 min)

**Result:** Visible UI improvements + a real bug fix + better data flowing in. Demonstrable in a 30-second screen share.
