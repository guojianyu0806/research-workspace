"""Read-only public GitHub checks. Repository text is data, never instructions."""
import base64
from datetime import datetime,timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit,quote

import httpx

SOURCE_EXTENSIONS={'.py','.ipynb','.cpp','.cc','.c','.cu','.rs','.java','.js','.ts','.jl','.r','.m'}
SUPPORTED_OPEN_LICENSES={'MIT','Apache-2.0','BSD-2-Clause','BSD-3-Clause','ISC','GPL-2.0','GPL-3.0',
    'GPL-2.0-only','GPL-2.0-or-later','GPL-3.0-only','GPL-3.0-or-later','LGPL-2.1','LGPL-3.0',
    'LGPL-2.1-only','LGPL-2.1-or-later','LGPL-3.0-only','LGPL-3.0-or-later','AGPL-3.0',
    'AGPL-3.0-only','AGPL-3.0-or-later','MPL-2.0','EPL-2.0'}


class PublicCheckError(ValueError):
    def __init__(self,status):self.status=status


def code_urls(text):
    normalized=unicodedata.normalize('NFKC',text)
    return list(dict.fromkeys('https://github.com/'+owner+'/'+repo.rstrip('.,')
        for owner,repo in re.findall(r'https://github\.com/\s*([A-Za-z0-9_.-]+)\s*/\s*([A-Za-z0-9_.-]+)',normalized)))


def local_code_hints(library,doc):
    run=doc.get('available_run');hints=[]
    if not run:return hints
    for block in library._evidence_blocks(doc['id'],run['id']):
        if block['page']>12:break
        for url in code_urls(block['text']):
            if url.casefold() not in {x['url'].casefold() for x in hints}:
                hints.append({'url':url,'verification':'unverified_pdf_link','source_type':'original_pdf',
                    'source_evidence':{'document_id':doc['id'],'run_id':run['id'],'block_id':block['block_id'],
                        'page':block['page'],'bbox':block['bbox'],'text':block['text'][:4000]},
                    'url_normalization':'printed_path_whitespace_and_NFKC'})
    # Links may be attached to "Code" rather than printed as a URL.
    import pymupdf
    from .library import MUPDF_LOCK
    with MUPDF_LOCK,pymupdf.open(library._source_path(doc['id'])) as pdf:
        for number in range(min(len(pdf),12)):
            page=pdf[number]
            for link in page.get_links():
                url=link.get('uri','')
                name=repository_name(url)
                if name and name.casefold() not in {str(repository_name(x['url'])).casefold() for x in hints}:
                    url='https://github.com/'+name
                    rect=link['from']*page.rotation_matrix
                    bbox=[rect.x0/page.rect.width,rect.y0/page.rect.height,rect.x1/page.rect.width,rect.y1/page.rect.height]
                    hints.append({'url':url,'verification':'unverified_pdf_link','source_type':'pdf_link_annotation',
                        'source_evidence':{'document_id':doc['id'],'run_id':run['id'],'page':number+1,'bbox':bbox,
                            'annotation_uri':url,'annotation_xref':link.get('xref'),'pdf_sha256':doc['sha256']}})
    return hints[:8]


def repository_name(url):
    try:
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password
                or parsed.port or parsed.query or parsed.fragment):return None
        parts=parsed.path.strip('/').split('/')
        if len(parts)!=2 or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',p) or '..' in p for p in parts):return None
        return '/'.join(parts).removesuffix('.git')
    except ValueError:return None


def paper_signature(paper,url):
    return hashlib.sha256(json.dumps([paper['canonical_id'],paper['title'],url],ensure_ascii=False).encode()).hexdigest()


def verify_repository(paper,url,client=None):
    checked=datetime.now(timezone.utc).isoformat()
    result={'url':url,'checked_at':checked,'paper_signature':paper_signature(paper,url),
        'verification':'unverified','association_verified':False,'open_source_verified':False,
        'reproduction_verified':False,'policy':'public_readonly_no_clone_no_execution'}
    name=repository_name(url)
    if not name:return {**result,'reason':'链接不是受支持的公开GitHub仓库地址。'}
    owned=client is None
    client=client or httpx.Client(timeout=httpx.Timeout(12,connect=5),trust_env=False,follow_redirects=False)
    def get(path,limit=800000):
        with client.stream('GET','https://api.github.com/repos/'+name+path,
            headers={'Accept':'application/vnd.github+json','User-Agent':'ResearchWorkspace-public-code-check'},follow_redirects=False) as response:
            if response.status_code!=200:raise PublicCheckError(response.status_code)
            data=bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>limit:raise ValueError('response_limit')
            parsed=json.loads(data)
            if not isinstance(parsed,dict):raise ValueError('invalid_envelope')
            return parsed
    try:
        repo=get('')
        if (str(repo.get('full_name','')).casefold()!=name.casefold() or repo.get('private',True)
                or repo.get('disabled',False)):raise ValueError('repository_identity_or_access')
        readme=get('/readme')
        if readme.get('encoding')!='base64' or readme.get('size',999999)>256000:raise ValueError('readme_limit_or_encoding')
        text=base64.b64decode(readme.get('content',''),validate=False).decode('utf-8',errors='replace')
        if len(text)>256000:raise ValueError('readme_limit')
        identifier=paper['canonical_id']
        identifier_match=re.search(r'(?<![\w])'+re.escape(identifier)+r'(?:v\d+)?(?:\.pdf)?(?![\w.])',text,re.I)
        normalized=lambda s:' '.join(re.findall(r'\w+',s.casefold()))
        title=normalized(paper['title'])
        title_match=len(title.split())>=5 and title in normalized(text)
        if not (identifier_match or title_match):
            return {**result,'reason':'仓库README未明确回指该论文标识或完整标题。','repository':name,'repository_sha':repo.get('default_branch')}
        branch=repo.get('default_branch','')
        if not isinstance(branch,str) or not branch or len(branch)>150:raise ValueError('invalid_default_branch')
        tree=get('/git/trees/'+quote(branch,safe='')+'?recursive=1',1200000)
        paths=[x['path'] for x in tree.get('tree',[]) if isinstance(x,dict) and x.get('type')=='blob' and
            isinstance(x.get('path'),str) and '.'+x['path'].rsplit('.',1)[-1].lower() in SOURCE_EXTENSIONS]
        license_data=repo.get('license')
        license_id=license_data.get('spdx_id') if isinstance(license_data,dict) else None
        licensed=license_id in SUPPORTED_OPEN_LICENSES
        usable=bool(paths) and licensed
        return {**result,'verification':'verified_open_source_association' if usable else 'association_only',
            'association_verified':True,'open_source_verified':usable,'repository':name,
            'repository_url':'https://github.com/'+name,'repository_sha':tree.get('sha'),
            'readme_sha':readme.get('sha'),'association_basis':'arxiv_identifier' if identifier_match else 'complete_title',
            'license_spdx':license_id,'archived':bool(repo.get('archived')),'source_file_examples':paths[:8],
            'license_policy':'conservative_supported_open_license_allowlist; other_licenses_require_manual_check',
            'tree_truncated':bool(tree.get('truncated')),
            'reason':('论文来源链接与仓库README双向关联，已发现许可证和源码；未执行复现实验。'
                +('仓库已归档，需核对依赖兼容性。' if repo.get('archived') else '')) if usable
                else 'README回指论文；许可证或源码尚不满足代码优先条件。'}
    except (httpx.HTTPError,ValueError,TypeError,KeyError) as error:
        # No credential, raw response, README or exception text is persisted.
        return {**result,'reason':'公开仓库核验未完成，可能限流、访问失败或返回格式不支持；可稍后重试。',
            'failure_type':type(error).__name__,
            **({'http_status':error.status} if isinstance(error,PublicCheckError) else {})}
    finally:
        if owned:client.close()
