"""Durable research task journal with recoverable per-block translation progress."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime,timedelta,timezone
import re
from uuid import uuid4

from .library import Library, _now


class TaskCancelled(Exception):
    pass


class JobStore:
    KINDS = {'qa','compare','translate','translate_text','review','future_work','future_filter','draft','discover_qa','agent_research'}
    SUMMARY_COLUMNS = 'id,kind,status,error,progress,total,cancel_requested,created_at,updated_at,result IS NOT NULL AS has_result'
    HISTORY_COLUMNS = """
        json_extract(payload,'$.question') AS history_question,
        CASE WHEN kind IN ('review','draft','future_work')
            THEN json_extract(payload,'$.instruction')
        END AS history_instruction,
        CASE WHEN kind IN ('agent_research','future_filter')
            THEN json_extract(payload,'$.source_task_id')
        END AS history_parent,
        CASE kind
            WHEN 'agent_research' THEN COALESCE(json_extract(payload,'$.agent_scope.document_ids'),
                                               json_extract(payload,'$.document_ids'))
            WHEN 'translate' THEN json_array(json_extract(payload,'$.document_id'))
            WHEN 'translate_text' THEN NULL
            WHEN 'discover_qa' THEN (
                SELECT json_array(json_extract(payload,'$.document_id'))
                FROM research_job_items WHERE job_id=research_jobs.id AND item_key='download'
            )
            WHEN 'future_filter' THEN (
                WITH RECURSIVE sources(id) AS (
                    SELECT json_extract(research_jobs.payload,'$.source_task_id')
                    UNION
                    SELECT json_extract(source.payload,'$.source_task_id')
                    FROM sources JOIN research_jobs AS source USING(id)
                    WHERE source.kind='future_filter'
                )
                SELECT json_extract(source.payload,'$.document_ids')
                FROM sources JOIN research_jobs AS source USING(id) WHERE source.kind='future_work'
            )
            ELSE json_extract(payload,'$.document_ids')
        END AS history_documents,
        CASE WHEN kind='agent_research' THEN (
            SELECT json_object('run_ids',json_extract(payload,'$.run_ids'),
                               'imported',json_extract(payload,'$.imported'))
            FROM research_agent_state WHERE job_id=research_jobs.id
        ) END AS history_agent
    """

    def __init__(self, library: Library):
        self.library = library
        with library._connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS research_jobs(
                id TEXT PRIMARY KEY,kind TEXT,status TEXT,payload TEXT,result TEXT,error TEXT,
                progress INTEGER,total INTEGER,cancel_requested INTEGER,created_at TEXT,updated_at TEXT);
              CREATE INDEX IF NOT EXISTS research_jobs_created_at ON research_jobs(created_at);
              CREATE TABLE IF NOT EXISTS research_job_items(
                job_id TEXT,item_key TEXT,payload TEXT,PRIMARY KEY(job_id,item_key));
              CREATE TABLE IF NOT EXISTS research_job_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT,stage TEXT,message TEXT,created_at TEXT);
              CREATE TABLE IF NOT EXISTS research_job_stream(
                job_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS research_agent_state(
                job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, ready_at TEXT NOT NULL);
            ''')
            from .translation_pages import create_schema
            create_schema(db)
        from .model_usage import UsageStore
        self.usage_store=UsageStore(library)

    def create(self,kind,payload):
        if kind not in self.KINDS:
            raise ValueError('不支持的研究任务。')
        job_id = uuid4().hex
        with self.library._connection() as db:
            db.execute('INSERT INTO research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (job_id,kind,'queued',json.dumps(payload,ensure_ascii=False),None,None,0,0,0,_now(),_now()))
        self.event(job_id,'queued','任务已进入队列。')
        return self.get(job_id)

    def get(self,job_id,*,include_items=True,include_result=True):
        if not include_result:return self.header(job_id)
        with self.library._connection() as db:
            row = db.execute('SELECT * FROM research_jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            item = dict(row)
            item['payload'] = json.loads(item['payload'])
            item['result'] = json.loads(item['result']) if item['result'] else None
            item['events'] = [dict(r) for r in db.execute('SELECT stage,message,created_at FROM research_job_events WHERE job_id=? ORDER BY id',(job_id,))]
            item['completed_items'] = [json.loads(r[0]) for r in db.execute('SELECT payload FROM research_job_items WHERE job_id=? ORDER BY rowid',(job_id,))] if include_items else []
            item['usage']=self.usage_store.summary(db,job_id)
            item['model_calls']=self.usage_store.calls(db,job_id)
            item['stream']=self.stream_snapshot(db,job_id)
            item.update(self._job_history(db,job_id))
        return item

    def header(self,job_id):
        """Bounded history view; no complete results, checkpoints or call bodies."""
        from .translation_pages import header_row,summary
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            item=header_row(db,job_id)
            info=summary(db,item) if item['kind']=='translate' else None
            item['payload']=json.loads(item['payload']);item['has_result']=bool(item['has_result'])
            item.update(result=None,result_deferred=item['has_result'],completed_items=[],items_deferred=True,
                        model_calls=[],model_calls_deferred=True,translation_summary=info)
            events=[dict(r) for r in db.execute('''SELECT stage,message,created_at FROM research_job_events
                  WHERE job_id=? ORDER BY id DESC LIMIT 100''',(job_id,))]
            item['events']=list(reversed(events))
            item['event_count']=db.execute('SELECT COUNT(*) FROM research_job_events WHERE job_id=?',(job_id,)).fetchone()[0]
            item['events_truncated']=item['event_count']>len(events)
            item['usage']=self.usage_store.summary(db,job_id);item['stream']=self.stream_snapshot(db,job_id)
            item.update(self._job_history(db,job_id))
        return item

    def translation_page(self,job_id,**options):
        from .translation_pages import page
        return page(self.library,job_id,**options)

    def summary(self,job_id,*,include_stage_event=False,include_history=True):
        """Bounded status payload for polling/SSE; full text stays behind get()."""
        columns = self.SUMMARY_COLUMNS
        if include_history:
            columns += f',{self.HISTORY_COLUMNS}'
        with self.library._connection() as db:
            if include_stage_event:
                db.execute('BEGIN')
            row = db.execute(f'SELECT {columns} FROM research_jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            item = self._summary(db,row,self._history_metadata(row) if include_history else {})
            if include_stage_event and item['kind'] == 'agent_research':
                event = item['last_event']
                if event and event['stage'] in {'model_call','model_response'}:
                    event = db.execute('''SELECT stage,message,created_at FROM research_job_events
                        WHERE job_id=? AND stage NOT IN ('model_call','model_response') ORDER BY id DESC LIMIT 1''',
                        (job_id,)).fetchone()
                item['last_stage_event'] = dict(event) if event else None
            return item

    @staticmethod
    def _history_metadata(row):
        title = ' '.join((row['history_question'] or '').split())
        if not title:
            title = ' '.join((row['history_instruction'] or '').split())
        if len(title) > 72:
            title = title[:72].rstrip() + '…'
        documents = json.loads(row['history_documents'] or '[]')
        agent = json.loads(row['history_agent'] or '{}')
        documents.extend(agent.get('run_ids') or {})
        documents.extend(record.get('document_id') for record in (agent.get('imported') or {}).values())
        return {'title':title or None,
                'document_ids':list(dict.fromkeys(value for value in documents if isinstance(value,str) and value)),
                'source_task_id':row['history_parent']}

    def _job_history(self,db,job_id):
        row = db.execute(f'SELECT {self.HISTORY_COLUMNS} FROM research_jobs WHERE id=?',(job_id,)).fetchone()
        return self._history_metadata(row)

    @staticmethod
    def _summary(db,row,history):
        item = {key:row[key] for key in row.keys() if not key.startswith('history_')}
        item.update(history)
        item['has_result'] = bool(item['has_result'])
        event = db.execute('SELECT stage,message,created_at FROM research_job_events WHERE job_id=? ORDER BY id DESC LIMIT 1',(item['id'],)).fetchone()
        item['last_event'] = dict(event) if event else None
        from .model_usage import UsageStore
        item['usage']=UsageStore.summary(db,item['id'])
        return item

    @staticmethod
    def stream_snapshot(db,job_id):
        row=db.execute('SELECT revision,payload FROM research_job_stream WHERE job_id=?',(job_id,)).fetchone()
        return {**json.loads(row['payload']),'revision':row['revision']} if row else None

    def set_stream(self,job_id,payload):
        with self.library._connection() as db:
            self._exists(db,job_id)
            db.execute('INSERT INTO research_job_stream VALUES(?,1,?) ON CONFLICT(job_id) DO UPDATE SET revision=revision+1,payload=excluded.payload',
                (job_id,json.dumps(payload,ensure_ascii=False)))

    def clear_stream(self,job_id):
        self.set_stream(job_id,{'status':'cleared','text':'','verified':False})

    def live_stream(self,job_id):
        with self.library._connection() as db:
            self._exists(db,job_id)
            return self.stream_snapshot(db,job_id)

    def list(self,*,document_id=None,status=None):
        with self.library._connection() as db:
            db.execute('BEGIN')
            query = f'SELECT {self.SUMMARY_COLUMNS},{self.HISTORY_COLUMNS} FROM research_jobs'
            parameters = ()
            if status is not None:
                query += ' WHERE status=?'
                parameters = (status,)
            query += ' ORDER BY created_at DESC,rowid DESC'
            if document_id is None:
                query += ' LIMIT 50'
            items = []
            for row in db.execute(query,parameters):
                history = self._history_metadata(row)
                if document_id is not None and document_id not in history['document_ids']:
                    continue
                items.append(self._summary(db,row,history))
                if len(items) == 50:
                    break
            return items

    def history_documents(self):
        with self.library._connection() as db:
            return [dict(row) for row in db.execute('SELECT id,title,archived_at FROM documents ORDER BY created_at DESC')]

    def event(self,job_id,stage,message):
        with self.library._connection() as db:
            db.execute('INSERT INTO research_job_events(job_id,stage,message,created_at) VALUES(?,?,?,?)',(job_id,stage,message,_now()))

    def cancel(self,job_id,*,include_result=True):
        with self.library._connection() as db:
            self._exists(db,job_id)
            db.execute("UPDATE research_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=? AND status IN ('queued','running')",(_now(),job_id))
        return self.get(job_id,include_result=include_result)

    @staticmethod
    def _exists(db,job_id):
        if not db.execute('SELECT 1 FROM research_jobs WHERE id=?',(job_id,)).fetchone():
            raise KeyError(job_id)

    def check_cancel(self,job_id):
        with self.library._connection() as db:
            row = db.execute('SELECT cancel_requested FROM research_jobs WHERE id=?',(job_id,)).fetchone()
        if not row or row[0]:
            raise TaskCancelled()

    def save_item(self,job_id,key,payload,total):
        # The model already returned successfully. Persist that paid response even
        # when cancellation arrived in flight; cancellation governs the next call.
        with self.library._connection() as db:
            self._exists(db,job_id)
            db.execute('INSERT OR REPLACE INTO research_job_items VALUES(?,?,?)',(job_id,key,json.dumps(payload,ensure_ascii=False)))
            count = db.execute("SELECT COUNT(*) FROM research_job_items WHERE job_id=? AND item_key NOT IN ('review_validation','grounding_validation') AND item_key NOT LIKE 'diagnostic:%'",(job_id,)).fetchone()[0]
            db.execute('UPDATE research_jobs SET progress=?,total=?,updated_at=? WHERE id=?',(count,total,_now(),job_id))

    def saved_result(self, job_id):
        """Read only the persisted source task and result for continuation."""
        with self.library._connection() as db:
            row = db.execute('SELECT id,kind,status,payload,result FROM research_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return {**dict(row), 'payload': json.loads(row['payload']),
                'result': json.loads(row['result']) if row['result'] else None}

    def load_agent_state(self, job_id):
        # The private protocol transcript is separate from browser-visible items.
        with self.library._connection() as db:
            row = db.execute('SELECT payload FROM research_agent_state WHERE job_id=?', (job_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_agent_state(self, job_id, state, *, wait_seconds=0):
        ready_at = (datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)).isoformat() if wait_seconds else ''
        with self.library._connection() as db:
            self._exists(db, job_id)
            db.execute('INSERT INTO research_agent_state VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload,ready_at=excluded.ready_at',
                       (job_id, json.dumps(state, ensure_ascii=False, allow_nan=False), ready_at))
            db.execute('UPDATE research_jobs SET updated_at=? WHERE id=?', (_now(), job_id))

    def retry(self,job_id,*,include_result=True):
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._exists(db,job_id)
            status=db.execute('SELECT status FROM research_jobs WHERE id=?',(job_id,)).fetchone()[0]
            if status in {'failed','cancelled','interrupted'}:
                for row in db.execute("SELECT item_key,payload FROM research_job_items WHERE job_id=? AND item_key IN ('grounding_validation','review_validation')",(job_id,)).fetchall():
                    key='diagnostic:'+row['item_key']+':'+uuid4().hex
                    record={'stage':key,'origin_stage':row['item_key'],'recorded_at':_now(),
                        'model_call_count':self.usage_store.summary(db,job_id)['calls'],'result':json.loads(row['payload'])['result']}
                    db.execute('INSERT INTO research_job_items VALUES(?,?,?)',(job_id,key,json.dumps(record,ensure_ascii=False)))
            changed = db.execute("UPDATE research_jobs SET status='queued',cancel_requested=0,error=NULL,updated_at=? WHERE id=? AND status IN ('failed','cancelled','interrupted')",(_now(),job_id)).rowcount
        if changed:
            self.clear_stream(job_id)
            self.event(job_id,'retry','已按用户操作重新排队；保留成功检查点。中断时未保存的调用可能已计费，重试可能产生重复费用。')
        return self.get(job_id,include_result=include_result)

    def recover(self):
        message = '执行进程已中断，外部模型调用结果未知。已保存的翻译进度保留；任务不会自动重放。明确重试后才继续，未保存的调用可能产生重复费用。'
        with self.library._connection() as db:
            rows = db.execute("SELECT id,cancel_requested FROM research_jobs WHERE status='running'").fetchall()
            for row in rows:
                db.execute('DELETE FROM research_job_stream WHERE job_id=?',(row['id'],))
                self.usage_store.recover(row['id'],db)
                status = 'cancelled' if row['cancel_requested'] else 'interrupted'
                db.execute('UPDATE research_jobs SET status=?,error=?,updated_at=? WHERE id=?',(status,message,_now(),row['id']))
                db.execute('INSERT INTO research_job_events(job_id,stage,message,created_at) VALUES(?,?,?,?)',(row['id'],status,message,_now()))
        return len(rows)

    def claim(self):
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            threshold=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            row = db.execute("""SELECT id FROM research_jobs WHERE status='queued'
                AND NOT(kind='discover_qa' AND progress=1 AND updated_at>?)
                AND NOT EXISTS(SELECT 1 FROM research_agent_state s WHERE s.job_id=research_jobs.id AND s.ready_at>?)
                ORDER BY updated_at,created_at LIMIT 1""", (threshold, _now())).fetchone()
            if not row:
                return None
            db.execute("UPDATE research_jobs SET status='running',updated_at=? WHERE id=?",(_now(),row[0]))
        return self.get(row[0])

    def finish(self,job_id,status,*,result=None,error=None):
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._exists(db,job_id)
            if status == 'completed' and db.execute('SELECT cancel_requested FROM research_jobs WHERE id=?',(job_id,)).fetchone()[0]:
                status = 'cancelled'
                error = '已停止后续调用；当前调用已经完成，返回结果已保存。'
            db.execute('UPDATE research_jobs SET status=?,result=?,error=?,updated_at=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,_now(),job_id))
            db.execute('DELETE FROM research_job_stream WHERE job_id=?',(job_id,))
        self.event(job_id,status,'任务完成。' if status=='completed' else error or '任务已取消。')

    def _set_total(self,job_id,total):
        with self.library._connection() as db:
            db.execute('UPDATE research_jobs SET total=?,updated_at=? WHERE id=?',(total,_now(),job_id))

    def _yield_step(self,job_id,*,message=None,stage='queued',repeat_event=True):
        """Release the queue after one step, including a racing cancellation."""
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            cancelled = db.execute('SELECT cancel_requested FROM research_jobs WHERE id=?',(job_id,)).fetchone()[0]
            status = 'cancelled' if cancelled else 'queued'
            now = _now()
            db.execute('UPDATE research_jobs SET status=?,updated_at=? WHERE id=?',(status,now,job_id))
            if not cancelled and not repeat_event:
                previous = db.execute('SELECT stage FROM research_job_events WHERE job_id=? ORDER BY id DESC LIMIT 1',
                                      (job_id,)).fetchone()
                if previous and previous['stage'] == stage:
                    return
            event_stage = status if cancelled else stage
            event_message = '已保存当前片段并停止后续调用。' if cancelled else message or '已保存当前片段，等待下一轮继续。'
            db.execute('INSERT INTO research_job_events(job_id,stage,message,created_at) VALUES(?,?,?,?)',
                       (job_id,event_stage,event_message,now))

    async def _text_step(self,service,job):
        """Save one segment per step; share the service's bounded language repair."""
        from .intelligence import (MAX_TRANSLATION_TEXT_CHARS, PROMPT_VERSION, ModelOutputError,
            protect_text, restore_text, split_protected_text, translate_protected_chunk,validate_glossary)
        service._require_model()
        payload,job_id = job['payload'],job['id']
        source = payload['text']
        glossary=validate_glossary(payload.get('glossary'))
        language = payload.get('target_language','en')
        if not isinstance(source,str) or not source.strip() or len(source)>MAX_TRANSLATION_TEXT_CHARS:
            raise ValueError('翻译文本为空或超过允许长度。')
        if language not in {'zh','en'}:
            raise ValueError('当前翻译目标仅支持zh或en。')
        protected,replacements = protect_text(source,glossary=glossary)
        chunks = split_protected_text(protected,max_chars=4000)
        self._set_total(job_id,len(chunks))
        done = {item['segment']:item for item in job['completed_items']}
        for index,chunk in enumerate(chunks,1):
            if index in done:
                continue
            self.check_cancel(job_id)
            output = await translate_protected_chunk(service.provider,chunk,language,index,len(chunks),
                                                     check_cancel=lambda:self.check_cancel(job_id))
            item = {'segment':index,'total_segments':len(chunks),
                    'source_text':restore_text(chunk,replacements),
                    'translated_text':restore_text(output.translated_text,replacements,glossary=glossary)}
            self.save_item(job_id,f'segment:{index}',item,len(chunks))
            done[index] = item
            if len(done)<len(chunks):
                self._yield_step(job_id)
                return None
            break
        segments = [done[index] for index in range(1,len(chunks)+1)]
        translated = '\n'.join(item['translated_text'] for item in segments)
        if Counter(re.findall(r'\d+(?:[.,]\d+)*',source)) != Counter(re.findall(r'\d+(?:[.,]\d+)*',translated)):
            raise ModelOutputError('翻译改变了原文数字，结果已拒绝。')
        return {'status':'completed','source_text':source,'translated_text':translated,
                'target_language':language,'segments':segments,'prompt_version':PROMPT_VERSION,'glossary':glossary or {},
                'verification':'protected_spans_and_numeric_preservation; target_language_sanity; translation_quality_not_guaranteed',
                'trace':[service._trace('translate',f'已核验目标语言并持久保存{len(chunks)}个片段；可从已保存片段继续。')]}

    async def process_next(self, service=None):
        from .model_usage import record_model_calls
        from .model_stream import stream_job
        # ContextVars keep concurrent accounts and reused providers independent.
        job=self.claim()
        if job is None:return False
        with record_model_calls(self.usage_store,job['id']), stream_job(self,job['id']):
            return await self._process_next_recorded(service,job)

    async def _process_next_recorded(self, service,job):
        from .intelligence import IntelligenceService
        job_id, kind, payload = job['id'], job['kind'], job['payload']
        service = service or IntelligenceService(self.library)
        try:
            self.check_cancel(job_id)
            if job['result'] is not None:
                # A cancellation may race a completed single-call answer. Retrying
                # that task should expose its saved answer, not purchase it again.
                self.finish(job_id,'completed',result=job['result'])
                return True
            if kind != 'agent_research':
                self.event(job_id,'execute','读取选定资料并执行'+kind+'任务。')
            if kind=='agent_research':
                from .agent_workflow import agent_step
                result = await agent_step(self, service, job)
                if result is None:
                    return True
            elif kind=='discover_qa':
                from .reading_workflow import reading_step
                result=await reading_step(self,service,job)
                if result is None:return True
            elif kind in {'qa','compare'}:
                result = await service.answer(payload['question'],payload['document_ids'],mode=kind,
                                              check_cancel=lambda:self.check_cancel(job_id))
            elif kind=='translate':
                # Freeze the parse version at task creation; retained blocks are immutable.
                selection = service.list_translation_blocks(payload['document_id'],run_id=payload.get('run_id'),page=payload.get('page'),block_ids=payload.get('block_ids'))
                if payload.get('selected_text') is not None and len(selection['blocks']) != 1:
                    raise ValueError('片段翻译必须限定在一个原文文本块。')
                self._set_total(job_id,len(selection['blocks']))
                done = {item['block_id']:item for item in job['completed_items']}
                for block in selection['blocks']:
                    self.check_cancel(job_id)
                    if block['block_id'] not in done:
                        if payload.get('selected_text') is not None:
                            source = service._canonical_block(block)
                            selected = payload['selected_text']
                            if not isinstance(selected,str) or not selected.strip() or len(selected)>16000 or selected not in source.text:
                                raise ValueError('选中文字不属于该文献版本的原文文本块。')
                            translated = await service.translate_text(selected,target_language=payload.get('target_language','zh'),
                                                                       check_cancel=lambda:self.check_cancel(job_id),
                                                                       **({'glossary':payload['glossary']} if payload.get('glossary') else {}))
                            translated.update({'document_id':source.document_id,'run_id':source.run_id,
                                'block_id':source.block_id,'evidence_id':source.evidence_id,
                                'page':source.page,'bbox':source.bbox,'source_scope':'selected_text_within_block'})
                        else:
                            translated = await service.translate_block(block,target_language=payload.get('target_language','zh'),
                                                                        check_cancel=lambda:self.check_cancel(job_id),
                                                                        **({'glossary':payload['glossary']} if payload.get('glossary') else {}))
                        self.save_item(job_id,block['block_id'],translated,len(selection['blocks']))
                        done[block['block_id']] = translated
                        if len(done)<len(selection['blocks']):
                            self._yield_step(job_id)
                            return True
                        break
                result = {key:value for key,value in selection.items() if key!='blocks'}
                result.update({'blocks':[done[b['block_id']] for b in selection['blocks']], 'target_language':payload.get('target_language','zh'),
                               'status':'partial' if selection.get('missing_pages') else 'completed','glossary':payload.get('glossary',{})})
            elif kind=='translate_text':
                result = await self._text_step(service,job)
                if result is None:
                    return True
            elif kind=='future_filter':
                from .future_filter import filter_step
                source=self.get(payload['source_task_id'])
                if source['status']!='completed' or not (source.get('result') or {}).get('future_fulltext'):
                    raise ValueError('请选择本工作空间中已完成正文核验的任务。')
                result=await filter_step(self,service,job,source['result'])
                if result is None:return True
            elif kind=='future_work':
                from .future_verification import verify_future_work
                saved = {item.get('stage'):item.get('result') for item in job['completed_items']}
                initial_total = 4 if payload.get('full_text_verify',False) else 2
                if saved.get('future_synthesis') is None:
                    synthesis = await service.synthesize(kind,payload['document_ids'],instruction=payload.get('instruction',''),
                                                         check_cancel=lambda:self.check_cancel(job_id))
                    self.save_item(job_id,'future_synthesis',{'stage':'future_synthesis','result':synthesis},initial_total)
                    self.event(job_id,'synthesis_saved','已保存原文支持的候选方向；下一轮核验最新公开研究。')
                    self._yield_step(job_id)
                    return True
                if saved.get('future_verification') is not None:
                    result = saved['future_verification']
                else:
                    result = await verify_future_work(self.library,service.provider,saved['future_synthesis'],
                        check_cancel=lambda:self.check_cancel(job_id),
                        **({'force_refresh':payload['refresh_search'],'lookback_days':payload.get('research_days',90)}
                           if 'refresh_search' in payload else {}))
                    self.save_item(job_id,'future_verification',{'stage':'future_verification','result':result},initial_total)
                if payload.get('full_text_verify', False):
                    from .future_fulltext import fulltext_step
                    result = await fulltext_step(self,service,job,result)
                    if result is None:return True
                    if payload.get('assess_solved',False):
                        from .future_filter import filter_step
                        result=await filter_step(self,service,job,result)
                        if result is None:return True
            else:
                result = await service.synthesize(kind,payload['document_ids'],instruction=payload.get('instruction',''),
                                                  check_cancel=lambda:self.check_cancel(job_id))
            # finish atomically observes cancellation but retains a completed response.
            self.finish(job_id,'completed',result=result)
        except TaskCancelled:
            self.finish(job_id,'cancelled')
        except Exception as exc:
            # Only service-level sanitized messages may reach the browser.
            from .intelligence import ModelServiceError, ModelOutputError
            error = str(exc) if isinstance(exc,(ModelServiceError,ValueError)) else '任务失败，请检查本机服务日志与输入资料后重试。'
            diagnostics = getattr(exc, 'review_diagnostics', None)
            if isinstance(exc, ModelOutputError) and isinstance(diagnostics, dict):
                # Keep failed review evidence as a diagnostic checkpoint, never
                # as a generated result or a completed progress item.
                with self.library._connection() as db:
                    db.execute('INSERT OR REPLACE INTO research_job_items(job_id,item_key,payload) VALUES(?,?,?)',
                        (job_id, 'review_validation', json.dumps({'stage':'review_validation',
                            'result':diagnostics}, ensure_ascii=False)))
            diagnostics = getattr(exc, 'grounding_diagnostics', None)
            if isinstance(exc, ModelOutputError) and isinstance(diagnostics, dict):
                with self.library._connection() as db:
                    db.execute('INSERT OR REPLACE INTO research_job_items(job_id,item_key,payload) VALUES(?,?,?)',
                        (job_id, 'grounding_validation', json.dumps({'stage':'grounding_validation',
                            'result':diagnostics}, ensure_ascii=False)))
            self.finish(job_id,'failed',error=error[:600])
        return True
