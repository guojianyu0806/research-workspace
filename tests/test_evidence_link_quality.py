"""Bare link footnotes should not outrank explanations; link lookup still works."""
import pytest

from research_workspace.intelligence import Evidence, IntelligenceService
from research_workspace.reader_search import ReaderSearch

LINKS = '2https://github.com/pytorch/fairseq\n3https://github.com/huggingface/transformers'


def block(identifier, text):
    return Evidence(evidence_id='E_'+identifier, document_id='doc', document_title='Paper', filename='paper.pdf',
                    run_id='run', block_id=identifier, page=2, text=text, bbox=[.1, .2, .8, .3])


@pytest.mark.parametrize('question', ['How many identical layers make up the Transformer encoder?',
                                    'Transformer编码器由多少个相同层组成？',
                                    'What is the parameter value in the experiment?'])
def test_link_only_footnote_cannot_masquerade_as_mechanism_or_numeric_evidence(question):
    link = block('links', LINKS)
    before = link.model_dump()
    body = block('body', 'The encoder is composed of a stack of six identical layers.')
    assert IntelligenceService._evidence_quality(link, question) < IntelligenceService._evidence_quality(body, question)/2
    assert link.model_dump() == before


@pytest.mark.parametrize('question', ['Where is the implementation repository?', 'Where can I find the code?',
                                    '这篇论文的代码仓库在哪里？', '请提供源码链接',
                                    'Wo finde ich den Quellcode?', 'Où trouver le lien du code ?',
                                    '¿Dónde está el código?', 'コードのリポジトリはどこですか？',
                                    '코드 저장소는 어디에 있습니까?', 'أين رابط الشفرة؟'])
def test_explicit_code_and_link_questions_keep_url_evidence(question):
    assert IntelligenceService._evidence_quality(block('links', LINKS), question) == 1


@pytest.mark.parametrize('text', [
    'The Transformer encoder consists of 6 layers. https://example.org/details',
    '40.4 https://example.org/results',
    'Accuracy = 94.8 https://example.org/measurements',
])
def test_prose_and_decimal_results_with_links_are_not_classified_as_bare_urls(text):
    assert IntelligenceService._evidence_quality(block('measurement', text), 'What is the accuracy value?') == 1


def test_reader_prefers_body_and_preserves_link_lookup_originals_and_audit():
    blocks = [block('links', LINKS), block('body', 'The encoder is composed of a stack of six identical layers.')]
    originals = {b.block_id: b.model_dump(exclude={'score'}) for b in blocks}

    class Dense:
        async def rank(self, queries, sources, **kwargs):
            return [b.model_copy(update={'score': .99 if b.block_id == 'links' else .8}) for b in sources], {'status': 'applied'}

    reader = ReaderSearch(None, semantic=Dense())
    reader.snapshot = lambda *args: (blocks, [{'document_id': 'doc', 'run_id': 'run', 'parse_status': 'completed'}], 'ready')
    result = reader.search('Transformer编码器由多少层组成？', mode='semantic')
    assert result['hits'][0]['block_id'] == 'body' and result['complete']
    assert result['model_calls'] == 0 and not result['network_used']
    links = next(h for h in result['hits'] if h['block_id'] == 'links')
    assert links['source_quality_weight'] < .5
    for h in result['hits']:
        assert all(h[k] == value for k, value in originals[h['block_id']].items())
    explicit = reader.search('Where is the code repository?', mode='semantic')
    assert explicit['hits'][0]['block_id'] == 'links' and explicit['hits'][0]['source_quality_weight'] == 1
    assert explicit['search_id'] != result['search_id']
