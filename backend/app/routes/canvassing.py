from collections import Counter
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CanvassingNote
from app.schemas import CanvassingInsightsOut, PrecinctInsight
from app.services.ingestion import ingest_canvassing_csv

router = APIRouter()


@router.post("/canvassing/upload")
async def upload_canvassing(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only CSV files are accepted")
    content = await file.read()
    try:
        csv_text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
    count = ingest_canvassing_csv(db, csv_text)
    return {"imported": count, "message": f"Imported {count} canvassing notes"}


@router.get("/canvassing/insights", response_model=CanvassingInsightsOut)
def get_canvassing_insights(db: Session = Depends(get_db)):
    notes = db.query(CanvassingNote).all()
    total = len(notes)

    if total == 0:
        return CanvassingInsightsOut(
            total_contacts=0,
            precincts=[],
            overall_top_issues=[],
            sentiment_breakdown={},
        )

    precincts_map: dict[str, list[CanvassingNote]] = {}
    for n in notes:
        precincts_map.setdefault(n.precinct, []).append(n)

    precinct_insights = []
    for precinct, pnotes in sorted(precincts_map.items()):
        issue_counter = Counter(n.issue for n in pnotes if n.issue)
        sentiment_counter = Counter(n.sentiment for n in pnotes if n.sentiment)
        top_issues = [i for i, _ in issue_counter.most_common(3)]
        dominant = sentiment_counter.most_common(1)[0][0] if sentiment_counter else "neutral"
        neg_pct = round(sentiment_counter.get("negative", 0) / len(pnotes) * 100)

        summary_parts = [f"Precinct {precinct}: {len(pnotes)} contacts."]
        if top_issues:
            summary_parts.append(f"Top concerns: {', '.join(top_issues)}.")
        summary_parts.append(f"{neg_pct}% expressed negative sentiment." if neg_pct > 0 else "Sentiment mostly neutral or positive.")

        precinct_insights.append(PrecinctInsight(
            precinct=precinct,
            contact_count=len(pnotes),
            top_issues=top_issues,
            dominant_sentiment=dominant,
            summary=" ".join(summary_parts),
        ))

    overall_issue_counter = Counter(n.issue for n in notes if n.issue)
    overall_top = [i for i, _ in overall_issue_counter.most_common(5)]
    sentiment_breakdown = dict(Counter(n.sentiment for n in notes if n.sentiment))

    return CanvassingInsightsOut(
        total_contacts=total,
        precincts=precinct_insights,
        overall_top_issues=overall_top,
        sentiment_breakdown=sentiment_breakdown,
    )
