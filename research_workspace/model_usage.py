"""Workspace-local receipts for actual model HTTP attempts; no prompts or secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from decimal import Decimal
import json
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from .library import _now

_active = ContextVar('model_call_recorder',default=None)
TOKEN_FIELDS=('prompt_tokens','completion_tokens','total_tokens','prompt_cache_hit_tokens','prompt_cache_miss_tokens')
PRICE_SOURCE='https://api-docs.deepseek.com/quick_start/pricing/'
PRICE_CHECKED='2026-10-17'
PRICES={'deepseek-v4-pro':('0.022','0.66','1.98'),'deepseek-flash':('0.003','0.15','0.6')}


def estimate_cost(model,host,usage):
    """Frozen public-price range, not an invoice or account-balance reading."""
    if host!='api.deepseek.com' or model not in PRICES:return None
    if 'prompt_tokens' not in usage or 'completion_tokens' not in usage:return None
    hit,miss,out=map(Decimal,PRICES[model]);p=usage['prompt_tokens'];c=usage['completion_tokens']
    h=usage.get('prompt_cache_hit_tokens');m=usage.get('prompt_cache_miss_tokens')
    if h is not None and m is not None and h+m==p:
        lower=Decimal(h)*hit+Decimal(m)*miss+Decimal(c)*out;upper=lower*2
        basis='reported_cache_split; off_peak_to_peak'
    else:
        lower=Decimal(p)*hit+Decimal(c)*out;upper=(Decimal(p)*miss+Decimal(c)*out)*2
        basis='cache_split_unknown; all_hit_off_peak_to_all_miss_peak'
    return {'currency':'USD','min':str(lower/10**6),'max':str(upper/10**6),
        'price_source':PRICE_SOURCE,'price_checked_at':PRICE_CHECKED,'basis':basis,'actual_charge':None}


def sanitized_usage(envelope):
    raw=envelope.get('usage') if isinstance(envelope,dict) else None
    if not isinstance(raw,dict):return {}
    return {key:value for key in TOKEN_FIELDS if type(value:=raw.get(key)) is int and 0<=value<=10**10}


class UsageStore:
    def __init__(self,library):
        self.library=library
        with library._connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS model_calls(
              id TEXT PRIMARY KEY,job_id TEXT,created_at TEXT,finished_at TEXT,state TEXT,
              model TEXT,provider_host TEXT,output_schema TEXT,prompt_sha256 TEXT,attempt INTEGER,
              duration_ms INTEGER,http_status INTEGER,usage TEXT,cost_estimate TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS model_calls_job ON model_calls(job_id,created_at)')
            if 'cost_estimate' not in {r['name'] for r in db.execute('PRAGMA table_info(model_calls)')}:
                db.execute('BEGIN IMMEDIATE')
                if 'cost_estimate' not in {r['name'] for r in db.execute('PRAGMA table_info(model_calls)')}:
                    db.execute('ALTER TABLE model_calls ADD COLUMN cost_estimate TEXT')

    def begin(self,job_id,endpoint,model,schema,prompt,attempt):
        call_id=uuid4().hex
        safe=lambda text:re.sub(r'[^A-Za-z0-9_.:/-]','?',str(text))[:120]
        with self.library._connection() as db:
            if not db.execute('SELECT 1 FROM research_jobs WHERE id=?',(job_id,)).fetchone():raise KeyError(job_id)
            db.execute('INSERT INTO model_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (call_id,job_id,_now(),None,'in_flight',safe(model),urlsplit(endpoint).hostname,
                 safe(schema),hashlib.sha256(prompt.encode()).hexdigest(),attempt,None,None,'{}',None))
            self._event(db,job_id,'model_call',f'正在调用模型：{safe(schema)}（本轮第{attempt}次请求）。')
        return call_id

    @staticmethod
    def _event(db,job_id,stage,message):
        now=_now()
        db.execute('INSERT INTO research_job_events(job_id,stage,message,created_at) VALUES(?,?,?,?)',(job_id,stage,message,now))
        db.execute('UPDATE research_jobs SET updated_at=? WHERE id=?',(now,job_id))

    def end(self,job_id,call_id,state,elapsed,status=None,usage=None):
        with self.library._connection() as db:
            row=db.execute('SELECT model,provider_host FROM model_calls WHERE id=? AND job_id=?',(call_id,job_id)).fetchone()
            estimate=estimate_cost(row['model'],row['provider_host'],usage or {}) if row and status==200 else None
            db.execute('UPDATE model_calls SET finished_at=?,state=?,duration_ms=?,http_status=?,usage=?,cost_estimate=? WHERE id=? AND job_id=?',
                (_now(),state,max(0,int(elapsed*1000)),status,json.dumps(usage or {}),json.dumps(estimate) if estimate else None,call_id,job_id))
            message='模型已返回，继续校验内容、引用及完整性。' if state=='response_received' else '本次模型请求未完成；结果与用量按实际状态记录。'
            self._event(db,job_id,'model_response',message)

    def recover(self,job_id,db):
        db.execute("UPDATE model_calls SET state='interrupted_unknown',finished_at=? WHERE job_id=? AND state='in_flight'",(_now(),job_id))

    @staticmethod
    def summary(db,job_id):
        # One small scalar row per attempt; never read model output/checkpoints.
        rows=db.execute('SELECT state,usage,cost_estimate FROM model_calls WHERE job_id=?',(job_id,)).fetchall()
        usages=[json.loads(row['usage']) for row in rows]
        totals={key:sum(u[key] for u in usages if key in u) if any(key in u for u in usages) else None for key in TOKEN_FIELDS}
        estimates=[json.loads(r['cost_estimate']) for r in rows if r['cost_estimate']]
        cost={'currency':'USD','min':str(sum((Decimal(e['min']) for e in estimates),Decimal(0))),
            'max':str(sum((Decimal(e['max']) for e in estimates),Decimal(0))), 'estimated_calls':len(estimates),
            'price_checked_at':PRICE_CHECKED,'price_source':PRICE_SOURCE} if estimates else None
        return {'calls':len(rows),'in_flight':sum(r['state']=='in_flight' for r in rows),
            'unknown_outcomes':sum(r['state'] in {'interrupted_unknown','transport_unknown'} for r in rows),
            'usage_reported_calls':sum('total_tokens' in u for u in usages),**totals,
            'cost':cost,'cost_status':'public_price_range_not_invoice' if cost else 'not_calculated','source':'provider_reported; excludes unreported usage and historical calls'}

    @staticmethod
    def calls(db,job_id):
        return [{**dict(row),'usage':json.loads(row['usage']),
            'cost_estimate':json.loads(row['cost_estimate']) if row['cost_estimate'] else None}
            for row in db.execute('SELECT * FROM model_calls WHERE job_id=? ORDER BY created_at,id',(job_id,))]


@contextmanager
def record_model_calls(store,job_id):
    token=_active.set((store,job_id))
    try:yield
    finally:_active.reset(token)


async def tracked_post(client,endpoint,*,headers,json,model,schema,prompt,attempt):
    active=_active.get()
    if not active:return await client.post(endpoint,headers=headers,json=json)
    store,job_id=active
    call_id=store.begin(job_id,endpoint,model,schema,prompt,attempt)
    started=time.monotonic()
    try:
        response=await client.post(endpoint,headers=headers,json=json)
    except BaseException as error:
        state='not_connected' if isinstance(error,(httpx.ConnectError,httpx.ConnectTimeout)) else 'transport_unknown'
        store.end(job_id,call_id,state,time.monotonic()-started)
        raise
    try:envelope=response.json() if len(response.content)<=2*1024*1024 else None
    except ValueError:envelope=None
    store.end(job_id,call_id,'response_received',time.monotonic()-started,response.status_code,sanitized_usage(envelope))
    return response
