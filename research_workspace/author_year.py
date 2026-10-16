"""Conservative author-year bibliography and same-line citation candidates."""
from __future__ import annotations
import re
import unicodedata

YEAR=re.compile(r"\b((?:19|20)\d{2}[a-z]?)\b")
NAME=r"[A-ZÀ-ÖØ-Þ][\w’'\-]+"
CITE=re.compile(rf"(?P<author>{NAME})(?:\s+et\s+al\.|\s+and\s+{NAME})?\s*(?:,\s*|\(\s*)(?P<year>(?:19|20)\d{{2}}[a-z]?)\)?")


def key(author,year):
    return (''.join(c for c in unicodedata.normalize('NFKD',author).casefold() if not unicodedata.combining(c)),year)


def bibliography(lines,anchor):
    margins={}
    for line in lines:
        b=line['block'];column=int(b['bbox'][0]>=.48)
        k=(b['page'],column)
        margins[k]=min(margins.get(k,1),b['bbox'][0])
    refs=[];current=None
    for i,line in enumerate(lines):
        block=line['block'];box=block['bbox'];column=int(box[0]>=.48)
        begins=not block['text'][:line['start']].strip()
        start=None
        if begins and box[0]<=margins[(block['page'],column)]+.008:
            preview=' '.join(v['text'] for v in lines[i:i+12])[:650]
            year=YEAR.search(preview)
            if year:
                authors=re.sub(r"(\w)-\s+(\w)",r"\1\2",preview[:year.start()])
                words=re.findall(r"[^\W\d_]+(?:[’'\-][^\W\d_]+)*",authors,re.UNICODE)
                allowed={'and','van','von','de','del','der','di','da','et','al','la'}
                if 2<=len(words)<=80 and all(w[0].isupper() or w in allowed for w in words):
                    first=re.split(r",|\band\b",authors)[0].strip(' .(')
                    surname=re.findall(r"[^\W\d_]+(?:[’'\-][^\W\d_]+)*",first,re.UNICODE)[-1]
                    start=(surname,year[1])
        if start:
            current={'id':f'reference-{len(refs)+1}','label':f'{start[0]}, {start[1]}',
                'text':line['text'],'evidence':anchor(block,line['start'],line['end']),
                'continuation_evidence':[],'citation_ids':[],
                'first_author_surname':start[0],'year_label':start[1],
                'method':'author_year_hanging_indent_heuristic'}
            refs.append(current)
        elif current and not (line['text'].isdigit() and box[1]>.9):
            current['text']+=' '+line['text']
            current['continuation_evidence'].append(anchor(block,line['start'],line['end']))
    return refs


def link_citations(lines,references,anchor,start_number=0):
    by_key={}
    for ref in references:
        if 'first_author_surname' in ref:
            by_key.setdefault(key(ref['first_author_surname'],ref['year_label']),[]).append(ref)
    citations=[]
    for line in lines:
        for match in CITE.finditer(line['text']):
            candidates=by_key.get(key(match['author'],match['year']),[])
            if len(candidates)==1:
                ids=[candidates[0]['id']];unresolved=[]
            else:
                ids=[];unresolved=[f"{match['author']}, {match['year']}"]
            item={'id':f'citation-{start_number+len(citations)+1}','marker':match[0],
                'reference_ids':ids,'unresolved_labels':unresolved,
                'candidate_reference_ids':[r['id'] for r in candidates],
                'evidence':anchor(line['block'],line['start']+match.start(),line['start']+match.end()),
                'method':'author_year_same_line_unique_match'}
            citations.append(item)
            if len(candidates)==1:candidates[0]['citation_ids'].append(item['id'])
    return citations
