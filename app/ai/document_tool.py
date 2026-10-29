"""Run with bundled Python for PDF/Pillow dependencies; JSON input/output only."""
import hashlib
import json
import sys
import re
from pathlib import Path

LAYOUT_VERSION = 1

def number_boxes(page, words):
    """Use actual PDF rectangles to distinguish boxed numbers from answers."""
    boxes=[]
    shapes=[r for r in page.rects if r.get('fill') and
            .025 <= r['width']/page.width <= .10 and .008 <= r['height']/page.height <= .04]
    for w in words:
        if not re.fullmatch(r'\d{1,3}',w['text'].strip()) or w['x0']/page.width>=.17:continue
        if any(r['x0']<=w['x0'] and r['x1']>=w['x1'] and
               r['top']<=w['top'] and r['bottom']>=w['bottom'] for r in shapes):
            boxes.append({'text':w['text'].strip(),'bbox':[w['x0']/page.width,w['top']/page.height,w['x1']/page.width,w['bottom']/page.height]})
    return boxes

def layout_metadata(source):
    import pdfplumber
    with pdfplumber.open(source) as pdf:
        return {'layout_version':LAYOUT_VERSION,'pages':[
            {'page':i+1,'number_boxes':number_boxes(p,p.extract_words() or [])} for i,p in enumerate(pdf.pages)]}

def prepare(source, directory):
    from PIL import Image, ImageOps
    directory.mkdir(parents=True, exist_ok=True)
    pages=[]
    if source.suffix.lower()=='.pdf':
        import pdfplumber
        import pypdfium2 as pdfium
        pdf=pdfium.PdfDocument(str(source))
        if len(pdf)>50: raise ValueError('PDF 超过50页，请拆分后上传')
        with pdfplumber.open(source) as texts:
            for index in range(len(pdf)):
                page=pdf[index]
                width,height=page.get_size()
                if width*height*(200/72)**2>60_000_000:raise ValueError('PDF页面尺寸过大，请缩小页面后上传')
                bitmap=page.render(scale=200/72); im=bitmap.to_pil().convert('RGB')
                path=directory/f'page-{index+1:03d}.png'; im.save(path)
                p=texts.pages[index]
                words=p.extract_words() or []
                pages.append({'page':index+1,'path':str(path),'width':im.width,'height':im.height,
                              'text':p.extract_text() or '', 'number_boxes':number_boxes(p,words),
                              'words':[{'text':w['text'],'bbox':[w['x0']/p.width,w['top']/p.height,w['x1']/p.width,w['bottom']/p.height]} for w in words]})
                bitmap.close();page.close()
        pdf.close()
    else:
        with Image.open(source) as opened:
            if opened.width*opened.height>60_000_000: raise ValueError('图片像素过大')
            im=ImageOps.exif_transpose(opened).convert('RGB')
            path=directory/'page-001.png'; im.save(path)
            pages=[{'page':1,'path':str(path),'width':im.width,'height':im.height,'text':'','words':[]}]
    return {'pages':pages,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'layout_version':LAYOUT_VERSION}

def crop(source, target, box):
    from PIL import Image
    x0,y0,x1,y1=map(float,box)
    if not (0<=x0<x1<=1 and 0<=y0<y1<=1):raise ValueError('裁图坐标无效')
    with Image.open(source) as im:
        im.crop((int(x0*im.width),int(y0*im.height),int(x1*im.width),int(y1*im.height))).save(target)
    return {'path':str(target)}

if __name__=='__main__':
    try:
        args=json.load(sys.stdin)
        if args['action']=='prepare':result=prepare(Path(args['source']),Path(args['directory']))
        elif args['action']=='layout':result=layout_metadata(Path(args['source']))
        elif args['action']=='crop':result=crop(Path(args['source']),Path(args['target']),args['bbox'])
        else:raise ValueError('Unknown document action')
        print(json.dumps(result,ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__+': '+str(exc)},ensure_ascii=True));sys.exit(1)
