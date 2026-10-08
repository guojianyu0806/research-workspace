"""Workspace-aware research routes; external calls require an explicit action."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .accounts import Accounts, AuthenticationError
from .intelligence import IntelligenceService
from .jobs import JobStore
from .library import Library
from .visualization import DataStudio, render_chart, render_diagram


def library_for(request: Request) -> Library:
    accounts = request.app.state.accounts
    try:
        user = accounts.resolve(request.cookies.get(Accounts.COOKIE))
    except AuthenticationError as exc:
        raise HTTPException(401,str(exc)) from None
    key = user['id'] or 'default'
    cache = request.app.state.libraries
    if key not in cache:
        with request.app.state.libraries_lock:
            if key not in cache:
                library = Library(accounts.directory(user))
                library.network_rate_path = accounts.root / '.arxiv-network.sqlite3'
                cache[key] = library
    return cache[key]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Credentials(StrictInput):
    name: str = Field(min_length=2,max_length=40)
    password: str = Field(min_length=1,max_length=200)


class Metadata(StrictInput):
    title: str | None = Field(default=None,max_length=1000)
    authors: list[str] | None = None


class Relation(StrictInput):
    other_id: str
    action: Literal['keep_both','use_document','use_other'] = 'keep_both'


class ResearchTask(StrictInput):
    kind: Literal['qa','compare','translate','translate_text','review','future_work','draft']
    document_ids: list[str] = Field(default_factory=list,max_length=20)
    document_id: str | None = None
    run_id: str | None = None
    question: str = Field(default='',max_length=4000)
    instruction: str = Field(default='',max_length=10000)
    text: str = Field(default='',max_length=60000)
    selected_text: str | None = Field(default=None,max_length=16000)
    page: int | None = Field(default=None,ge=1)
    block_ids: list[str] | None = Field(default=None,max_length=10000)
    target_language: Literal['zh','en'] = 'zh'


class SubscriptionInput(StrictInput):
    query: str = Field(min_length=2,max_length=300)
    interval_hours: int = Field(default=24,ge=1,le=720)
    field: Literal['all','title'] = 'all'


class SubscriptionUpdate(StrictInput):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None,ge=1,le=720)


class FeedbackInput(StrictInput):
    value: Literal[-1,0,1]


class ChartInput(StrictInput):
    kind: Literal['line','bar']
    x: str
    y: list[str] = Field(min_length=1,max_length=6)
    missing: Literal['reject','drop'] = 'reject'
    normalize: bool = False
    title: str = Field(default='',max_length=150)
    units: dict[str,str] = Field(default_factory=dict)


class DiagramInput(StrictInput):
    nodes: list[dict] = Field(min_length=1,max_length=30)
    edges: list[dict] = Field(default_factory=list,max_length=100)


def task_markdown(job):
    result = job['result'] or {'blocks':job['completed_items']}
    lines = [f'# {job["kind"]}',f'任务编号：{job["id"]}',f'状态：{job["status"]}','']
    if result.get('answer'):
        lines.append(result['answer'])
    for section in result.get('sections',[]):
        lines.append('## '+section.get('heading',''))
        for claim in section.get('claims',[]):
            lines.append(claim.get('text','')+' '+ ' '.join('['+c['evidence_id']+']' for c in claim.get('citations',[])))
        if section.get('proposed_work'):
            lines.append('待验证的研究方案：'+section['proposed_work'])
    for block in result.get('blocks',[]):
        lines.extend([f'## 第{block.get("page", "—")}页 · {block.get("block_id", "")}',
                      str(block.get('source_text',block.get('text',''))), str(block.get('translated_text','')),''])
    if result.get('translated_text'):
        lines.append(result['translated_text'])
    if result.get('limitations'):
        lines.append('## 使用范围与限制\n\n'+'\n'.join('- '+str(value) for value in result['limitations']))
    if result.get('future_work_verification')=='evidence_insufficient':
        lines.append('未来方向核验状态：证据不足；本结果未完成外部最新研究查新，不能据此宣称某方向尚未解决。')
    lines.append('\n## 来源与任务记录\n')
    lines.append('```json\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n```')
    return '\n\n'.join(lines)


def build_router():
    router = APIRouter(prefix='/api')

    @router.get('/account')
    def account(request: Request):
        try:
            return {'user':request.app.state.accounts.resolve(request.cookies.get(Accounts.COOKIE))}
        except AuthenticationError as exc:
            raise HTTPException(401,str(exc)) from None

    def credentials_response(request,credentials,register=False):
        store = request.app.state.accounts
        try:
            token,user = (store.register if register else store.login)(credentials.name,credentials.password)
        except AuthenticationError as exc:
            raise HTTPException(401,str(exc)) from None
        response = JSONResponse({'user':user})
        response.set_cookie(Accounts.COOKIE,token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=7*86400)
        return response

    @router.post('/account/register')
    def register(credentials:Credentials,request:Request):
        return credentials_response(request,credentials,True)

    @router.post('/account/login')
    def login(credentials:Credentials,request:Request):
        return credentials_response(request,credentials)

    @router.post('/account/logout')
    def logout(request:Request):
        request.app.state.accounts.logout(request.cookies.get(Accounts.COOKIE))
        response = JSONResponse({'status':'logged_out'})
        response.delete_cookie(Accounts.COOKIE)
        return response

    @router.patch('/documents/{document_id}')
    def metadata(document_id:str,body:Metadata,library:Library=Depends(library_for)):
        return {'document':library.update_metadata(document_id,**body.model_dump(exclude_none=True))}

    @router.post('/documents/{document_id}/archive')
    def archive(document_id:str,library:Library=Depends(library_for)):
        return {'document':library.archive_document(document_id)}

    @router.post('/documents/{document_id}/restore')
    def restore(document_id:str,library:Library=Depends(library_for)):
        return {'document':library.restore_document(document_id)}

    @router.get('/documents/{document_id}/versions')
    def versions(document_id:str,library:Library=Depends(library_for)):
        return {'family':library.get_version_family(document_id),'candidates':library.find_version_candidates(document_id)}

    @router.post('/documents/{document_id}/versions')
    def confirm_version(document_id:str,body:Relation,library:Library=Depends(library_for)):
        return {'relation':library.confirm_version_relation(document_id,body.other_id,action=body.action)}

    @router.get('/documents/{document_id}/diff')
    def diff(document_id:str,other_id:str,library:Library=Depends(library_for)):
        return library.compare_versions(document_id,other_id)

    @router.get('/documents/{document_id}/structure')
    def structure(document_id:str,run_id:str|None=None,library:Library=Depends(library_for)):
        return library.get_structure(document_id,run_id=run_id)

    @router.post('/jobs/{run_id}/cancel')
    def cancel_parse(run_id:str,library:Library=Depends(library_for)):
        return {'run':library.cancel_run(run_id)}

    @router.post('/documents/{document_id}/pages/{page}/retry')
    def retry_page(document_id:str,page:int,run_id:str|None=None,library:Library=Depends(library_for)):
        return {'run':library.retry_page(document_id,page,run_id=run_id)}

    def create_task_locked(body:ResearchTask,library:Library):
        service = IntelligenceService(library)
        if not service.capabilities()['configured']:
            raise HTTPException(503,'尚未配置大模型服务。请先完成本机模型配置；此处不会生成模拟结果。')
        payload = body.model_dump(exclude_none=True)
        if body.kind=='translate':
            if not body.document_id:
                raise HTTPException(400,'请选择待翻译文献。')
            doc = library.get_document(body.document_id)
            run = doc['available_run']
            if not run:
                raise HTTPException(400,'该文献没有可用文字，需先完成解析或OCR。')
            payload['run_id'] = body.run_id or run['id']
            # Validate the exact selection before enqueuing a potentially long operation.
            selection = service.list_translation_blocks(body.document_id,run_id=payload['run_id'],page=body.page,block_ids=body.block_ids)
            if not selection['blocks']:
                raise HTTPException(400,'选定范围没有可翻译文本。')
            if body.selected_text is not None:
                if len(selection['blocks'])!=1 or not body.selected_text.strip() or body.selected_text not in selection['blocks'][0]['text']:
                    raise HTTPException(400,'所选文字必须是同一原文块中的连续片段。')
        elif body.kind=='translate_text':
            if not body.text.strip():
                raise HTTPException(400,'请输入需要翻译的文本。')
        else:
            if not body.document_ids:
                raise HTTPException(400,'请至少选择一篇文献。')
            for document_id in body.document_ids:
                library.get_document(document_id)
            if body.kind in {'qa','compare'} and not body.question.strip():
                raise HTTPException(400,'请输入问题。')
            if body.kind=='compare' and len(set(body.document_ids))<2:
                raise HTTPException(400,'比较需要至少两篇文献。')
        return {'job':JobStore(library).create(body.kind,payload)}

    @router.post('/research/tasks',status_code=202)
    def create_task(body:ResearchTask,request:Request,library:Library=Depends(library_for)):
        with request.app.state.model_settings_lock:
            return create_task_locked(body,library)

    @router.get('/research/tasks')
    def tasks(library:Library=Depends(library_for)):
        return {'jobs':JobStore(library).list()}

    @router.get('/research/tasks/{job_id}')
    def task(job_id:str,library:Library=Depends(library_for)):
        store = JobStore(library)
        summary = store.summary(job_id)
        return {'job':store.get(job_id,include_items=summary['status'] not in {'queued','running'})}

    @router.post('/research/tasks/{job_id}/cancel')
    def cancel(job_id:str,library:Library=Depends(library_for)):
        return {'job':JobStore(library).cancel(job_id)}

    @router.post('/research/tasks/{job_id}/retry')
    def retry(job_id:str,request:Request,library:Library=Depends(library_for)):
        with request.app.state.model_settings_lock:
            return {'job':JobStore(library).retry(job_id)}

    @router.get('/research/tasks/{job_id}/events')
    async def events(job_id:str,request:Request,library:Library=Depends(library_for)):
        store = JobStore(library)
        await run_in_threadpool(store.summary,job_id)
        async def stream():
            previous = None
            while not await request.is_disconnected():
                job = await run_in_threadpool(store.summary,job_id)
                if job['updated_at'] != previous:
                    yield 'data: '+json.dumps(job,ensure_ascii=False)+'\n\n'
                    previous = job['updated_at']
                if job['status'] not in {'queued','running'}:
                    break
                await asyncio.sleep(.75)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @router.get('/research/tasks/{job_id}/export')
    def export_task(job_id:str,library:Library=Depends(library_for)):
        job = JobStore(library).get(job_id)
        return Response(task_markdown(job),media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="research-{job_id}.md"'})

    @router.get('/discover/search')
    def discovery_search(q:str=Query(min_length=2,max_length=300),field:Literal['all','title']='all',library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return DiscoveryService(library).search(q,field=field)

    @router.post('/discover/import/{paper_id:path}')
    def discovery_import(paper_id:str,library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return DiscoveryService(library).import_paper(paper_id)

    @router.get('/subscriptions')
    def subscriptions(library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return {'subscriptions':DiscoveryService(library).list_subscriptions()}

    @router.post('/subscriptions')
    def subscribe(body:SubscriptionInput,library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return {'subscription':DiscoveryService(library).create_subscription(**body.model_dump())}

    @router.patch('/subscriptions/{subscription_id}')
    def subscription_update(subscription_id:str,body:SubscriptionUpdate,library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return {'subscription':DiscoveryService(library).update_subscription(subscription_id,**body.model_dump(exclude_none=True))}

    @router.post('/subscriptions/{subscription_id}/check')
    def check_subscription(subscription_id:str,library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return DiscoveryService(library).check_subscription(subscription_id)

    @router.get('/recommendations')
    def recommendations(q:str=Query(default='',max_length=300),library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return DiscoveryService(library).recommendations(keywords=q)

    @router.post('/discover/feedback/{paper_id:path}')
    def feedback(paper_id:str,body:FeedbackInput,library:Library=Depends(library_for)):
        from .discovery import DiscoveryService
        return DiscoveryService(library).feedback(paper_id,body.value)

    @router.get('/graph')
    def graph(document_id:list[str]=Query(default=[]),library:Library=Depends(library_for)):
        from .graph import GraphService
        return GraphService(library).build(document_id or None)

    @router.get('/datasets')
    def datasets(library:Library=Depends(library_for)):
        return {'datasets':DataStudio(library).list_datasets()}

    @router.post('/datasets',status_code=201)
    async def upload_csv(file:UploadFile,library:Library=Depends(library_for)):
        try:
            content = await file.read(5*1024*1024+1)
            return {'dataset':DataStudio(library).import_csv(content,file.filename or 'data.csv')}
        finally:
            await file.close()

    @router.get('/datasets/{dataset_id}')
    def dataset(dataset_id:str,library:Library=Depends(library_for)):
        return {'dataset':DataStudio(library).get_dataset(dataset_id)}

    @router.post('/datasets/{dataset_id}/charts')
    def create_chart(dataset_id:str,body:ChartInput,library:Library=Depends(library_for)):
        return {'chart':DataStudio(library).create_chart(dataset_id,**body.model_dump())}

    @router.get('/charts/{chart_id}/{format}')
    def chart_export(chart_id:str,format:Literal['svg','py','json'],library:Library=Depends(library_for)):
        studio = DataStudio(library)
        chart = studio.get_chart(chart_id)
        text = render_chart(chart['spec']) if format=='svg' else studio.export_script(chart_id) if format=='py' else json.dumps(chart,ensure_ascii=False,indent=2)
        return Response(text,media_type='image/svg+xml' if format=='svg' else 'text/plain',headers={'Content-Disposition':f'attachment; filename="chart-{chart_id}.{format}"'})

    @router.post('/diagrams')
    def diagram(body:DiagramInput,library:Library=Depends(library_for)):
        return Response(render_diagram(body.nodes,body.edges),media_type='image/svg+xml')

    return router
