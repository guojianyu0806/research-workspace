"""Account-scoped page layout and explicit reference discovery endpoints."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from .advanced_api import StrictInput, library_for
from .layout import LayoutService
from .library import Library
from .reference_discovery import ReferenceDiscovery

router = APIRouter(prefix='/api/documents')


class SelectedReferences(StrictInput):
    reference_ids: list[str] = Field(min_length=1,max_length=10)
    max_searches: int = Field(default=5,ge=1,le=10)
    run_id: str | None = None


@router.get('/{document_id}/layout/pages/{page}')
def page_layout(document_id: str,page: int,run_id: str | None = None,library: Library = Depends(library_for)):
    return LayoutService(library).analyze_page(document_id,page,run_id=run_id)


@router.get('/{document_id}/layout/pages/{page}/export')
def export_layout(document_id: str,page: int,run_id: str | None = None,library: Library = Depends(library_for)):
    import json
    result = LayoutService(library).analyze_page(document_id,page,run_id=run_id)
    return Response(json.dumps(result,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="layout-page-{page}.json"'})


@router.get('/{document_id}/layout/summary')
def layout_summary(document_id: str,run_id: str | None = None,library: Library = Depends(library_for)):
    return LayoutService(library).get_document_summary(document_id,run_id=run_id)


@router.get('/{document_id}/references/ranked')
def ranked_references(document_id: str,q: str = Query(default='',max_length=500),
                      include_filtered: bool = False,run_id: str | None = None,library: Library = Depends(library_for)):
    return ReferenceDiscovery(library).rank_references(document_id,q,run_id=run_id,include_filtered=include_filtered)


@router.post('/{document_id}/references/search')
def search_references(document_id: str,body: SelectedReferences,library: Library = Depends(library_for)):
    return ReferenceDiscovery(library).search_selected(document_id,body.reference_ids,
                                                      max_searches=body.max_searches,run_id=body.run_id)
