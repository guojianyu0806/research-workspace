"""Deterministic terminology replacement preserves immutable source and digits."""
import asyncio,re
import pytest

from research_workspace.intelligence import IntelligenceService,TranslationPayload,protect_text,restore_text,validate_glossary
from research_workspace.library import Library,LibraryError
from research_workspace.jobs import JobStore


class Provider:
    configured=True
    async def generate(self,prompt,payload,schema):
        return TranslationPayload(translated_text='统一译文 '+payload['text'])


def test_terms_use_longest_whole_word_and_do_not_edit_code_or_formula():
    text='n-grams and N-gram are terms. `n-gram` and $n-gram$ remain code. 5-gram = 5-gram. RAG + x'
    glossary={'n-gram':'n元语法','n-grams':'n元语法','5-gram':'5-gram','RAG':'检索增强生成'}
    protected,mapping=protect_text(text,glossary=glossary)
    assert restore_text(protected,mapping)==text
    translated=restore_text(protected,mapping,glossary=validate_glossary(glossary))
    assert translated.startswith('n元语法 and n元语法')
    assert '`n-gram`' in translated and '$n-gram$' in translated and '5-gram = 5-gram' in translated and 'RAG + x' in translated


@pytest.mark.parametrize('glossary',[{'number':'42'},{'x=2':'公式'},{'term':'bad\ncontrol'},{'term':'⟪K012345abcdef_0⟫'},{'Term':'甲','term':'乙'}])
def test_glossary_cannot_invent_numeric_values_or_modify_protected_protocol(glossary):
    with pytest.raises(LibraryError):validate_glossary(glossary)


def test_direct_and_queued_text_keep_original_and_same_translated_term(tmp_path):
    source='perplexity n-gram 5-gram';glossary={'Perplexity':'困惑度','n-gram':'n-gram','5-gram':'5-gram'}
    library=Library(tmp_path);service=IntelligenceService(library,Provider())
    direct=asyncio.run(service.translate_text(source,target_language='zh',glossary=glossary))
    assert direct['source_text']==source and direct['segments'][0]['source_text']==source
    assert '困惑度 n-gram 5-gram' in direct['translated_text']
    store=JobStore(library);job=store.create('translate_text',{'text':source,'target_language':'zh','glossary':glossary})
    asyncio.run(store.process_next(service));saved=store.get(job['id'])
    assert saved['status']=='completed' and saved['result']['source_text']==source
    assert saved['result']['segments'][0]['source_text']==source
    assert saved['result']['translated_text']==direct['translated_text']


def test_document_task_keeps_glossary_and_original_block_identity(tmp_path):
    import pymupdf
    library=Library(tmp_path)
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((30,70),'perplexity n-gram')
        doc=library.import_document(pdf.tobytes(),'terms.pdf')['document']
    library.process_next_job();store=JobStore(library)
    job=store.create('translate',{'document_id':doc['id'],'glossary':{'perplexity':'困惑度','n-gram':'n-gram'}})
    asyncio.run(store.process_next(IntelligenceService(library,Provider())))
    result=store.get(job['id'])['result'];block=result['blocks'][0]
    assert block['source_text']=='perplexity n-gram' and '困惑度 n-gram' in block['translated_text']
    assert result['glossary']=={'perplexity':'困惑度','n-gram':'n-gram'}


def test_translation_api_freezes_valid_glossary_and_rejects_invalid_before_queue(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from research_workspace.api import create_app
    from research_workspace.advanced_api import task_markdown
    monkeypatch.setenv('RESEARCH_MODEL_BASE_URL','https://model.invalid')
    monkeypatch.setenv('RESEARCH_MODEL','fixture-model');monkeypatch.setenv('RESEARCH_MODEL_API_KEY','fixture-not-real')
    app=create_app(tmp_path,start_worker=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        body={'kind':'translate_text','text':'perplexity n-gram','target_language':'zh','glossary':{'Perplexity':'困惑度'}}
        response=client.post('/api/research/tasks',json=body)
        assert response.status_code==202
        assert response.json()['job']['payload']['glossary']=={'perplexity':'困惑度'}
        assert '困惑度' in task_markdown(response.json()['job'])
        assert client.post('/api/research/tasks',json={**body,'glossary':{'term':'42'}}).status_code==400
        assert client.post('/api/research/tasks',json={**body,'kind':'qa'}).status_code==400
        assert len(client.get('/api/research/tasks').json()['jobs'])==1
