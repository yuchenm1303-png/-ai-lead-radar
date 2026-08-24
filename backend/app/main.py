import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from .ai import get_ai_provider
from .connectors.base import RawLead
from .connectors.mock import MockConnector
from .notifications import notification_enabled, notify_high_score
from .pipeline import analyze_raw
from .schemas import Lead, LeadStatus, LeadStatusUpdate, ManualIngestRequest, MonitorStatus
from .storage import init_db, list_leads, update_lead_status, upsert_lead

monitor_state={'running':False,'mode':'safe-mvp','platforms':['manual','browser-helper','mock-xiaohongshu'],'last_scan_at':None}
@asynccontextmanager
async def lifespan(_:FastAPI): init_db(); yield
app=FastAPI(title='AI Lead Radar API',version='0.3.0',lifespan=lifespan)
origins=[x.strip() for x in os.getenv('CORS_ORIGINS','https://smirel.com,http://localhost:3000').split(',') if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,allow_methods=['GET','POST','PATCH','OPTIONS'],allow_headers=['*'])

def _provider(): return get_ai_provider()
def _ingest(raw_items):
    stored=[]; filtered=0
    provider=_provider()
    for raw in raw_items:
        payload=analyze_raw(raw,provider)
        if payload is None: filtered+=1; continue
        lead=upsert_lead(payload); stored.append(lead); notify_high_score(lead)
    return stored,filtered

@app.get('/health')
def health(): return {'ok':True,'service':'ai-lead-radar-api','version':'0.3.0','timestamp':datetime.now(timezone.utc).isoformat()}
@app.get('/api/v1/leads',response_model=list[Lead])
def get_leads(min_score:int=Query(0,ge=0,le=100),status:LeadStatus|None=None,limit:int=Query(100,ge=1,le=500)): return list_leads(min_score,status,limit)
@app.patch('/api/v1/leads/{lead_id}/status',response_model=Lead)
def patch_status(lead_id:int,payload:LeadStatusUpdate):
    lead=update_lead_status(lead_id,payload.status)
    if not lead: raise HTTPException(404,'Lead not found')
    return lead
@app.post('/api/v1/ingest/manual')
def manual_ingest(payload:ManualIngestRequest):
    raws=[RawLead(source=i.source,external_id=i.external_id or '',title=i.title,excerpt=i.excerpt,published_at=i.published_at,url=i.url,budget=i.budget) for i in payload.items]
    stored,filtered=_ingest(raws)
    return {'ok':True,'received':len(raws),'stored':len(stored),'filtered':filtered,'lead_ids':[x.id for x in stored]}
@app.get('/api/v1/monitor/status',response_model=MonitorStatus)
def status():
    p=_provider(); return MonitorStatus(running=bool(monitor_state['running']),mode=str(monitor_state['mode']),platforms=list(monitor_state['platforms']),last_scan_at=monitor_state['last_scan_at'],ai_provider=p.name,notification_enabled=notification_enabled(),note='Safe MVP: manual/browser-assisted ingest enabled; no anti-bot bypass.')
@app.post('/api/v1/monitor/scan')
def scan():
    monitor_state['running']=True
    try: stored,filtered=_ingest(MockConnector().fetch_latest())
    finally: monitor_state['running']=False
    t=datetime.now(timezone.utc); monitor_state['last_scan_at']=t
    return {'ok':True,'connector':'mock-xiaohongshu','scanned':len(stored)+filtered,'stored':len(stored),'filtered':filtered,'high_intent':sum(x.score>=80 for x in stored),'last_scan_at':t.isoformat()}
