import json, os, urllib.request
from dataclasses import dataclass

@dataclass(frozen=True)
class AIClassification:
    is_lead: bool
    need_type: str
    intent_score: int
    fit_score: int
    urgency: str
    budget_text: str | None
    reason: str
    confidence: int
    signals: list[str]

class AIProvider:
    name = 'rules'
    def classify(self, title: str, excerpt: str) -> AIClassification | None:
        return None

class OpenAIProvider(AIProvider):
    name = 'openai'
    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model
    def classify(self, title: str, excerpt: str) -> AIClassification | None:
        schema = {'type':'object','properties':{
            'is_lead':{'type':'boolean'},'need_type':{'type':'string'},'intent_score':{'type':'integer','minimum':0,'maximum':100},
            'fit_score':{'type':'integer','minimum':0,'maximum':100},'urgency':{'type':'string','enum':['low','medium','high']},
            'budget_text':{'type':['string','null']},'reason':{'type':'string'},'confidence':{'type':'integer','minimum':0,'maximum':100},
            'signals':{'type':'array','items':{'type':'string'},'maxItems':8}},
            'required':['is_lead','need_type','intent_score','fit_score','urgency','budget_text','reason','confidence','signals'],'additionalProperties':False}
        payload = {
            'model': self.model,
            'store': False,
            'instructions': '判断内容是否为真实的软件开发外包/购买需求。学习、教程、泛讨论必须判为非 lead。只输出结构化结果。不要推断或输出个人敏感信息。',
            'input': f'标题：{title}\n内容：{excerpt}',
            'text': {'format': {'type': 'json_schema', 'name': 'lead_classification', 'strict': True, 'schema': schema}},
            'max_output_tokens': 500,
        }
        req = urllib.request.Request('https://api.openai.com/v1/responses', data=json.dumps(payload,ensure_ascii=False).encode(), headers={'Authorization':f'Bearer {self.api_key}','Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r: data=json.load(r)
            text = ''.join(c.get('text','') for o in data.get('output',[]) if o.get('type')=='message' for c in o.get('content',[]) if c.get('type')=='output_text')
            return AIClassification(**json.loads(text)) if text else None
        except Exception:
            return None

def get_ai_provider() -> AIProvider:
    if os.getenv('AI_PROVIDER','rules').lower() == 'openai' and os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL'):
        return OpenAIProvider(os.environ['OPENAI_API_KEY'], os.environ['OPENAI_MODEL'])
    return AIProvider()
