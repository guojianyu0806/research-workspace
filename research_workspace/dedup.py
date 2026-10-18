"""Conservative, reversible document deduplication. Never delete evidence."""
from itertools import combinations
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
from uuid import uuid4

from .library import LibraryError, _now
from . import semantic

MAX_DOCUMENTS = 60
MAX_DOCUMENT_CHARS = 250_000
MAX_TOTAL_CHARS = 3_000_000
MAX_PAIRS = 120
_scan_slot = threading.Lock()


class DedupConflict(LibraryError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def identifiers(doc, blocks):
    """Only an explicit first-page label counts as document identity evidence."""
    pattern = r'(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?'
    filename = re.fullmatch(pattern, Path(doc['filename']).stem, re.I)
    arxiv, dois, evidence = set(), set(), []
    for b in blocks:
        if b['page'] != 1 or len(b['text']) > 400:
            continue
        text = ' '.join(b['text'].split())
        m = re.match(r'^arxiv\s*:\s*' + pattern, text, re.I)
        box=b.get('bbox') or [0,0,1,1]
        if m and not (box[1]<.2 or box[0]<.12 or box[2]>.88):m=None
        d = re.match(r'^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/\S+)', text, re.I)
        if m:arxiv.add(m[1].casefold());evidence.append(b)
        if d:dois.add(d[1].rstrip('.,;').casefold());evidence.append(b)
    inconsistent = len(arxiv)>1 or len(dois)>1 or bool(filename and arxiv and filename[1].casefold() not in arxiv)
    return {'arxiv':sorted(arxiv), 'doi':sorted(dois), 'filename_arxiv':filename[1].casefold() if filename else None,
        'internally_conflicting':inconsistent, 'evidence':evidence}


def signature(doc, run, blocks):
    # Bibliographies can be shared by distinct works. Do not let their words or
    # their cited arXiv identifiers establish the paper's own identity.
    body = []
    for b in blocks:
        if re.fullmatch(r'(?:\d+\s+)?(?:references|bibliography|参考文献)', b['text'].strip(), re.I):break
        body.append(b)
    text = normalize(' '.join(b['text'] for b in body))
    tokens = re.findall(r'\w+', text)
    shingles = {hashlib.blake2b(' '.join(tokens[i:i+5]).encode(), digest_size=8).hexdigest()
        for i in range(max(0,len(tokens)-4))}
    prose = [b for b in body if len(re.findall(r'\w+',b['text'])) >= 20]
    samples = [{**prose[i], 'text':prose[i]['text'][:1600],
        'sample_span':{'start':0,'end':min(1600,len(prose[i]['text']))},
        'source_text_length':len(prose[i]['text']), 'truncated':len(prose[i]['text'])>1600}
        for i in sorted({0,len(prose)//2,len(prose)-1})] if prose else []
    identity = identifiers(doc,blocks)
    source = {'document_id':doc['id'], 'filename':doc['filename'], 'title':doc['title'], 'authors':doc['authors'],
        'source_sha256':doc['sha256'], 'run_id':run['id'], 'parse_version':run['version'], 'parse_status':run['status'],
        'pages_done':run['pages_done'], 'total_pages':run['total_pages'], 'archived_at':doc['archived_at'],
        'family_id':doc['version_family_id'], 'is_current_version':doc['is_current_version'],
        'block_count':len(blocks), 'body_tokens':len(tokens),
        'body_sha256':hashlib.sha256(' '.join(' '.join(b['text'] for b in body).split()).encode()).hexdigest(),
        'blocks_sha256':digest(blocks)}
    return {'source':source,'identity':identity,'shingles':shingles,'samples':samples}


def pair_assessment(a,b,similarity):
    left,right=a['source'],b['source'];ia,ib=a['identity'],b['identity']
    conflicts = ia['internally_conflicting'] or ib['internally_conflicting'] or any(
        ia[k] and ib[k] and not set(ia[k]) & set(ib[k]) for k in ['arxiv','doi'])
    same_id = any(set(ia[k]) & set(ib[k]) for k in ['arxiv','doi'])
    union = a['shingles'] | b['shingles']
    overlap = len(a['shingles'] & b['shingles']) / len(union) if union else 0
    enough = min(left['body_tokens'],right['body_tokens']) >= 200
    exact = enough and left['body_sha256'] == right['body_sha256']
    complete = left['parse_status']==right['parse_status']=='completed'
    looks_related = same_id or exact or overlap>=.25 or (similarity is not None and similarity>=.80)
    if not looks_related and not conflicts:return None
    can_group=False
    if conflicts:
        status='identity_conflict';reason='论文自身标识不同或存在冲突，保持分开；主题相近和正文重合不能覆盖身份差异。'
    elif left['family_id'] and left['family_id']==right['family_id']:
        status='already_grouped';reason='已属于同一版本组，本次不重复分组。'
    elif left['family_id'] or right['family_id']:
        status='existing_group';reason='至少一篇已在版本组中；不跨组自动传播配对结论。'
    elif not complete or not enough:
        status='insufficient_evidence';reason='完整可读正文或正文数量不足，不能确认重复。'
    elif same_id and overlap>=.35:
        status='same_work_candidate';reason='自身标识一致且主要正文有实质重合；请核对两侧原文后确认。';can_group=True
    elif exact:
        status='text_copy_candidate';reason='完整可读主要正文一致；图片和未提取内容未证明相同，仍需人工核对。';can_group=True
    else:
        status='related_only';reason='主题或部分正文相近，论文身份尚未得到独立支持，保持分开。'
    return {'pair_id':'P_'+digest(sorted([left,right],key=lambda s:s['document_id'])),'left':left,'right':right,'classification':status,'can_group':can_group,
        'reason':reason,'body_overlap':round(overlap,6),'same_readable_body':exact,'own_identifier_agrees':bool(same_id),
        'semantic_similarity':similarity,'identities':{'left':ia,'right':ib},
        'source_samples':{'left':a['samples'],'right':b['samples']}}


class DedupService:
    def __init__(self,library,*,encoder=None):
        self.library=library;self.encoder=encoder
        with library._connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS dedup_scans (
                id TEXT PRIMARY KEY,payload TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dedup_actions (
                id TEXT PRIMARY KEY,scan_id TEXT NOT NULL,pair_id TEXT NOT NULL,
                payload TEXT NOT NULL,created_at TEXT NOT NULL,reverted_at TEXT);
                CREATE INDEX IF NOT EXISTS dedup_actions_scan ON dedup_actions(scan_id);''')

    def _snapshot(self,db,document_id=None):
        ids=[r[0] for r in db.execute('SELECT id FROM documents WHERE archived_at IS NULL ORDER BY created_at DESC,id LIMIT ?', (MAX_DOCUMENTS+1,))]
        if document_id:
            doc=self.library._document(db,document_id)
            if doc['archived_at']:raise LibraryError('请先恢复归档文献，再进行去重扫描。')
            ids=[document_id]+[i for i in ids if i!=document_id]
        truncated=len(ids)>MAX_DOCUMENTS;ids=ids[:MAX_DOCUMENTS];items=[];skipped=[];total=0
        for did in ids:
            doc=self.library._document(db,did);run=doc['available_run']
            if not run:skipped.append({'document_id':did,'reason':'no_readable_run'});continue
            rows=db.execute('SELECT id AS block_id,run_id,page,text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal',(run['id'],)).fetchall()
            size=sum(len(r['text']) for r in rows)
            if size>MAX_DOCUMENT_CHARS or total+size>MAX_TOTAL_CHARS:
                skipped.append({'document_id':did,'reason':'source_budget_exceeded'});continue
            total+=size
            blocks=[dict(r)|{'document_id':did,'bbox':json.loads(r['bbox'])} for r in rows]
            items.append(signature(doc,run,blocks))
        return items,skipped,truncated

    def _similarities(self,items):
        status=semantic.model_status() if self.encoder is None else {'status':'ready','model':'test_encoder','fingerprint':'test','dimensions':2}
        if status['status']!='ready':return {},status
        import numpy as np
        entries=[];vectors={};pending=[]
        with sqlite3.connect(self.library.data_dir/'semantic.sqlite3',timeout=30) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY,vector BLOB NOT NULL)')
            for item in items:
                source=item['source']
                for block in item['samples']:
                    text=block['text'][:1600]
                    key=digest(['dedup-three-regions-v1',status['fingerprint'],source['document_id'],source['run_id'],block['block_id'],text])
                    entries.append((key,source['document_id']))
                    row=db.execute('SELECT vector FROM vectors WHERE cache_key=?',(key,)).fetchone()
                    try:
                        if not row:raise ValueError('uncached')
                        vectors[key]=semantic.SemanticIndex._vector(np.frombuffer(row[0],dtype='<f4'),status['dimensions'])
                    except ValueError:pending.append((key,text))
            encode=self.encoder or semantic._encode
            for start in range(0,len(pending),24):
                batch=pending[start:start+24];values=encode([t for _,t in batch],False)
                if len(values)!=len(batch):raise ValueError('Embedding count mismatch')
                for (key,_),v in zip(batch,values):
                    vector=semantic.SemanticIndex._vector(v,status['dimensions']);vectors[key]=vector
                    db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)',(key,vector.astype('<f4').tobytes()))
                db.commit()
        grouped={}
        for key,did in entries:grouped.setdefault(did,[]).append(vectors[key])
        means={did:semantic.SemanticIndex._vector(np.mean(vs,axis=0),status['dimensions']) for did,vs in grouped.items()}
        scores={tuple(sorted([a,b])):round(float(means[a]@means[b]),6) for a,b in combinations(means,2)}
        return scores,{**status,'status':'applied','new_samples':len(pending),'cache_hits':len(entries)-len(pending),
            'scope':'up_to_three_canonical_main_body_blocks; first_1600_chars_each','network_used':False}

    def scan(self,document_id=None):
        if not _scan_slot.acquire(blocking=False):raise DedupConflict('已有去重扫描正在执行，请稍后重试。')
        try:
            with self.library._connection() as db:
                db.execute('BEGIN');items,skipped,truncated=self._snapshot(db,document_id)
            try:scores,info=self._similarities(items)
            except Exception:scores,info={}, {'status':'failed','error':'local_embedding_unavailable'}
            pairs=[];evaluated=0
            for a,b in combinations(items,2):
                if document_id and document_id not in [a['source']['document_id'],b['source']['document_id']]:continue
                evaluated+=1
                score=scores.get(tuple(sorted([a['source']['document_id'],b['source']['document_id']])))
                pair=pair_assessment(a,b,score)
                if pair:pairs.append(pair)
            pairs.sort(key=lambda p:(not p['can_group'],p['classification']!='identity_conflict',-p['body_overlap'],p['pair_id']))
            omitted=max(0,len(pairs)-MAX_PAIRS)
            report={'id':uuid4().hex,'created_at':_now(),'method':'conservative-semantic-dedup-v1','document_id':document_id,
                'semantic':info,'complete':not(skipped or truncated or omitted) and info.get('status')=='applied',
                'sources':[i['source'] for i in items],'skipped_sources':skipped,'documents_truncated':truncated,
                'pairs_omitted':omitted,'pairs_evaluated':evaluated,'pairs':pairs[:MAX_PAIRS],
                'automatic_merges':0,'model_calls':0,'network_used':False,
                'limits':{'max_documents':MAX_DOCUMENTS,'max_document_chars':MAX_DOCUMENT_CHARS,'max_total_chars':MAX_TOTAL_CHARS,'max_pairs':MAX_PAIRS},
                'limitations':['语义分数仅用于寻找候选，不证明论文身份；所有分组均需人工确认。',
                    '仅核对可提取正文；不证明图片、数学语义或未知OCR内容一致。',
                    '仅主题相近或身份冲突的条目不能通过去重入口分组；不跨现有版本组传播配对结论。']}
            with self.library._connection() as db:db.execute('INSERT INTO dedup_scans VALUES(?,?,?)',(report['id'],json.dumps(report,ensure_ascii=False),report['created_at']))
            return self.get(report['id'])
        finally:_scan_slot.release()

    def get(self,scan_id):
        with self.library._connection() as db:
            row=db.execute('SELECT payload FROM dedup_scans WHERE id=?',(scan_id,)).fetchone()
            if not row:raise KeyError(scan_id)
            result=json.loads(row[0]);pair_ids=[p['pair_id'] for p in result['pairs']]
            clause='scan_id=?' + (' OR pair_id IN ('+','.join('?' for _ in pair_ids)+')' if pair_ids else '')
            result['actions']=[{**json.loads(r['payload']),'reverted_at':r['reverted_at']} for r in db.execute(
                'SELECT * FROM dedup_actions WHERE '+clause+' ORDER BY created_at',[scan_id,*pair_ids])]
            return result

    def history(self):
        with self.library._connection() as db:
            return [{'id':r['id'],'created_at':r['created_at'],'document_id':p.get('document_id'),'pair_count':len(p['pairs']),
                'complete':p['complete']} for r in db.execute('SELECT * FROM dedup_scans ORDER BY created_at DESC LIMIT 20') for p in [json.loads(r['payload'])]]

    def decide(self,scan_id,pair_id,decision,preferred_id=None):
        if decision not in {'group','keep_separate'}:raise LibraryError('去重操作无效。')
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM dedup_scans WHERE id=?',(scan_id,)).fetchone()
            if not row:raise KeyError(scan_id)
            report=json.loads(row[0]);pair=next((p for p in report['pairs'] if p['pair_id']==pair_id),None)
            if not pair:raise KeyError(pair_id)
            if db.execute('SELECT 1 FROM dedup_actions WHERE pair_id=? AND reverted_at IS NULL',(pair_id,)).fetchone():raise DedupConflict('该组来源已有有效审核记录，请先撤销原操作。')
            ids=[pair[s]['document_id'] for s in ['left','right']]
            # Revalidate the exact metadata, grouping, completed run and original
            # block snapshot while holding the write transaction.
            current=[]
            for side,did in zip(['left','right'],ids):
                doc=self.library._document(db,did);run=doc['available_run']
                if not run or run['id']!=pair[side]['run_id']:raise DedupConflict('来源已变化，请重新扫描。')
                blocks=[dict(r)|{'document_id':did,'bbox':json.loads(r['bbox'])} for r in db.execute('SELECT id AS block_id,run_id,page,text,bbox FROM blocks WHERE run_id=? ORDER BY page,ordinal',(run['id'],))]
                item=signature(doc,run,blocks);current.append(item)
                if item['source']!=pair[side]:raise DedupConflict('文献、解析或分组已变化，请重新扫描后再确认。')
            family=None
            if decision=='group':
                assessment=pair_assessment(*current,pair['semantic_similarity'])
                if not pair['can_group'] or not assessment or not assessment['can_group']:raise DedupConflict('该候选缺少可确认的论文身份依据，不能分组。')
                if preferred_id not in ids:raise LibraryError('请选择这两篇文献中的一篇作为常用版本。')
                family=uuid4().hex
                db.execute('INSERT INTO version_families VALUES(?,?,?)',(family,preferred_id,_now()))
                for did in ids:db.execute('UPDATE documents SET version_family_id=? WHERE id=?',(family,did))
                db.execute('INSERT INTO version_relations VALUES(?,?,?,?,?,?)',(uuid4().hex,family,*ids,'dedup_confirmed',_now()))
            action={'id':uuid4().hex,'scan_id':scan_id,'pair_id':pair_id,'decision':decision,'document_ids':ids,
                'preferred_id':preferred_id if decision=='group' else None,'family_id':family,'created_at':_now(),
                'source_snapshots':[pair['left'],pair['right']]}
            if family:action['group_state']=self._group_state(db,family)
            db.execute('INSERT INTO dedup_actions VALUES(?,?,?,?,?,NULL)',(action['id'],scan_id,pair_id,json.dumps(action,ensure_ascii=False),action['created_at']))
            return action

    @staticmethod
    def _group_state(db,family):
        return {'members':[r[0] for r in db.execute('SELECT id FROM documents WHERE version_family_id=? ORDER BY id',(family,))],
            'current':db.execute('SELECT current_document_id FROM version_families WHERE id=?',(family,)).fetchone()[0],
            'relations':[r[0] for r in db.execute('SELECT id FROM version_relations WHERE family_id=? ORDER BY id',(family,))]}

    def undo(self,action_id):
        with self.library._connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM dedup_actions WHERE id=?',(action_id,)).fetchone()
            if not row:raise KeyError(action_id)
            if row['reverted_at']:raise DedupConflict('该操作已经撤销。')
            action=json.loads(row['payload']);family=action['family_id']
            if family:
                if self._group_state(db,family)!=action['group_state']:raise DedupConflict('版本组已发生后续变更，不能覆盖后续选择。')
                db.execute('UPDATE documents SET version_family_id=NULL WHERE version_family_id=?',(family,))
                # Keep the retired family and relation rows as historical audit.
                db.execute('UPDATE version_families SET current_document_id=NULL WHERE id=?',(family,))
            stamp=_now();db.execute('UPDATE dedup_actions SET reverted_at=? WHERE id=?',(stamp,action_id))
            return {**action,'reverted_at':stamp}
