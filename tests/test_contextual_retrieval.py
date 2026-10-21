"""Context inputs cannot borrow foreign evidence or change recalled targets."""
import hashlib

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from research_workspace.contextual_retrieval import source_part, make_input, target_spans, clip_part
from research_workspace.intelligence import Evidence


@pytest.fixture
def tokenizer():
    tokenizer = Tokenizer(models.WordLevel({'UNK': 0, 'query': 1, 'passage': 2, ':': 3, '[CLS]': 4, '[SEP]': 5}, unk_token='UNK'))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(single='[CLS] $A [SEP]', special_tokens=[('[CLS]', 4), ('[SEP]', 5)])
    return tokenizer


def block(name, text, **kwargs):
    return Evidence(evidence_id='E_'+name, block_id=name, document_id='doc', run_id='run', page=2,
                    document_title='Paper', filename='paper.pdf',
                    bbox=kwargs.pop('bbox', [.1, .3, .9, .4]), text=text, **kwargs)


def example():
    target = block('body', 'While the transformations are shared, the inner dimension is 2048.')
    title = block('title', 'Neural attention networks', bbox=[.2, .1, .8, .15])
    heading = block('heading', 'Position-wise feed-forward networks', bbox=[.1, .2, .9, .22])
    prior = block('prior', 'The network applies two transformations with a nonlinear activation between them.', bbox=[.1, .23, .9, .28])
    return target, title, heading, prior


def test_added_context_has_own_anchors_and_never_replaces_target(tokenizer):
    target, title, heading, prior = example()
    canonical = {b.block_id: b for b in [target, title, heading, prior]}
    before = {key: value.model_dump() for key, value in canonical.items()}
    value = make_input(target, target_spans(target.text, tokenizer)[0], canonical, tokenizer, 'model',
        variant='title_heading_previous384', title=[source_part(title, 'candidate')],
        heading=[source_part(heading, 'heading')], previous=source_part(prior, 'previous'))
    assert [p['role'] for p in value['parts']] == ['title_candidate', 'heading', 'previous', 'target']
    assert value['target']['block_id'] == 'body' and value['parts'][-1] == value['target']
    for part in value['parts']:
        b = canonical[part['block_id']]
        assert part['text'] == b.text[part['start']:part['end']]
        assert part['text_sha256'] == hashlib.sha256(part['text'].encode()).hexdigest()
        assert part['bbox'] == b.bbox and part['document_id'] == b.document_id and part['run_id'] == b.run_id
    assert value['text'] == '\n\n'.join(p['text'] for p in value['parts'])
    assert value['tokens'] == len(tokenizer.encode('passage: '+value['text']).ids) <= 512
    assert before == {key: value.model_dump() for key, value in canonical.items()}


@pytest.mark.parametrize('field,replacement', [('document_id', 'foreign'), ('run_id', 'foreign'), ('text', 'invented'),
                                             ('page', 9), ('bbox', [0, 0, 1, 1]), ('text_sha256', 'wrong')])
def test_every_additional_source_field_is_checked(tokenizer, field, replacement):
    target, title, _, _ = example()
    part = source_part(title, 'candidate')
    part[field] = replacement
    with pytest.raises(ValueError):
        make_input(target, target_spans(target.text, tokenizer)[0], {b.block_id: b for b in [target, title]}, tokenizer, 'model',
                   variant='title_heading384', title=[part])


@pytest.mark.parametrize('changes', [{'page': 1}, {'bbox': [.91, .2, .99, .25]}, {'bbox': [.1, .6, .9, .7]},
                                   {'bbox': [.1, .01, .9, .02]}, {'text': 'Short label'}])
def test_cross_page_column_future_distant_and_nonprose_context_rejected(tokenizer, changes):
    target, _, _, prior = example()
    prior = prior.model_copy(update=changes)
    with pytest.raises(ValueError, match='same-page'):
        make_input(target, target_spans(target.text, tokenizer)[0], {b.block_id: b for b in [target, prior]}, tokenizer, 'model',
                   variant='title_heading_previous384', previous=source_part(prior, 'previous'))


def test_target_source_and_reserved_limit_cannot_be_forged(tokenizer):
    target, _, _, _ = example()
    span = target_spans(target.text, tokenizer)[0]
    with pytest.raises(ValueError):
        make_input(target, dict(span, text='invented'), {'body': target}, tokenizer, 'model', variant='target384')
    with pytest.raises(ValueError):
        make_input(target.model_copy(update={'run_id': 'other'}), span, {'body': target}, tokenizer, 'model', variant='target384')
    huge = block('huge', 'word '*800)
    with pytest.raises(ValueError, match='reserved'):
        make_input(huge, {'start': 0, 'end': len(huge.text), 'text': huge.text}, {'huge': huge}, tokenizer, 'model', variant='target384')


def test_context_is_dropped_before_target_and_source_tail_remains(tokenizer):
    target = block('body', 'word '*900+'TAIL')
    title = block('title', 'title '*70, bbox=[.1, .1, .9, .13])
    heading = block('heading', 'heading '*70, bbox=[.1, .2, .9, .22])
    prior = block('prior', 'prior '*90, bbox=[.1, .23, .9, .28])
    canonical = {b.block_id: b for b in [target, title, heading, prior]}
    spans = target_spans(target.text, tokenizer)
    values = [make_input(target, span, canonical, tokenizer, 'model', variant='title_heading_previous384',
                         title=[source_part(title, 'title')], heading=[source_part(heading, 'heading')],
                         previous=source_part(prior, 'previous')) for span in spans]
    assert any(v['omitted_for_budget'] for v in values)
    covered = 0
    for value in values:
        part = value['target']
        assert part['start'] <= covered < part['end'] and part['text'] == target.text[part['start']:part['end']]
        assert value['tokens'] <= 512
        covered = part['end']
    assert covered == len(target.text) and values[-1]['target']['text'].endswith('TAIL')


def test_model_source_geometry_and_actual_context_change_cache_identity(tokenizer):
    target, title, _, _ = example()
    def plan(t, title_part, model):
        return make_input(t, target_spans(t.text, tokenizer)[0], {t.block_id: t, title.block_id: title}, tokenizer, model,
                          variant='title_heading384', title=[title_part])
    original = plan(target, source_part(title, 'title'), 'model')
    assert plan(target, source_part(title, 'title'), 'other-model')['cache_key'] != original['cache_key']
    assert plan(target.model_copy(update={'bbox': [.2, .3, .9, .4]}), source_part(title, 'title'), 'model')['cache_key'] != original['cache_key']
    shortened = source_part(title, 'title', 0, 6)
    assert plan(target, shortened, 'model')['cache_key'] != original['cache_key']


def test_clipped_context_keeps_exact_absolute_intervals(tokenizer):
    b = block('source', 'zero one two three four five six seven eight nine')
    part = source_part(b, 'title', 5, len(b.text))
    for tail in [False, True]:
        clipped = clip_part(part, tokenizer, 4, tail=tail)
        assert clipped['text'] == b.text[clipped['start']:clipped['end']]
        assert len(tokenizer.encode(clipped['text'], add_special_tokens=False).ids) <= 4
