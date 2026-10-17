"""Resumable, bounded PDF evidence review after public abstract screening.

PDF availability and an answer supported by retrieved passages are separate
facts. Neither is a proof that a proposal is solved or that a gap is novel.
"""
from copy import deepcopy
from datetime import datetime, timezone

from .discovery import DiscoveryService

MAX_PAPERS = 3


def _now():
    return datetime.now(timezone.utc).isoformat()


def plan_sources(result):
    audit = result.get('future_verification', {})
    sources = {s['source_id']: s for s in audit.get('sources', [])}
    queries = {q['query_id']: q for q in audit.get('queries', [])}
    selected = []
    # Fair first pass: one returned paper per direction before extra papers.
    directions = audit.get('directions', [])
    for offset in range(MAX_PAPERS):
        for direction in directions:
            ids = list(dict.fromkeys(s for q in direction.get('query_ids', [])
                for s in queries.get(q, {}).get('source_ids', []) if s in sources))
            if offset < len(ids) and ids[offset] not in selected and len(selected) < MAX_PAPERS:
                selected.append(ids[offset])
    return {'papers': [deepcopy(sources[s]) for s in selected],
            'directions': deepcopy(directions), 'queries': deepcopy(list(queries.values())),
            'live_search_required':audit.get('live_refresh_requested',False),'date_filter':audit.get('date_filter'),
            'created_at': _now(), 'paper_limit': MAX_PAPERS}


def _question(direction):
    return (f"核对待验证方向：{direction['proposed_work']}\n"
        "请仅依据所选论文正文，逐篇说明与这个方向直接相关的必要方法或数据条件、结果和明确限制。"
        "优先组织为3至5条自足结论；完整回答优先，不罗列无关实现细节。"
        "对设想中正文没有支持的条件明确说明证据缺口，不把主题相关当成覆盖全部条件。"
        "不要将首轮召回中缺少的信息表述为整篇论文未提供；若涉及表格指标，核对实际列头、行和表题。"
        "每条事实结论必须附自己完整的原文引用，不能借用相邻结论。"
        "不要宣称方向已经解决、研究空白或新颖性；本次只核对检索到的正文片段。")


async def fulltext_step(store, service, job, abstract_result):
    """One download/parse checkpoint/model response per tick; no paid replay."""
    jid = job['id']
    saved = {i.get('stage'): i for i in job.get('completed_items', [])}
    if 'future_fulltext' in saved:
        return saved['future_fulltext']['result']
    plan = saved.get('future_fulltext_plan', {}).get('result')
    if plan is None:
        plan = plan_sources(abstract_result)
        total = 4 + 2 * len(plan['papers']) + len(plan['directions'])
        store.save_item(jid, 'future_fulltext_plan', {'stage': 'future_fulltext_plan', 'result': plan}, total)
        store._yield_step(jid, message='已固定全文来源计划；最多下载三篇公开PDF，分阶段保存进度。')
        return None
    total = 4 + 2 * len(plan['papers']) + len(plan['directions'])
    for source in plan['papers']:
        sid = source['source_id']
        download_key, parse_key = 'future_pdf:' + sid, 'future_parse:' + sid
        if download_key not in saved:
            store.check_cancel(jid)
            try:
                imported = DiscoveryService(store.library).import_paper(sid)
                doc = imported['document']
                record = {'status': 'downloaded', 'document_id': doc['id'], 'sha256': doc['sha256'],
                          'source': imported['source'], 'duplicate': imported['duplicate']}
            except Exception:
                # Fixed safe error, never persist URL headers or transport bodies.
                record = {'status': 'unavailable', 'reason': '公开PDF下载失败，未将摘要当作全文证据。'}
            store.save_item(jid, download_key, {'stage': download_key, **record}, total)
            store._yield_step(jid, message='已保存全文获取记录；失败来源会明确标为不可用。')
            return None
        if parse_key not in saved:
            downloaded = saved[download_key]
            record = {'status': 'unavailable', 'reason': downloaded.get('reason', '全文解析不可用。')}
            if downloaded['status'] == 'downloaded':
                doc = store.library.get_document(downloaded['document_id'])
                if doc['sha256'] != downloaded['sha256']:
                    raise ValueError('全文原文件指纹不一致，已停止核验。')
                run = doc.get('available_run')
                current = doc.get('current_run')
                if not run and current and current['status'] in {'queued', 'running'}:
                    store._yield_step(jid, message=f"等待全文解析：{current['pages_done']}/{current['total_pages']}页。")
                    return None
                if run:
                    record = {'status': 'ready', 'document_id': doc['id'], 'run_id': run['id'],
                              'sha256': doc['sha256'], 'parse_status': run['status'],
                              'pages_done': run['pages_done'], 'total_pages': run['total_pages']}
            store.save_item(jid, parse_key, {'stage': parse_key, **record}, total)
            store._yield_step(jid, message='已保存不可变全文解析版本；接下来逐方向核对正文。')
            return None
    for direction in plan['directions']:
        key = 'future_direction:' + direction['direction_id']
        if key in saved:
            continue
        allowed = {sid for q in plan['queries'] if q['query_id'] in direction.get('query_ids', [])
                   for sid in q.get('source_ids', [])}
        selected = [s for s in plan['papers'] if s['source_id'] in allowed]
        ready = [dict(saved['future_parse:' + s['source_id']], source_id=s['source_id'])
                 for s in selected if saved['future_parse:' + s['source_id']]['status'] == 'ready']
        record = {'direction_id': direction['direction_id'], 'section_index': direction['section_index'],
                  'heading': direction.get('heading', ''), 'proposed_work': direction['proposed_work'],
                  'source_ids': [s['source_id'] for s in selected], 'documents': ready,
                  'excluded': False, 'status': 'evidence_insufficient', 'full_text_reviewed': False,
                  'coverage': 'not_assessed', 'checked_at': _now()}
        own_queries=[q for q in plan['queries'] if q['query_id'] in direction.get('query_ids',[])]
        live_required=plan.get('live_search_required',False)
        fresh=bool(own_queries) and all(q.get('status')=='completed' and q.get('cached') is False
            and (q.get('receipt') or {}).get('id') for q in own_queries)
        covered=not (set(direction.get('required_queries',[]))-{q.get('query','') for q in own_queries})
        specific=direction.get('query_quality')=='direction_specific'
        record['search_audit']={'live_required':live_required,'verified':bool(fresh and covered),
            'query_quality':direction.get('query_quality'),'conditions_preserved':specific,
            'date_filter':plan.get('date_filter'),'queries':deepcopy(own_queries),
            'note':'实时查询不完整、命中缓存或缺少回执时禁止自动筛除。'}
        if live_required and not specific:record['search_audit']['verified']=False
        store.check_cancel(jid)
        if ready:
            docs = list(dict.fromkeys(r['document_id'] for r in ready))
            runs = {r['document_id']: r['run_id'] for r in ready}
            # The established answer pipeline verifies every own quotation,
            # isolated entailment, response coverage and stable claim IDs.
            from .intelligence import ModelOutputError
            try:
                answer = await service.answer(_question(direction), docs, mode='compare' if len(docs)>1 else 'qa', run_ids=runs,
                                              fulltext_review=True,
                                              check_cancel=lambda: store.check_cancel(jid))
            except ModelOutputError as error:
                # A failed review is not a final conclusion. Keep its local
                # index space explicitly attached to the direction being tested.
                for attribute in ('grounding_diagnostics','review_diagnostics'):
                    diagnostic=getattr(error,attribute,None)
                    if isinstance(diagnostic,dict):
                        setattr(error,attribute,{**diagnostic,'future_fulltext_context':{
                            'direction_id':direction['direction_id'],'section_index':direction['section_index'],
                            'documents':ready,'source_ids':record['source_ids']}})
                raise
            passed = answer.get('grounding', {}).get('status') == 'passed' and bool(answer.get('claims'))
            record.update(status='passage_reviewed' if passed else 'evidence_insufficient',
                          full_text_reviewed=passed, answer=answer,
                          coverage='retrieved_passages_only',
                          reason='正文结论及其独立引用已审核；不代表全文穷尽审阅或设想全部条件已被覆盖。' if passed else
                                 '没有形成通过正文审核的结论；保留原设想及证据缺口。')
        else:
            record['reason'] = '查询关联来源未取得可用全文，或超出三篇全文预算；未调用正文判定模型。'
        # Save a returned paid response before observing a racing cancellation.
        store.save_item(jid, key, {'stage': key, 'result': record}, total)
        store._yield_step(jid, message=f"已保存{direction['direction_id']}的独立正文引用与审核记录。")
        return None
    result = deepcopy(abstract_result)
    directions = [saved['future_direction:' + d['direction_id']]['result'] for d in plan['directions']]
    reviewed = sum(d['full_text_reviewed'] for d in directions)
    result['future_fulltext'] = {'status': 'completed' if directions and reviewed == len(directions) else 'partial' if reviewed else 'evidence_insufficient',
        'prompt_version': 'future-fulltext-v2', 'scope': 'bounded_arxiv_pdf_retrieved_passages',
        'paper_limit': MAX_PAPERS, 'started_at': plan['created_at'], 'completed_at': _now(),
        'sources': [{**s, 'download': saved['future_pdf:' + s['source_id']],
                     'parse': saved['future_parse:' + s['source_id']]} for s in plan['papers']],
        'directions': directions, 'novelty_proven': False, 'automatic_exclusion': False,
        'notice': '有限公开PDF正文检索核验；每条正文结论有独立引用及审核。未穷尽全文或最新研究，不据此认定方向已解决。'}
    # Abstract screening remains intact as historical preliminary evidence, but
    # cannot silently remove a proposal once the full-text phase is requested.
    result['retained_directions'] = [{**d, 'excluded': False} for d in plan['directions']]
    result['excluded_directions'] = []
    result['future_work_verification'] = 'bounded_fulltext_passage_review'
    result.setdefault('limitations', []).append(result['future_fulltext']['notice'])
    store.save_item(jid, 'future_fulltext', {'stage': 'future_fulltext', 'result': result}, total)
    return result
