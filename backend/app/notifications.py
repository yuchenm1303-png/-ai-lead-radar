import json, os, urllib.request
from .schemas import Lead

def notification_enabled(): return bool(os.getenv('FEISHU_WEBHOOK_URL'))
def notify_high_score(lead: Lead) -> bool:
    url=os.getenv('FEISHU_WEBHOOK_URL'); threshold=int(os.getenv('NOTIFY_MIN_SCORE','85'))
    if not url or lead.score < threshold or lead.status != 'new': return False
    text=f'【AI Lead Radar】\n来源：{lead.source}\n类型：{lead.category}\nAI Score：{lead.score}\n需求：{lead.title}\n链接：{lead.url or "未提供"}'
    data=json.dumps({'msg_type':'text','content':{'text':text}},ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'}),timeout=10) as r: return 200 <= r.status < 300
    except Exception: return False
