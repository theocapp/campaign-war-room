# Plan: Cold Start Fix + Monitoring Baseline Marker

**Status:** Ready to build  
**Priority:** High — affects every new campaign using the tool  
**Estimated scope:** Medium (2–3 hours of implementation)

---

## Background

The analytics system (narrative frame trends, this week vs last week) is only meaningful
once continuous RSS monitoring has been running for a sustained period. New campaigns get
the tool but face a "cold start" problem: no trend data for weeks. This plan fixes it.

There are two distinct problems:

1. **Cold start** — new campaigns have no historical data on day one
2. **Data quality transparency** — charts don't distinguish reliable RSS-sourced data from
   inconsistently-sourced backfill data, which could mislead the campaign team

---

## What to Build

### Feature 1: Historical Google News Backfill on Campaign Init

**What it does:**  
When a campaign is first initialized, run a one-time historical backfill using Google News
RSS with date-range operators. Google News supports `after:YYYY-MM-DD` in the query string,
allowing us to retrieve articles published on specific dates. We break 90 days into 3
monthly windows and run each key search term against each window.

**Why Google News (not Brave Search):**  
All backfill articles come from the same source (Google News), so the data is internally
consistent for trend comparison — no mixing problem. Brave Search backfill is inconsistent
because it reflects query-time ranking, not publication volume over time.

**Key files to modify:**
- `backend/app/services/source_discovery.py` — add `_gnews_url_with_dates(query, after, before)` helper
- `backend/app/services/monitors.py` — add `run_historical_backfill(db)` function
- `backend/app/routes/campaign.py` — call backfill on `campaign_initialize` (first-time setup only)
- `backend/app/models.py` — add `historical_backfill_completed` boolean field to `CampaignConfig`

**Implementation details:**

```python
# In source_discovery.py — add this helper
def _gnews_url_with_dates(query: str, after: str, before: str) -> str:
    """Google News RSS with date range operators.
    after/before format: 'YYYY-MM-DD'
    """
    dated_query = f"{query} after:{after} before:{before}"
    params = urlencode({"q": dated_query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return f"https://news.google.com/rss/search?{params}"
```

```python
# In monitors.py — add this function
def run_historical_backfill(db: Session) -> dict:
    """One-time 90-day Google News backfill on campaign initialization.
    
    Breaks 90 days into 3 monthly windows and fetches each key query per window.
    Marks CampaignConfig.historical_backfill_completed = True when done.
    Safe to call multiple times — skips if already completed.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign or campaign.historical_backfill_completed:
        return {"skipped": True}
    
    opponents = db.query(Opponent).all()
    candidate = campaign.candidate_name
    
    # Build the core queries to backfill (same terms as Google News RSS monitors)
    queries = []
    if candidate:
        cand_last = _candidate_last_name(candidate)  # import from source_discovery
        if cand_last:
            queries.append(cand_last)
    for opp in opponents:
        opp_last = _candidate_last_name(opp.name)
        if opp_last:
            queries.append(opp_last)
    if campaign.district:
        queries.append(campaign.district)  # e.g. "PA-08"
    
    # 3 monthly windows going back 90 days
    now = datetime.utcnow()
    windows = []
    for i in range(3):
        before = now - timedelta(days=i * 30)
        after = now - timedelta(days=(i + 1) * 30)
        windows.append((after.strftime("%Y-%m-%d"), before.strftime("%Y-%m-%d")))
    
    total_added = 0
    for query in queries:
        for after_date, before_date in windows:
            url = _gnews_url_with_dates(query, after_date, before_date)
            try:
                result = ingest_rss(db, url, label=f"Backfill: {query} ({after_date})")
                total_added += result.added
            except Exception:
                pass
    
    campaign.historical_backfill_completed = True
    db.commit()
    return {"added": total_added, "queries": len(queries), "windows": len(windows)}
```

```python
# In campaign.py — call backfill in campaign_initialize (first-time setup)
@router.post("/campaign/initialize")
def campaign_initialize(db: Session = Depends(get_db)):
    # ... existing init logic ...
    try:
        run_historical_backfill(db)
    except Exception as exc:
        logger.warning("historical_backfill failed: %s", exc)
    # ...
```

**Database migration needed:**
Add `historical_backfill_completed` boolean column (default False) to `campaign_config` table:

```python
# In models.py, add to CampaignConfig:
historical_backfill_completed = Column(Boolean, default=False)
```

Run migration:
```sql
ALTER TABLE campaign_config ADD COLUMN historical_backfill_completed BOOLEAN DEFAULT 0;
```

**For the existing Cognetti campaign:**  
The backfill has already been run manually (current data goes back to Dec 2023 via search
monitors). Set `historical_backfill_completed = True` so the new backfill doesn't re-run:

```sql
UPDATE campaign_config SET historical_backfill_completed = 1 WHERE id = 1;
```

---

### Feature 2: Monitoring Baseline Marker on Narrative Charts

**What it does:**  
Adds a vertical reference line on the frame detail timeseries chart (and optionally the
narrative pulse on the morning briefing) showing when continuous RSS monitoring began.
Includes a tooltip explaining that data before this date is a partial backfill.

**Why this matters:**  
Prevents the campaign team from drawing incorrect trend conclusions from pre-RSS data.
Makes the tool trustworthy — honest about its own limitations.

**Key files to modify:**
- `backend/app/routes/analytics.py` — add `/api/monitoring/start-date` endpoint
- `frontend/src/pages/FrameDetail.tsx` — add `ReferenceLine` to the bar chart
- `frontend/src/api/client.ts` — add `getMonitoringStartDate()` method

**Backend endpoint:**

```python
# In analytics.py — add this endpoint
@router.get("/monitoring/start-date")
def get_monitoring_start_date(db: Session = Depends(get_db)):
    """Return the date continuous RSS monitoring began (earliest RSS feed last_fetched_at)."""
    from app.models import RssFeed
    earliest = (
        db.query(func.min(RssFeed.last_fetched_at))
        .scalar()
    )
    return {
        "monitoring_start": earliest.date().isoformat() if earliest else None,
        "has_backfill": bool(
            db.query(CampaignConfig).first() and
            db.query(CampaignConfig).first().historical_backfill_completed
        )
    }
```

**Frontend — FrameDetail.tsx changes:**

1. Add a state variable `monitoringStart: string | null` and fetch it with `api.getMonitoringStartDate()` in the useEffect.

2. Add a `ReferenceLine` from recharts to the `BarChart` where the bar chart currently is:

```tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

// Inside the BarChart, after the Bar:
{monitoringStart && (
  <ReferenceLine
    x={monitoringStart}
    stroke="#f59e0b"
    strokeDasharray="4 2"
    label={{
      value: 'Monitoring started',
      position: 'insideTopRight',
      fontSize: 10,
      fill: '#f59e0b',
    }}
  />
)}
```

3. Add a small explanatory note below the chart:
```tsx
{monitoringStart && (
  <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 6 }}>
    Data before {formatDate(monitoringStart)} is a partial backfill and may undercount coverage.
  </div>
)}
```

**Frontend — client.ts:**

```ts
async getMonitoringStartDate(): Promise<{ monitoring_start: string | null; has_backfill: boolean }> {
  return this.get('/monitoring/start-date')
}
```

---

## Implementation Order

1. Add `historical_backfill_completed` column to `CampaignConfig` model + migration SQL
2. Add `_gnews_url_with_dates()` to `source_discovery.py`
3. Add `run_historical_backfill()` to `monitors.py`
4. Wire backfill into `campaign_initialize` in `campaign.py`
5. Mark existing Cognetti campaign as backfill-completed (SQL)
6. Add `/monitoring/start-date` endpoint to `analytics.py`
7. Add `getMonitoringStartDate()` to `frontend/src/api/client.ts`
8. Update `FrameDetail.tsx` with ReferenceLine and explanatory note

---

## Testing Checklist

- [ ] New campaign setup triggers backfill automatically
- [ ] Second call to `campaign_initialize` does NOT re-run backfill (idempotent)
- [ ] Backfill adds articles dated before RSS monitoring start
- [ ] `/api/monitoring/start-date` returns the correct earliest RSS date
- [ ] ReferenceLine appears on the FrameDetail chart at the correct date
- [ ] ReferenceLine does NOT appear if no RSS monitoring has run yet (null case)
- [ ] Explanatory note renders below chart

---

## What This Does NOT Fix

- The trend charts will still show a step-change at the monitoring start date because
  Google News RSS pulls more articles per day than sporadic backfill windows. This is
  expected and honest — the marker makes it visible rather than hiding it.
- Twitter/X data is not available without a paid API ($100+/month). Not planned.
