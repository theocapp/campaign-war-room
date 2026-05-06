from datetime import datetime, timedelta
from app.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import CampaignConfig, Opponent, SourceItem, OpponentActivity

engine = create_engine('sqlite:///:memory:', connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

session.add(CampaignConfig(candidate_name='Alex Rivera', office='Assembly', district='Queens'))
opp = Opponent(name='Jordan Lee')
session.add(opp)
session.commit()

now = datetime.utcnow()

s_prior = SourceItem(title='Prior mention', raw_text='prior', source_name='Local News', source_type='news', published_at=now - timedelta(days=10), created_at=now - timedelta(days=10), race_relevance_score=80, race_relevance_label='critical', actionability_score=50, actionability_label='review', content_category='campaign', archived_as_irrelevant=False, story_cluster_id='cluster-a', geo_relevance='district', opponent_mentioned=True)
session.add(s_prior)
session.commit()
session.add(OpponentActivity(opponent_id=opp.id, source_item_id=s_prior.id, attack='prior'))
session.commit()

s_recent1 = SourceItem(title='Recent mention 1', raw_text='recent 1', source_name='Social Post', source_type='social', published_at=now - timedelta(days=2), created_at=now - timedelta(days=2), race_relevance_score=85, race_relevance_label='critical', actionability_score=70, actionability_label='respond', content_category='campaign', archived_as_irrelevant=False, story_cluster_id='cluster-b', geo_relevance='district', opponent_mentioned=True)

s_recent2 = SourceItem(title='Recent mention 2', raw_text='recent 2', source_name='Neighborhood Post', source_type='news', published_at=now - timedelta(days=1), created_at=now - timedelta(days=1), race_relevance_score=85, race_relevance_label='critical', actionability_score=60, actionability_label='review', content_category='campaign', archived_as_irrelevant=False, story_cluster_id='cluster-c', geo_relevance='city', opponent_mentioned=True)

session.add_all([s_recent1, s_recent2])
session.commit()
session.add(OpponentActivity(opponent_id=opp.id, source_item_id=s_recent1.id, attack='recent1'))
session.add(OpponentActivity(opponent_id=opp.id, source_item_id=s_recent2.id, attack='recent2'))
session.commit()

from app.services.narratives import refresh_narratives, top_narratives
from app.services.narratives import _candidate_from_opponent_activity, _candidate_from_source

from app.models import OpponentActivity, SourceItem
print('OpponentActivity count:', session.query(OpponentActivity).count())
print('SourceItem count:', session.query(SourceItem).count())

acts = session.query(OpponentActivity).all()
for a in acts:
    cand = _candidate_from_opponent_activity(a, session.query(CampaignConfig).first())
    print('activity id', a.id, 'attack', a.attack, 'candidate', bool(cand), 'type', getattr(cand, 'narrative_type', None))

narratives = refresh_narratives(session)
print('narratives after refresh:', len(narratives))
for n in narratives:
    print('narrative:', n.id, n.short_label, n.source_cluster_count, n.source_count, n.messenger_diversity_count)

session.close()
