"""Controlled layout counterexamples; not counted as real-paper gold groups."""
import copy

import pytest

from research_workspace.structure import extract_structure


def block(text, index, box=None):
    return dict(document_id='doc', run_id='old-run', page=1, block_id=f'b{index}',
                text=text, bbox=box or [.2, .2, .6, .25])


def extract(*blocks):
    original = copy.deepcopy(blocks)
    result = extract_structure(list(blocks), 'doc', 'old-run')['front_matter']
    assert original == blocks
    assert result['audit']['source_spans_valid']
    by_id = {b['block_id']: b for b in blocks}
    for org in result['affiliations']:
        assert org['text'] == ' '.join(p['text'] for p in org['evidence_parts'])
        assert org['evidence'] == org['evidence_parts'][0]
        for p in org['evidence_parts']:
            source = by_id[p['block_id']]
            assert source['text'][p['char_start']:p['char_end']] == p['text']
            assert all(p[k] == source[k] for k in ('document_id', 'run_id', 'page', 'bbox'))
    authors = {a['id']: a for a in result['authors']}
    orgs = {o['id']: o for o in result['affiliations']}
    for relation in result['relations']:
        assert relation['evidence'] == [authors[relation['author_id']]['evidence'],
                                        *orgs[relation['affiliation_id']]['evidence_parts']]
    return result


def test_cross_block_department_parent_has_separate_exact_spans_and_full_relation():
    result = extract(block('Alice Smith\nDepartment of Computer Science', 0, [.2,.2,.6,.25]),
                     block('University of A\na@example.org', 1, [.25,.255,.55,.29]), block('Abstract', 2))
    assert [o['text'] for o in result['affiliations']] == ['Department of Computer Science University of A']
    org = result['affiliations'][0]
    assert [p['block_id'] for p in org['evidence_parts']] == ['b0', 'b1']
    assert org['continuations'] == [{'method':'department_parent_institution', 'same_block':False}]
    assert len(result['relations']) == 1 and len(result['relations'][0]['evidence']) == 3


@pytest.mark.parametrize('parts', [
    ['Institute of', 'Technology'], ['University', 'of A'],
    ['Department of', 'Computer Science', 'University of A'],
])
def test_unfinished_words_and_hierarchy_in_one_block(parts):
    result = extract(block('Alice Smith\n'+'\n'.join(parts)+'\na@example.org', 0), block('Abstract', 1))
    assert [o['text'] for o in result['affiliations']] == [' '.join(parts)]
    assert [p['text'] for p in result['affiliations'][0]['evidence_parts']] == parts
    assert len(result['authors']) == len(result['relations']) == 1


@pytest.mark.parametrize('barrier', ['Bob Jones', 'a@example.org', 'Senior Fellow',
                                    '10 Crichton Street', 'Abstract', '1University of A', '¹University of A'])
def test_new_name_contact_role_address_boundary_or_marker_never_completes_department(barrier):
    result = extract(block('Alice Smith\nDepartment of\n'+barrier+'\nUniversity of B', 0), block('Abstract', 1))
    department = next(o for o in result['affiliations'] if o['text'].startswith('Department'))
    assert department['text'] == 'Department of'
    assert len(department['evidence_parts']) == 1


def test_separate_institutions_and_repeated_markers_remain_ambiguous():
    result = extract(block('Alice Smith¹\n¹Department of Computer Science\n¹University of A\na@example.org', 0),
                     block('Abstract', 1))
    assert len(result['affiliations']) == 2 and not result['relations']
    result = extract(block('Alice Smith\nUniversity of A\nand Google Inc.\na@example.org', 0), block('Abstract', 1))
    assert [o['text'] for o in result['affiliations']] == ['University of A', 'and Google Inc.']
    assert not result['relations']


@pytest.mark.parametrize('boxes', [
    ([.1,.2,.3,.23], [.6,.235,.8,.26]),  # another column
    ([.1,.2,.4,.23], [.1,.32,.4,.35]),   # large vertical gap
])
def test_cross_block_geometry_cannot_join_other_column_or_distant_institution(boxes):
    result = extract(block('Department of Computer Science', 0, boxes[0]),
                     block('University of A', 1, boxes[1]), block('Abstract', 2))
    assert len(result['affiliations']) == 2


def test_ambiguous_two_columns_below_department_are_not_arbitrarily_selected():
    result = extract(block('Department of Computer Science', 0, [.1,.2,.9,.23]),
                     block('University of A', 1, [.1,.235,.4,.26]),
                     block('University of B', 2, [.6,.235,.9,.26]), block('Abstract', 3))
    assert len(result['affiliations']) == 3


def test_inline_contact_ends_chain_but_preserves_printed_institution_and_marker():
    result = extract(block('Alice Smith¹', 0),
                     block('¹University of A, {alice,bob}@example.org\nInstitute of B', 1), block('Abstract', 2))
    assert [o['text'] for o in result['affiliations']] == ['University of A', 'Institute of B']
    assert result['authors'][0]['affiliation_ids'] == ['affiliation-1']
    assert result['relations'][0]['method'] == 'explicit_affiliation_marker'


@pytest.mark.parametrize('label', ['作者：张三¹、李四²', 'Authors: 张三¹, 李四²', '作者\n张三¹、李四²'])
def test_explicit_labeled_chinese_authors_have_exact_offsets_and_marked_relations(label):
    result = extract(block(label+'\n¹云南大学\n²北京大学', 0), block('摘要', 1))
    assert [a['name'] for a in result['authors']] == ['张三', '李四']
    assert [a['affiliation_ids'] for a in result['authors']] == [['affiliation-1'], ['affiliation-2']]


def test_bare_chinese_title_and_email_domain_do_not_create_authorship_or_institution():
    result = extract(block('研究方法\n张三\na@university.example', 0), block('摘要', 1))
    assert not result['authors'] and not result['affiliations']


def test_and_ampersand_author_separators_preserve_all_names_and_native_diacritics():
    result = extract(block('Fran¸cois Chollet and A¨aron van den Oord & Alice Smith\nGoogle, Inc.\na@example.org', 0),
                     block('Abstract', 1))
    assert [a['name'] for a in result['authors']] == ['Fran¸cois Chollet', 'A¨aron van den Oord', 'Alice Smith']
    assert len(result['relations']) == 3


def test_symbol_marker_is_not_discarded_when_another_symbol_is_missing():
    result = extract(block('Alice Smith⋄\nBob Jones⋄†', 0), block('⋄IBM Watson', 1), block('Abstract', 2))
    assert result['authors'][0]['affiliation_ids'] == ['affiliation-1']
    assert not result['authors'][1]['affiliation_ids']


def test_acceptance_rejects_cross_entity_joins_and_protected_source_text():
    from scripts.affiliation_acceptance import unsafe_join
    def piece(start, end):
        return dict(block_id='source', char_start=start, char_end=end, text='')
    a, b, address = piece(0, 10), piece(20, 30), piece(40, 50)
    case = dict(expected_affiliations=[dict(evidence_parts=[a]), dict(evidence_parts=[b])])
    assert not unsafe_join(case, dict(evidence_parts=[a]))
    assert unsafe_join(case, dict(evidence_parts=[a, b]))
    assert unsafe_join(case, dict(evidence_parts=[a, address]))
    # A partial source piece is incomplete, but is not a false concatenation.
    assert not unsafe_join(case, dict(evidence_parts=[piece(0, 5)]))
