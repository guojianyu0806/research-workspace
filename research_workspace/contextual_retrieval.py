"""Experimental pure source-bound encoder input planning; no cache or network.

Additional context never changes the target text, identity or recalled unit.
Every literal source fragment is bound to a canonical document/run/page/block.
"""
import hashlib
import json

from .e5_model import chunks, DOCUMENT_PREFIX

POLICY = 'source-bound-context-candidate-v1'
VARIANTS = ('target384', 'title_heading384', 'title_heading_previous384')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def source_part(block, role, start=0, end=None):
    end = len(block.text) if end is None else end
    if (any(isinstance(v, bool) or not isinstance(v, int) for v in [start, end])
            or not 0 <= start < end <= len(block.text)):
        raise ValueError('Invalid exact source interval')
    text = block.text[start:end]
    return {'role': role, 'document_id': block.document_id, 'run_id': block.run_id, 'block_id': block.block_id,
            'page': block.page, 'bbox': list(block.bbox), 'start': start, 'end': end,
            'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest()}


def validate_part(part, canonical, target):
    block = canonical.get(part['block_id'])
    if (block is None or block.document_id != target.document_id or block.run_id != target.run_id
            or part != source_part(block, part['role'], part['start'], part['end'])):
        raise ValueError('Context does not resolve to the same canonical source')
    return part


def clip_part(part, tokenizer, limit, *, tail=False):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('Invalid context token budget')
    if len(tokenizer.encode(part['text'], add_special_tokens=False).ids) <= limit:
        return dict(part)
    observed = tokenizer.encode(part['text'], add_special_tokens=False)
    selected = observed.offsets[-limit:] if tail else observed.offsets[:limit]
    if tail:
        start, end = min(a for a, b in selected if b > a), len(part['text'])
    else:
        start, end = 0, max(b for a, b in selected if b > a)
    while len(tokenizer.encode(part['text'][start:end], add_special_tokens=False).ids) > limit:
        if tail:
            start += 1
        else:
            end -= 1
        if start >= end:
            raise ValueError('Context character cannot fit its budget')
    text = part['text'][start:end]
    return dict(part, start=part['start']+start, end=part['start']+end, text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest())


def make_input(target, span, canonical, tokenizer, model_fingerprint, *, variant, title=(), heading=(), previous=None):
    if variant not in VARIANTS:
        raise ValueError('Unknown context variant')
    canonical_target = canonical.get(target.block_id)
    if canonical_target != target:
        raise ValueError('Target does not belong to the canonical source snapshot')
    target_part = source_part(target, 'target', span['start'], span['end'])
    if target_part['text'] != span['text']:
        raise ValueError('Target span differs from original')
    if len(tokenizer.encode(DOCUMENT_PREFIX+target_part['text']).ids) > 384:
        raise ValueError('Target exceeds its reserved token budget')
    contexts = []
    if variant != 'target384':
        for role, parts, limit in [('title_candidate', title, 48), ('heading', heading, 48)]:
            remaining = limit
            for original in parts:
                validate_part(original, canonical, target)
                if remaining <= 0:
                    break
                part = clip_part(dict(original, role=role), tokenizer, remaining)
                used = len(tokenizer.encode(part['text'], add_special_tokens=False).ids)
                remaining -= used
                if part['block_id'] != target.block_id or not (part['start'] >= target_part['start'] and part['end'] <= target_part['end']):
                    contexts.append(part)
        if variant == 'title_heading_previous384' and previous is not None:
            validate_part(previous, canonical, target)
            a, b = previous['bbox'], target.bbox
            width = min(a[2]-a[0], b[2]-b[0])
            overlap = max(0, min(a[2], b[2])-max(a[0], b[0]))
            from .intelligence import IntelligenceService
            if (previous['page'] != target.page or previous['block_id'] == target.block_id
                    or not -.01 <= b[1]-a[3] <= .20 or a[1] >= b[1] or width <= 0 or overlap/width < .5
                    or len(IntelligenceService._search_terms(previous['text'])) < 8):
                raise ValueError('Prior context fails same-page prose and column geometry')
            contexts.append(clip_part(dict(previous, role='previous'), tokenizer, 64, tail=True))
    unique = []
    for part in contexts:
        if not any((p['block_id'], p['start'], p['end']) == (part['block_id'], part['start'], part['end']) for p in unique):
            unique.append(part)
    contexts, omitted = unique, []
    while True:
        parts = contexts+[target_part]
        text = '\n\n'.join(p['text'] for p in parts)
        tokens = len(tokenizer.encode(DOCUMENT_PREFIX+text).ids)
        if tokens <= 512:
            break
        if not contexts:
            raise ValueError('Target exceeds the whole model budget')
        index = next((i for i, p in enumerate(contexts) if p['role'] == 'previous'),
                     next((i for i, p in enumerate(contexts) if p['role'] == 'heading'), 0))
        omitted.append(contexts.pop(index))
    value = {'policy': POLICY, 'variant': variant, 'model_fingerprint': model_fingerprint,
             'target': target_part, 'parts': parts, 'text': text, 'tokens': tokens,
             'input_sha256': hashlib.sha256(text.encode()).hexdigest(), 'omitted_for_budget': omitted}
    # Include geometry as well as all textual inputs; source movement must not
    # make a previously encoded anchor masquerade as a current one.
    value['cache_key'] = digest({k: value[k] for k in ['policy', 'variant', 'model_fingerprint', 'target', 'parts', 'input_sha256']})
    return value


def target_spans(text, tokenizer):
    return list(chunks(text, tokenizer, max_tokens=384, overlap_tokens=64))
