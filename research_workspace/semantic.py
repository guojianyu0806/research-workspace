"""Optional offline dense retrieval; every returned hit remains a canonical block."""
from __future__ import annotations
import asyncio
import hashlib
import json
import importlib.util
import math
import os
from pathlib import Path
import sqlite3
import threading

ROOT=Path(__file__).resolve().parents[1]
MODEL='BAAI/bge-small-en-v1.5'
MANIFEST=ROOT/'data/models/semantic-model.json'
_models={}
_model_lock=threading.Lock()


def model_status():
    if os.getenv('RESEARCH_SEMANTIC_ENABLED','0').lower() not in {'1','true','auto'}:
        return {'status':'disabled','model':MODEL}
    if not MANIFEST.is_file():
        return {'status':'model_not_prepared','model':MODEL}
    if importlib.util.find_spec('fastembed') is None or importlib.util.find_spec('numpy') is None:
        return {'status':'dependency_missing','model':MODEL}
    try:
        data=json.loads(MANIFEST.read_text(encoding='utf-8'))
        if data['model']!=MODEL or data['dimensions']!=384 or len(data['fingerprint'])!=64:
            raise ValueError('Invalid model manifest')
        return {'status':'ready','model':MODEL,'fingerprint':data['fingerprint'],'dimensions':384}
    except (OSError,ValueError,KeyError,TypeError):
        return {'status':'invalid_model_manifest','model':MODEL}


def _load_model():
    # This runtime never downloads a model or reads the user's Hugging Face token.
    with _model_lock:
        data=json.loads(MANIFEST.read_text(encoding='utf-8'))
        fingerprint=data['fingerprint']
        if fingerprint in _models:return _models[fingerprint]
        directory=(ROOT/data['model_directory']).resolve()
        if not directory.is_relative_to((ROOT/'data/models').resolve()):
            raise ValueError('Model must stay inside project data/models')
        for item in data['files']:
            path=(directory/item['path']).resolve()
            if not path.is_relative_to((ROOT/'data/models').resolve()) or not path.is_file():
                raise ValueError('Invalid model file')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise ValueError('Model file checksum mismatch')
        from fastembed import TextEmbedding
        model=TextEmbedding(model_name=MODEL,specific_model_path=str(directory),
            cache_dir=str(ROOT/'data/models'),local_files_only=True,
            threads=2,providers=['CPUExecutionProvider'])
        _models[fingerprint]=(model,threading.Lock())
        return _models[fingerprint]


def _encode(texts,query=False):
    model,lock=_load_model()
    with lock:
        return list(model.query_embed(texts) if query else model.passage_embed(texts,batch_size=32))


def document_similarities(library,signatures):
    """Advisory version candidates only, never an automatic identity decision."""
    status=model_status()
    if status['status']!='ready' or len(signatures)<2:return {}
    signatures=signatures[:101]
    import numpy as np
    vectors={};missing=[]
    with sqlite3.connect(library.data_dir/'semantic.sqlite3',timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, vector BLOB NOT NULL)')
        for item in signatures:
            doc=item['document'];run=(doc.get('available_run') or doc.get('current_run') or {}).get('id')
            text=item.get('body_preview','')[:1600]
            if not text:continue
            cache_key=hashlib.sha256(json.dumps(['version-preview',status['fingerprint'],doc['id'],run,
                item['body_sha256'],text],ensure_ascii=False).encode()).hexdigest()
            row=db.execute('SELECT vector FROM vectors WHERE cache_key=?',(cache_key,)).fetchone()
            try:
                if not row:raise ValueError('Not cached')
                vectors[doc['id']]=SemanticIndex._vector(np.frombuffer(row[0],dtype='<f4'),384)
            except ValueError:missing.append((cache_key,doc['id'],text))
        for start in range(0,len(missing),32):
            batch=missing[start:start+32];values=_encode([t for _,_,t in batch])
            if len(values)!=len(batch):raise ValueError('Embedding count mismatch')
            for (cache_key,doc_id,_),value in zip(batch,values):
                vector=SemanticIndex._vector(value,384);vectors[doc_id]=vector
                db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)',(cache_key,vector.astype('<f4').tobytes()))
    source=vectors.get(signatures[0]['document']['id'])
    return {} if source is None else {doc_id:float(source@v) for doc_id,v in vectors.items()}


class SemanticIndex:
    def __init__(self,library,*,encoder=None,fingerprint=None,dimensions=384):
        self.library=library
        self.encoder=encoder or _encode
        self.injected_fingerprint=fingerprint
        self.dimensions=dimensions

    def status(self):
        if self.injected_fingerprint:
            return {'status':'ready','model':'test_encoder','fingerprint':self.injected_fingerprint,'dimensions':self.dimensions}
        return model_status()

    @staticmethod
    def _vector(value,dimensions):
        import numpy as np
        vector=np.asarray(value,dtype='<f4')
        if vector.shape!=(dimensions,) or not np.isfinite(vector).all():
            raise ValueError('Invalid embedding vector')
        norm=float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm<=0:raise ValueError('Zero embedding vector')
        return vector/norm

    async def rank(self,queries,blocks,*,limit=48,check_cancel=None):
        status=self.status()
        if status['status']!='ready' or not blocks:return [],status
        if len(blocks)>12000:return [],{**status,'status':'source_budget_exceeded','max_source_blocks':12000}
        import numpy as np
        from .intelligence import IntelligenceService
        dimension=status['dimensions'];entries=[]
        for block in blocks:
            for offset in range(0,min(len(block.text),5800),1400):
                text=block.text[offset:min(offset+1600,5800)]
                cache_key=hashlib.sha256(json.dumps([status['fingerprint'],block.document_id,block.run_id,
                    block.block_id,offset,text],ensure_ascii=False).encode()).hexdigest()
                entries.append((cache_key,block,text))
        if len(entries)>24000:return [],{**status,'status':'chunk_budget_exceeded','max_chunks':24000}
        path=self.library.data_dir/'semantic.sqlite3'
        with sqlite3.connect(path,timeout=30) as db:
            db.execute('CREATE TABLE IF NOT EXISTS vectors(cache_key TEXT PRIMARY KEY, vector BLOB NOT NULL)')
            vectors={};pending=[];hits=0
            for cache_key,block,text in entries:
                row=db.execute('SELECT vector FROM vectors WHERE cache_key=?',(cache_key,)).fetchone()
                try:
                    if not row:raise ValueError('Not cached')
                    vectors[cache_key]=self._vector(np.frombuffer(row[0],dtype='<f4'),dimension);hits+=1
                except ValueError:pending.append((cache_key,text))
            for start in range(0,len(pending),32):
                await IntelligenceService._check_cancel(check_cancel)
                batch=pending[start:start+32]
                values=await asyncio.to_thread(self.encoder,[text for _,text in batch],False)
                if len(values)!=len(batch):raise ValueError('Embedding batch count mismatch')
                for (cache_key,text),value in zip(batch,values):
                    vector=self._vector(value,dimension);vectors[cache_key]=vector
                    db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?)',(cache_key,vector.astype('<f4').tobytes()))
                db.commit()
        await IntelligenceService._check_cancel(check_cancel)
        values=await asyncio.to_thread(self.encoder,queries[:13],True)
        if len(values)!=len(queries[:13]) or not values:raise ValueError('Query vector count mismatch')
        query_vectors=np.stack([self._vector(v,dimension) for v in values])
        matrix=np.stack([vectors[key] for key,_,_ in entries])
        scores=(matrix@query_vectors.T).max(axis=1)
        ranked={}
        for (_,block,_),score in zip(entries,scores):
            old=ranked.get(block.block_id)
            if old is None or score>old.score:ranked[block.block_id]=block.model_copy(update={'score':float(score)})
        ordered=sorted(ranked.values(),key=lambda b:(-b.score,b.document_id,b.page,b.block_id))
        return ordered[:limit],{**status,'status':'applied','network_used':False,'cache_hits':hits,
            'new_chunks':len(pending),'source_blocks':len(blocks),'chunks':len(entries),
            'query_count':len(values),'truncated_blocks':sum(len(b.text)>5800 for b in blocks),
            'max_source_chars_per_block':5800,'language_policy':'English corpus; bilingual expansion supplies English queries',
            'selection_policy':'RRF ranking signal only; facts must still quote original canonical evidence'}
