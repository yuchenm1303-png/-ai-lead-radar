import hashlib, json, os, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from .schemas import Lead, LeadCreate, LeadStatus

DB_PATH = Path(os.getenv('DATABASE_PATH','./lead_radar.db'))
NEW_COLUMNS = {
 'discovered_at':'TEXT','is_lead':'INTEGER NOT NULL DEFAULT 1','intent_score':'INTEGER NOT NULL DEFAULT 0','fit_score':'INTEGER NOT NULL DEFAULT 0',
 'freshness_score':'INTEGER NOT NULL DEFAULT 0','urgency':"TEXT NOT NULL DEFAULT 'low'",'confidence':'INTEGER NOT NULL DEFAULT 0',
 'priority':"TEXT NOT NULL DEFAULT 'low'",'reason':"TEXT NOT NULL DEFAULT ''",'dedupe_key':'TEXT'
}
def utc_now(): return datetime.now(timezone.utc).isoformat()
@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True,exist_ok=True); conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row
    try: yield conn; conn.commit()
    finally: conn.close()

def init_db():
    with connection() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT NOT NULL,external_id TEXT,title TEXT NOT NULL,excerpt TEXT NOT NULL,category TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,published_at TEXT NOT NULL,budget TEXT,status TEXT NOT NULL DEFAULT 'new',url TEXT,signals_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source,external_id))''')
        existing={r['name'] for r in c.execute('PRAGMA table_info(leads)').fetchall()}
        for name, ddl in NEW_COLUMNS.items():
            if name not in existing: c.execute(f'ALTER TABLE leads ADD COLUMN {name} {ddl}')
        c.execute('CREATE INDEX IF NOT EXISTS idx_leads_score ON leads(score DESC)'); c.execute('CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)'); c.execute('CREATE INDEX IF NOT EXISTS idx_leads_published ON leads(published_at DESC)'); c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_dedupe ON leads(dedupe_key)')

def make_dedupe_key(p: LeadCreate) -> str:
    if p.dedupe_key: return p.dedupe_key
    raw = f"{p.source}|{p.external_id or ''}|{p.url or ''}|{p.title.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()

def _row(r):
    return Lead(id=r['id'],source=r['source'],external_id=r['external_id'],title=r['title'],excerpt=r['excerpt'],category=r['category'],score=r['score'],is_lead=bool(r['is_lead']),intent_score=r['intent_score'],fit_score=r['fit_score'],freshness_score=r['freshness_score'],urgency=r['urgency'],confidence=r['confidence'],priority=r['priority'],published_at=datetime.fromisoformat(r['published_at']),discovered_at=datetime.fromisoformat(r['discovered_at']) if r['discovered_at'] else None,budget=r['budget'],reason=r['reason'],status=r['status'],url=r['url'],signals=json.loads(r['signals_json'] or '[]'),dedupe_key=r['dedupe_key'],created_at=datetime.fromisoformat(r['created_at']),updated_at=datetime.fromisoformat(r['updated_at']))

def list_leads(min_score=0,status:LeadStatus|None=None,limit=100,include_non_leads=False):
    sql='SELECT * FROM leads WHERE score >= ?'; params=[min_score]
    if not include_non_leads: sql+=' AND is_lead = 1'
    if status is not None: sql+=' AND status = ?'; params.append(status)
    sql+=' ORDER BY score DESC, published_at DESC LIMIT ?'; params.append(limit)
    with connection() as c: rows=c.execute(sql,params).fetchall()
    return [_row(r) for r in rows]

def upsert_lead(p:LeadCreate):
    now=utc_now(); dedupe=make_dedupe_key(p); external=p.external_id or f'manual:{dedupe[:20]}'
    vals=(p.source,external,p.title,p.excerpt,p.category,p.score,p.published_at.isoformat(),p.discovered_at.isoformat() if p.discovered_at else now,p.budget,p.status,p.url,json.dumps(p.signals,ensure_ascii=False),int(p.is_lead),p.intent_score,p.fit_score,p.freshness_score,p.urgency,p.confidence,p.priority,p.reason,dedupe,now,now)
    with connection() as c:
        c.execute('''INSERT INTO leads(source,external_id,title,excerpt,category,score,published_at,discovered_at,budget,status,url,signals_json,is_lead,intent_score,fit_score,freshness_score,urgency,confidence,priority,reason,dedupe_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dedupe_key) DO UPDATE SET title=excluded.title,excerpt=excluded.excerpt,category=excluded.category,score=excluded.score,published_at=excluded.published_at,budget=excluded.budget,url=excluded.url,signals_json=excluded.signals_json,is_lead=excluded.is_lead,intent_score=excluded.intent_score,fit_score=excluded.fit_score,freshness_score=excluded.freshness_score,urgency=excluded.urgency,confidence=excluded.confidence,priority=excluded.priority,reason=excluded.reason,updated_at=excluded.updated_at''',vals)
        r=c.execute('SELECT * FROM leads WHERE dedupe_key=?',(dedupe,)).fetchone()
    return _row(r)

def update_lead_status(lead_id:int,status:LeadStatus):
    with connection() as c:
        c.execute('UPDATE leads SET status=?,updated_at=? WHERE id=?',(status,utc_now(),lead_id)); r=c.execute('SELECT * FROM leads WHERE id=?',(lead_id,)).fetchone()
    return _row(r) if r else None
