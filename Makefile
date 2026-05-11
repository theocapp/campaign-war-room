.PHONY: verify-kg test-kg ingest-pa08 ingest-pack reembed-recluster help

# ── Knowledge Graph verification ──────────────────────────────────────────────

verify-kg:
	@cd backend && python scripts/verify_kg_pipeline.py

# Ingest PA-08 source pack: register feeds, fetch articles, cluster, alert (idempotent)
ingest-pa08:
	@cd backend && ENABLE_KG_PIPELINE=1 LLM_PROVIDER=mock python scripts/ingest_source_pack.py --pack pa_08 --limit 200 --relevance-min 0.3 --days 60

# Generic source pack ingestion — override with: make ingest-pack PACK=pa_08
ingest-pack:
	@cd backend && ENABLE_KG_PIPELINE=1 LLM_PROVIDER=mock python scripts/ingest_source_pack.py --pack $(PACK)

# Run the KG-specific unit tests (requires pytest in the backend venv)
test-kg:
	@cd backend && python -m pytest tests/test_kg_narrative_engine.py tests/test_kg_credibility.py -v

# Clear stale embeddings and re-run clustering on the real DB (requires --yes)
reembed-recluster:
	@cd backend && python scripts/reembed_and_recluster.py --yes

help:
	@echo "Available targets:"
	@echo "  ingest-pa08        Ingest PA-08 source pack feeds + run KG pipeline (idempotent)"
	@echo "  ingest-pack        Generic pack runner: make ingest-pack PACK=<name>"
	@echo "  verify-kg          End-to-end KG pipeline verification (no API keys needed)"
	@echo "  test-kg            KG unit tests via pytest"
	@echo "  reembed-recluster  Clear stale embeddings and re-cluster (writes to real DB)"
