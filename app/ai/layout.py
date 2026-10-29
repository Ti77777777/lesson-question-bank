"""Use PDF glyph positions when available; visual coordinates remain a fallback."""
import re

def answer_label(value):
    """Bracketed labels and standalone headings used by exported answer editions."""
    return bool(re.search(r'【(?:答案|解析|详解|解答|解|答)】',value) or
                re.fullmatch(r'\s*(?:答案|解析|详解|解答)\s*',value))

def locate(page, questions):
    words=page.get('words',[])
    if not words:return {}
    anchors=[]
    known={str(q['qno']) for q in questions if not q.get('continuation')}
    boxed={n:[w['bbox'] for w in page.get('number_boxes',[]) if w['text']==n] for n in known}
    for w in words:
        m=re.match(r'^(\d{1,3})[.．、]',w['text'])
        if m and m[1] in known and w['bbox'][0]<0.17:anchors.append((m[1],w['bbox']))
        elif w['text'].strip() in known and w['bbox'][0]<0.17:
            # Boxed question numbers may have no punctuation. Require proximity
            # to the visual hint so an answer such as "4" is not an anchor.
            q=next(q for q in questions if str(q['qno'])==w['text'].strip() and not q.get('continuation'))
            hints=q.get('statement_regions',[])
            if hints and abs(w['bbox'][1]-hints[0]['bbox'][1])<.15:
                anchors.append((w['text'].strip(),w['bbox']))
    chosen=[]
    for q in questions:
        if q.get('continuation'):continue
        candidates=boxed.get(str(q['qno'])) or [b for n,b in anchors if n==str(q['qno'])]
        if not candidates:continue
        hint=(q.get('statement_regions') or [{'bbox':[0,0.5,1,0.6]}])[0]['bbox'][1]
        b=min(candidates,key=lambda b:abs(b[1]-hint))
        chosen.append((str(q['qno']),b))
    chosen.sort(key=lambda x:x[1][1])
    result={}
    for i,(qno,b) in enumerate(chosen):
        top=max(0,b[1]-.009)
        next_top=chosen[i+1][1][1]-.009 if i+1<len(chosen) else .94
        refs=[w['bbox'][1] for w in words if b[1]<w['bbox'][1]<next_top and answer_label(w['text'])]
        end=min(refs)-.004 if refs else next_top
        if end<=top:continue
        # Remove blank/footer space without cutting diagrams: use generous next-anchor boundary.
        result[qno]={'statement_regions':[{'bbox':[.065,top,.96,end]}],
                      'reference_regions':[{'bbox':[.065,min(refs)-.002,.96,next_top]}] if refs else [],
                      'method':'pdf_boxed_number_anchors' if boxed.get(qno) else 'pdf_glyph_anchors'}
    # A continuation may start with options or subquestions, without a repeated
    # question number. Recover only the area above the first answer heading.
    for q in questions:
        if not q.get('continuation') or not (q.get('stem') or q.get('options') or q.get('subquestions')):continue
        next_top=chosen[0][1][1]-.009 if chosen else .94
        refs=[w['bbox'][1] for w in words if w['bbox'][1]<next_top and answer_label(w['text'])]
        if not refs:continue  # Keep visual coordinates when no glyph boundary is proven.
        end=min(refs)-.004
        content=[w['bbox'] for w in words if .025<w['bbox'][1]<end and w['bbox'][0]<.96]
        if not content:continue
        top=max(.025,min(b[1] for b in content)-.009)
        result[str(q['qno'])]={'statement_regions':[{'bbox':[.065,top,.96,end]}],
            'reference_regions':[{'bbox':[.065,min(refs)-.002,.96,next_top]}],
            'method':'pdf_glyph_continuation'}
    return result
