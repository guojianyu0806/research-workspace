import asyncio
from pathlib import Path
import pytest
from research_workspace.intelligence import Evidence,IntelligenceService
from research_workspace.jobs import TaskCancelled
from research_workspace.library import Library
from research_workspace.semantic import SemanticIndex


def block(name,text,run='run'):
    return Evidence(evidence_id='E_'+name,document_id='doc',run_id=run,block_id=name,
        document_title='Actual document',filename='paper.pdf',page=1,bbox=[.1,.2,.8,.3],text=text)


class Encoder:
    def __init__(self):self.calls=[]
    def __call__(self,texts,query):
        self.calls.append((texts,query))
        return [[1.,0.] if any(w in t.lower() for w in ['dog','canine']) else [0.,1.] for t in texts]


def test_semantic_cache_is_version_text_model_and_workspace_bound(tmp_path):
    lib=Library(tmp_path/'one');encoder=Encoder();index=SemanticIndex(lib,encoder=encoder,fingerprint='model-v1',dimensions=2)
    blocks=[block('a','A canine rests in the garden.'),block('b','A table describes energy use.')]
    ranking,first=asyncio.run(index.rank(['dog'],blocks))
    assert ranking[0].block_id=='a' and ranking[0].text==blocks[0].text
    assert first['new_chunks']==2 and first['cache_hits']==0
    _,second=asyncio.run(index.rank(['dog'],blocks))
    assert second['new_chunks']==0 and second['cache_hits']==2
    _,third=asyncio.run(index.rank(['dog'],[blocks[0].model_copy(update={'run_id':'new-run'})]))
    assert third['cache_hits']==0
    _,fourth=asyncio.run(index.rank(['dog'],[blocks[0].model_copy(update={'text':'Changed original text.'})]))
    assert fourth['cache_hits']==0
    other=SemanticIndex(Library(tmp_path/'two'),encoder=encoder,fingerprint='model-v1',dimensions=2)
    _,fifth=asyncio.run(other.rank(['dog'],blocks));assert fifth['cache_hits']==0
    changed=SemanticIndex(lib,encoder=encoder,fingerprint='model-v2',dimensions=2)
    _,sixth=asyncio.run(changed.rank(['dog'],blocks));assert sixth['cache_hits']==0


def test_repeated_text_shares_computation_but_preserves_every_source_and_workspace(tmp_path):
    encoder=Encoder();lib=Library(tmp_path/'alice')
    texts=['canine '+('x'*((i%9+1)*60)) for i in range(90)]+['canine','CANINE',' canine']
    blocks=[block(str(i),text) for i,text in enumerate(texts)]
    index=SemanticIndex(lib,encoder=encoder,fingerprint='bounded',dimensions=2)
    ranked,meta=asyncio.run(index.rank(['dog'],blocks,limit=100))
    encoded=[text for batch,query in encoder.calls if not query for text in batch]
    assert set(encoded)==set(texts) and len(encoded)==len(set(texts))
    assert [len(text) for text in encoded]==sorted(len(text) for text in encoded)
    assert meta['new_chunks']==len(texts) and meta['encoded_texts']==len(set(texts))
    assert meta['reused_identical_chunks']==len(texts)-len(set(texts))
    assert {item.block_id for item in ranked}=={b.block_id for b in blocks}
    assert all(item.text==texts[int(item.block_id)] for item in ranked)
    import sqlite3
    with sqlite3.connect(lib.data_dir/'semantic.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]==len(texts)
    encoder.calls.clear();_,again=asyncio.run(index.rank(['dog'],blocks))
    assert again['cache_hits']==len(texts) and again['encoded_texts']==0
    assert all(query for _,query in encoder.calls)
    encoder.calls.clear()
    other=SemanticIndex(Library(tmp_path/'bob'),encoder=encoder,fingerprint='bounded',dimensions=2)
    _,fresh=asyncio.run(other.rank(['dog'],blocks))
    assert fresh['cache_hits']==0 and fresh['encoded_texts']==len(set(texts))


def test_cancel_after_an_encoding_batch_keeps_only_real_completed_source_records(tmp_path):
    encoder=Encoder();lib=Library(tmp_path)
    index=SemanticIndex(lib,encoder=encoder,fingerprint='cancel',dimensions=2)
    blocks=[block(str(i),'canine '+str(i)) for i in range(70)]
    def cancel():
        if encoder.calls:raise TaskCancelled()
    with pytest.raises(TaskCancelled):asyncio.run(index.rank(['dog'],blocks,check_cancel=cancel))
    import sqlite3
    with sqlite3.connect(lib.data_dir/'semantic.sqlite3') as db:assert db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]==32
    assert len(encoder.calls)==1 and not encoder.calls[0][1]


@pytest.mark.parametrize('vector',[[float('nan'),0],[0,0],[1,2,3]])
def test_invalid_vectors_are_never_cached_or_used(tmp_path,vector):
    index=SemanticIndex(Library(tmp_path),encoder=lambda texts,query:[vector]*len(texts),fingerprint='invalid',dimensions=2)
    with pytest.raises(ValueError):asyncio.run(index.rank(['dog'],[block('a','A canine.')]))


def test_cancel_is_not_swallowed_or_converted_to_lexical_fallback(tmp_path):
    encoder=Encoder();index=SemanticIndex(Library(tmp_path),encoder=encoder,fingerprint='model',dimensions=2)
    def cancel():raise TaskCancelled()
    with pytest.raises(TaskCancelled):asyncio.run(index.rank(['dog'],[block('a','A canine.')],check_cancel=cancel))
    assert encoder.calls==[]


def test_chunks_do_not_silently_claim_full_coverage_of_an_unbounded_block(tmp_path):
    encoder=Encoder();index=SemanticIndex(Library(tmp_path),encoder=encoder,fingerprint='model',dimensions=2)
    _,meta=asyncio.run(index.rank(['dog'],[block('a','canine '+('x'*10000))]))
    assert meta['truncated_blocks']==1 and meta['max_source_chars_per_block']==5800
    assert max(len(t) for texts,query in encoder.calls if not query for t in texts)<=1600


def test_dense_fusion_uses_only_snapshot_originals_not_backend_supplied_text(tmp_path,monkeypatch):
    actual=block('a','The actual canonical original passage.')
    class Dense:
        def status(self):return {'status':'ready'}
        async def rank(self,queries,blocks,**kwargs):
            assert blocks==[actual]
            return [actual.model_copy(update={'text':'Invented backend answer','score':.9}),block('foreign','Foreign paper')],{'status':'applied'}
    class Provider:configured=False
    service=IntelligenceService(Library(tmp_path),Provider(),semantic=Dense())
    monkeypatch.setattr(service,'_blocks',lambda ids:[actual])
    result=asyncio.run(service.retrieve_for_question('zebra',['doc'],expansion='off'))
    assert result['mode']=='bm25_dense_rrf'
    assert [b.text for b in result['evidence']]==[actual.text]
    assert result['selected_runs']=={'doc':'run'}


def test_dense_version_candidates_require_actual_body_overlap_and_never_merge(tmp_path,monkeypatch):
    import pymupdf
    from research_workspace import semantic
    lib=Library(tmp_path)
    common=' '.join('word'+str(i) for i in range(90))
    def add(text,name):
        with pymupdf.open() as pdf:
            p=pdf.new_page();p.insert_textbox(p.rect,text,fontsize=10)
            doc=lib.import_document(pdf.tobytes(),name)['document']
        while lib.process_next_job():pass
        return doc['id']
    first=add(common+' original conclusion','one.pdf')
    related=add(common.replace('word40','different40').replace('word50','different50')+' revised analysis','two.pdf')
    unrelated=add(' '.join('other'+str(i) for i in range(100)),'three.pdf')
    monkeypatch.setattr(semantic,'document_similarities',lambda library,signatures:{s['document']['id']:.99 for s in signatures})
    candidates=lib.find_version_candidates(first)
    assert related in [c['document']['id'] for c in candidates]
    assert unrelated not in [c['document']['id'] for c in candidates]
    assert all(c['requires_confirmation'] for c in candidates)
    assert all(lib.get_document(i)['version_family_id'] is None for i in [first,related,unrelated])


@pytest.mark.parametrize('opening',['A canine receives veterinary treatment. ','word based only on its context. '])
def test_dense_best_passage_survives_lexical_keyword_distractors(tmp_path,monkeypatch,opening):
    target=block('target',opening*8)
    noise=[block('noise'+str(i),'network context algorithm '+str(i)+' other terminology. '*10) for i in range(5)]
    class Dense:
        def status(self):return {'status':'ready'}
        async def rank(self,queries,blocks,**kwargs):
            return [target.model_copy(update={'score':.9})]+[b.model_copy(update={'score':.8}) for b in noise],{'status':'applied'}
    class Provider:configured=False
    service=IntelligenceService(Library(tmp_path),Provider(),semantic=Dense())
    monkeypatch.setattr(service,'_blocks',lambda ids:[target,*noise])
    result=asyncio.run(service.retrieve_for_question('network context',['doc'],limit=2,expansion='off'))
    assert target.block_id in [b.block_id for b in result['evidence']]
    assert result['semantic']['reserved_block_ids']==[target.block_id]
