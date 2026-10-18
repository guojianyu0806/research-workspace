"""Bounded, case-sensitive text revision diff with immutable source spans.

Whitespace and block boundaries do not define a revision. Images, equations
missing from the saved text and semantic equivalence are never inferred.
"""
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
import re

VERSION='source-token-diff-v1'
MAX_CHARS=2_000_000
MAX_TOKENS=60_000
MAX_MATCH_PAIRS=2_000_000
MAX_CHANGES=200
MAX_REGIONS=2000
MAX_OUTPUT_CHARS=600_000
TOKEN=re.compile(r'\w+|[^\w\s]',re.UNICODE)


def _tokens(blocks):
    out=[]
    for index,block in enumerate(blocks):
        for match in TOKEN.finditer(block['text']):
            out.append((match.group(),index,match.start(),match.end()))
            if len(out)>MAX_TOKENS:return out
    return out


def _regions(blocks,tokens):
    spans={}
    for _,index,start,end in tokens:
        if index in spans:spans[index][1]=end
        else:spans[index]=[start,end]
    return [{**blocks[i],'change_span':{'start':start,'end':end,'text':blocks[i]['text'][start:end]},
        'location_precision':'saved_text_block_with_exact_character_span'} for i,(start,end) in spans.items()]


def compare_text_snapshots(before,after):
    """Compare two already frozen block lists; never load a model or network."""
    counts=[sum(len(b['text']) for b in blocks) for blocks in (before,after)]
    result={'method':VERSION,'status':'compared','complete':False,'changes':[],
        'text_equivalent':None,'changes_truncated':False,'total_changes':None,
        'normalization':'ignore_whitespace_and_block_boundaries; preserve_case_punctuation_and_symbols',
        'limits':{'max_chars_per_side':MAX_CHARS,'max_tokens_per_side':MAX_TOKENS,
            'max_match_pairs':MAX_MATCH_PAIRS,'max_changes':MAX_CHANGES,'max_source_regions':MAX_REGIONS,
            'max_output_chars':MAX_OUTPUT_CHARS},
        'coverage':{'before':{'characters':counts[0],'blocks':len(before)},'after':{'characters':counts[1],'blocks':len(after)}},
        'limitations':['仅比较所选解析版本中已提取的正文，不推断图片、未识别公式或语义修订。',
            '空白与分块差异不计为文字修订；大小写、标点、数字及符号变化仍保留。',
            '字符片段来自原文精确偏移，PDF高亮范围为对应的完整文本块。',
            '未标注的移动可能表现为新增与移除，不保证最小编辑路径。']}
    def limited(reason):
        result.update(status='resource_limit',reason=reason)
        return result
    if max(counts)>MAX_CHARS:return limited('character_budget_exceeded')
    a,b=_tokens(before),_tokens(after)
    for side,values in [('before',a),('after',b)]:
        result['coverage'][side].update(tokens=len(values),token_count_exact=len(values)<=MAX_TOKENS)
    if max(len(a),len(b))>MAX_TOKENS:return limited('token_budget_exceeded')
    if not a or not b:
        result.update(status='no_comparable_tokens',reason='empty_saved_text')
        return result
    aa,bb=[t[0] for t in a],[t[0] for t in b]
    # Match SequenceMatcher's documented popular-token exclusion, and cap the
    # remaining candidate-pair work before constructing the matcher.
    ca,cb=Counter(aa),Counter(bb);popular={t for t,n in cb.items() if len(bb)>=200 and n>len(bb)//100+1}
    pairs=sum(n*cb[t] for t,n in ca.items() if t not in popular)
    result['alignment_audit']={'estimated_match_pairs':pairs,'popular_tokens':len(popular),
        'model_calls':0,'algorithm':'SequenceMatcher_autojunk_true'}
    if pairs>MAX_MATCH_PAIRS:return limited('alignment_budget_exceeded')
    result['text_equivalent']=aa==bb
    used=0;total=0;stopped=False;output_chars=0
    for kind,i,j,x,y in SequenceMatcher(a=aa,b=bb,autojunk=True).get_opcodes():
        if kind=='equal':continue
        total+=1
        if stopped or len(result['changes'])>=MAX_CHANGES:
            stopped=True;continue
        left,right=_regions(before,a[i:j]),_regions(after,b[x:y])
        if used+len(left)+len(right)>MAX_REGIONS:
            stopped=True;continue
        used+=len(left)+len(right)
        change={'kind':kind,'before':left[0] if left else None,'after':right[0] if right else None,
            'before_regions':left,'after_regions':right,
            'before_text':'\n'.join(r['change_span']['text'] for r in left),
            'after_text':'\n'.join(r['change_span']['text'] for r in right),
            'join_policy':'newline_between_distinct_source_blocks; no_generated_text'}
        change['change_id']='D_'+hashlib.sha256(json.dumps(change,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        size=len(json.dumps(change,ensure_ascii=False))
        if output_chars+size>MAX_OUTPUT_CHARS:
            stopped=True;continue
        output_chars+=size
        result['changes'].append(change)
    result.update(total_changes=total,changes_truncated=stopped,complete=not stopped,
        status='partial_output' if stopped else 'compared')
    return result
