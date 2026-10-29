"""Short rich-text previews without cutting LaTeX formulas or image commands."""
import re

MATH=re.compile(r'(?<!\\)\$\$.*?(?<!\\)\$\$|\\\[.*?\\\]|\\\(.*?\\\)|(?<!\\)\$(?:\\.|[^$\\])*?(?<!\\)\$',re.S)


def preview(text,limit=80):
    if not text:return ''
    flat=re.sub(r'\\qbankimage(?:\[[^\]]*\])?\{[^}]*\}','【题图】',str(text))
    flat=re.sub(r'\\begin\{tabular\}.*?\\end\{tabular\}','【表格】',flat,flags=re.S)
    flat=re.sub(r'\s+',' ',flat).strip()
    if len(flat)<=limit:return flat
    out='';pos=0
    for math in MATH.finditer(flat):
        prose=flat[pos:math.start()];remaining=limit-len(out)
        if len(prose)>=remaining:return out+prose[:remaining].rstrip()+'…'
        out+=prose
        token=math[0]
        out+=token if len(token)<=max(256,limit*3) else '【公式，点开查看】'
        pos=math.end()
        if len(out)>=limit:return out+('…' if pos<len(flat) else '')
    remaining=limit-len(out)
    return out+flat[pos:pos+remaining].rstrip()+('…' if pos+remaining<len(flat) else '')
