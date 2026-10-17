"""Bounded download -> parse -> grounded QA for one explicitly selected paper."""
from .discovery import DiscoveryService
from .library import LibraryError


async def reading_step(store,service,job):
    job_id=job['id'];payload=job['payload']
    done={item.get('stage'):item for item in job['completed_items']}
    if 'answer' in done:return done['answer']['result']
    if 'download' not in done:
        store.check_cancel(job_id)
        imported=DiscoveryService(store.library).import_paper(payload['paper_id'])
        document=imported['document']
        store.save_item(job_id,'download',{'stage':'download','document_id':document['id'],
            'sha256':document['sha256'],'source':imported['source'],'duplicate':imported['duplicate']},3)
        store.event(job_id,'download_saved','已保存公开原文与来源记录；等待解析。')
        store._yield_step(job_id)
        return None
    source=done['download'];doc_id=source['document_id']
    document=store.library.get_document(doc_id)
    if document['sha256']!=source['sha256']:raise LibraryError('工作流原文件指纹不一致，已停止。')
    if 'parse' not in done:
        run=document['available_run']
        if not run:
            current=document['current_run']
            if current['status'] not in {'queued','running'}:
                raise LibraryError('原文尚无可用解析。请在文献库重新提取或修复失败页，再重试此任务；已下载文件保留。')
            store._yield_step(job_id,message=f'等待原文解析：已处理{current["pages_done"]}/{current["total_pages"]}页。')
            return None
        store.save_item(job_id,'parse',{'stage':'parse','document_id':doc_id,'run_id':run['id'],
            'parse_status':run['status'],'pages_done':run['pages_done'],'total_pages':run['total_pages']},3)
        store.event(job_id,'parse_saved','已固定可用解析版本；下一阶段检索原文并审核回答。')
        store._yield_step(job_id)
        return None
    store.check_cancel(job_id)
    parsed=done['parse']
    answer=await service.answer(payload['question'],[doc_id],mode='qa',
        run_ids={doc_id:parsed['run_id']},check_cancel=lambda:store.check_cancel(job_id))
    answer['workflow']={'paper_id':payload['paper_id'],'download':source,'parse':parsed,
        'scope':'one_explicit_paper; download_parse_qa; not_autonomous_topic_search'}
    store.save_item(job_id,'answer',{'stage':'answer','result':answer},3)
    return answer
