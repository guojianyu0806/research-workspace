import hashlib
import json
from pathlib import Path

from scripts.freeze_multilingual_queries import LANGUAGES, normalized

MANIFEST = Path(__file__).parent / 'fixtures/multilingual/20261020-v1/manifest.json'


def test_full_query_denominators_and_unique_ids():
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    assert len(data['queries']) == len({q['id'] for q in data['queries']}) == 160
    assert len(data['facts']) == len({f['id'] for f in data['facts']}) == 20
    for language in LANGUAGES:
        queries = [q for q in data['queries'] if q['language'] == language]
        assert len(queries) == len({q['query'] for q in queries}) == 20
        assert sum(q['split'] == 'held_out_source' for q in queries) == 8
        assert all(0 < len(q['query']) <= 200 for q in queries)


def test_source_document_split_has_no_version_leakage():
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    split_by_source = {}
    facts = {f['id']: f for f in data['facts']}
    for fact in facts.values():
        split_by_source.setdefault(fact['source'], set()).add(fact['split'])
    assert all(len(splits) == 1 for splits in split_by_source.values())
    assert split_by_source['1810.04805v2.pdf'] == {'held_out_source'}
    assert len(split_by_source) == 3
    for query in data['queries']:
        assert facts[query['fact_id']]['split'] == query['split']


def test_source_anchors_and_geometry_are_self_consistent():
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for fact in data['facts']:
        evidence = fact['evidence']
        assert fact['source'] in data['sources']
        assert evidence['physical_page'] >= 1
        assert hashlib.sha256(evidence['native_text'].encode()).hexdigest() == evidence['native_text_sha256']
        assert normalized(evidence['anchor']) in normalized(evidence['native_text'])
        x0, y0, x1, y1 = evidence['bbox']
        assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert 'No independent human/native-language validation claimed' in data['annotation']
