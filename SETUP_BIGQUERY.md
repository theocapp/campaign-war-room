# BigQuery historical backfill — one-time setup

The GDELT DOC API caps every request at 250 articles, which limits the
historical backfill to ~9k URLs even for a year-long pull. For genuinely
complete historical coverage, the backend supports an alternate path that
queries GDELT's public BigQuery dataset directly. No 250 cap, much higher
recall, free under typical campaign usage.

This is **optional**. Without it the GDELT DOC API path still works.

## What you're doing

Creating a Google Cloud project + a service account so the backend can
query the public `gdelt-bq.gdeltv2` dataset on your behalf. You pay nothing
for storage (the data lives in Google's account, not yours). Query cost is
free under BigQuery's 1 TB/month free tier; our queries always filter by
date partition and scan ~100 GB per backfill run, so you get ~10 runs/month
in the free tier.

## Steps

1. **Create a Google Cloud project** (free)
   - Go to <https://console.cloud.google.com/>
   - Click the project dropdown at the top, then **NEW PROJECT**
   - Name it something like `campaign-war-room`
   - You don't need to enable billing for read-only public-dataset queries
     within the free tier, but BigQuery may ask you to enable a billing
     account just to confirm. The 1 TB/month free tier means you pay $0
     unless you exceed it.

2. **Enable the BigQuery API** in that project
   - In the Cloud Console search bar, type "BigQuery API"
   - Click **ENABLE**

3. **Create a service account**
   - Cloud Console → **IAM & Admin** → **Service Accounts** → **CREATE SERVICE ACCOUNT**
   - Name: `war-room-bigquery` (or whatever)
   - Grant these roles:
     - **BigQuery Data Viewer** (lets it read data)
     - **BigQuery Job User** (lets it run queries)
   - Click **DONE**

4. **Create a JSON key for the service account**
   - Click the service account you just made
   - **KEYS** tab → **ADD KEY** → **Create new key** → **JSON** → **CREATE**
   - It downloads a `.json` file. Move it somewhere safe, e.g.
     `~/.config/gcp/war-room-bigquery.json`

5. **Point the backend at the key**
   - Edit `backend/.env` and add:
     ```
     GOOGLE_APPLICATION_CREDENTIALS=/Users/yourname/.config/gcp/war-room-bigquery.json
     ```
     (Use the absolute path.)

6. **Install the BigQuery client library** in the backend venv
   ```bash
   cd backend
   source .venv/bin/activate
   pip install google-cloud-bigquery
   ```

7. **Restart the backend** so it picks up the new env var
   ```bash
   kill $(lsof -ti:8000) 2>/dev/null; sleep 2; uvicorn app.main:app
   ```

## Running a BigQuery backfill

Once the setup is done, trigger a backfill — BigQuery is the default source:

```bash
curl -X POST "http://localhost:8000/api/campaign/backfill-historical?days_back=365&force=true"
```

To explicitly force the GDELT DOC API path instead (e.g., for testing or if
BigQuery credentials aren't configured), add `source=api`:

```bash
curl -X POST "http://localhost:8000/api/campaign/backfill-historical?days_back=365&force=true&source=api"
```

The realtime poll (every 15 minutes) always uses the GDELT DOC API regardless
of this setting — only the historical backfill defaults to BigQuery.

Poll progress with the same status endpoint as the API-based backfill:

```bash
curl -s http://localhost:8000/api/campaign/pipeline-status | python3 -m json.tool
```

## Cost expectations

- **Storage**: $0/month — the dataset lives in Google's account, not yours
- **Queries**: free under 1 TB/month scanned. A year-range backfill scans
  ~100 GB per run. ~10 runs/month before you'd start paying.
- **Beyond free tier**: $5/TB. A backfill costs ~$0.50 once you blow
  through the monthly allowance.

The one thing that can blow the budget: a query without a `_PARTITIONTIME`
filter — that scans the entire petabyte-scale dataset. Every query in
`app/services/gdelt_bigquery.py` filters on `_PARTITIONTIME` by design.

## Troubleshooting

- `GOOGLE_APPLICATION_CREDENTIALS env var not set` — check `.env` is loaded
  by the backend and the path is absolute.
- `google.api_core.exceptions.PermissionDenied` — service account is
  missing one of the two BigQuery roles.
- `google.api_core.exceptions.NotFound: Dataset gdelt-bq:gdeltv2` — your
  project's billing is configured wrong; even public datasets require a
  project that can be billed (even if no charge accrues).
