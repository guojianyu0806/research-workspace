"""Explicitly synthetic arrangements of existing source pixels, not paper claims.

Original source files stay immutable. Test input is a raster-only PDF; vector
wrapper strokes are rendered before the parser sees the page.
"""
from pathlib import Path
import pymupdf

ROOT=Path(__file__).resolve().parents[1]


def crop(filename,page,box):
    with pymupdf.open(ROOT/'data/samples'/filename) as source:
        return source[page-1].get_pixmap(dpi=288,clip=pymupdf.Rect(box)).tobytes('png')


def wrap(inner,width,height,kind):
    pdf=pymupdf.open();w=width+17;h=height+12 if kind=='root' else height+19
    p=pdf.new_page(width=w,height=h)
    if kind=='root':
        p.insert_image(pymupdf.Rect(15,7,15+width,7+height),stream=inner)
        p.draw_polyline([(1,h*.55),(4,h*.5),(8,h-1),(14,1),(w-1,1)],width=.55,color=(0,0,0))
    else:
        p.insert_image(pymupdf.Rect((w-width)/2,1,(w+width)/2,1+height),stream=inner)
        p.draw_line((1,height+5),(w-1,height+5),width=.55,color=(0,0,0))
        one=crop('UnderstandingDeepLearning_02_09_26_C.pdf',80,[339,482.5,343,490.5])
        p.insert_image(pymupdf.Rect(w/2-2,height+9,w/2+2,height+17),stream=one)
    data=p.get_pixmap(dpi=288).tobytes('png');pdf.close()
    return data,w,h


def fixture(kind,erase=None):
    if kind in ('double-root','triple-root','fraction-in-root'):
        if kind=='fraction-in-root':
            data=crop('UnderstandingDeepLearning_02_09_26_C.pdf',80,[315,482,367,507]);w,h=52,25
        else:
            data=crop('1706.03762v1.pdf',3,[359,715,376.5,727]);w,h=17.5,12
        for _ in range(2 if kind=='triple-root' else 1):data,w,h=wrap(data,w,h,'root')
    else:
        data=crop('UnderstandingDeepLearning_02_09_26_C.pdf',80,[315,482,367,507]);w,h=52,25
        data,w,h=wrap(data,w,h,'fraction')
    with pymupdf.open() as intermediate:
        p=intermediate.new_page(width=240,height=180)
        p.insert_image(pymupdf.Rect(70,60,70+w,60+h),stream=data)
        q=crop('1706.03762v1.pdf',3,[355.5,701.5,363.5,711.5])
        equal=crop('1706.03762v1.pdf',3,[305.5,711.5,313.5,715])
        axis=60+h-13 if kind=='nested-fraction' else 60+h-10
        p.insert_image(pymupdf.Rect(38,axis-4,46,axis+6),stream=q)
        p.insert_image(pymupdf.Rect(52,axis,60,axis+3.5),stream=equal)
        if erase:p.draw_rect(pymupdf.Rect(erase),color=None,fill=(1,1,1),overlay=True)
        image=p.get_pixmap(dpi=288).tobytes('png')
    result=pymupdf.open();p=result.new_page(width=240,height=180);p.insert_image(p.rect,stream=image)
    assert p.get_text()==''
    return result


def ascii_sum_fixture():
    """Derived (x_i-y_i)^2 summand from original source crops; not a paper claim."""
    with pymupdf.open(ROOT/'data/samples/UnderstandingDeepLearning_02_09_26_C.pdf') as source,pymupdf.open() as pdf:
        p=source[32];page=pdf.new_page(width=p.rect.width,height=p.rect.height)
        page.insert_image(page.rect,stream=p.get_pixmap(dpi=288).tobytes('png'))
        tail=p.get_pixmap(dpi=288,clip=pymupdf.Rect(315,599,341,614)).tobytes('png')
        page.draw_rect(pymupdf.Rect(283,602,289,614),color=None,fill=(1,1,1),overlay=True)
        page.draw_rect(pymupdf.Rect(298,599,363,615),color=None,fill=(1,1,1),overlay=True)
        page.insert_image(pymupdf.Rect(299,599,325,614),stream=tail)
        page.draw_rect(pymupdf.Rect(215,627,363,662),color=None,fill=(1,1,1),overlay=True)
        data=page.get_pixmap(dpi=288).tobytes('png')
        result=pymupdf.open();scan=result.new_page(width=p.rect.width,height=p.rect.height);scan.insert_image(scan.rect,stream=data)
    assert result[0].get_text()==''
    return result
