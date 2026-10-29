"""Read exam identity from document headers, never from a student's filename."""
import json
import re
from app import db,provenance

SCHOOL=re.compile(r'^(.{2,45}?(?:中学|学校|[一二三四五六七八九十百零〇0-9]+中|附中))(?=\s|20\d{2}|$)')

def compact(value):
    return re.sub(r'\s+','',str(value or '')).strip(' ·-—')

def extract(text='', recognized_title=''):
    lines=[compact(x) for x in text.splitlines()[:12] if compact(x)]
    title='';school='';method='';region=''
    for index,line in enumerate(lines):
        match=SCHOOL.match(line)
        if not match:continue
        school=match[1];parts=[line]
        for next_line in lines[index+1:index+3]:
            if re.fullmatch(r'(?:高中|高一|高二|高三)?(?:数学|语文|英语|物理|化学|生物|政治|历史|地理)(?:试题|试卷)?|(?:标准)?(?:参考)?(?:答案|解答|解析)版',next_line):parts.append(next_line)
            else:break
        title=''.join(parts);method='pdf_header'
        dates=[x for x in lines[:index] if re.fullmatch(r'20\d{2}年\d{1,2}月(?:\d{1,2}日)?',x)]
        if dates and dates[-1] not in title:title+='（'+dates[-1]+'）'
        break
    if not title:
        # City/province examinations have a real paper identity without a school.
        for index,line in enumerate(lines):
            match=re.match(r'^([\u4e00-\u9fff]{2,20}(?:省|市|区|县))(?=20\d{2})',line)
            if not match:continue
            candidate=line
            for extra in lines[index+1:index+3]:
                if re.search(r'(?:试卷|试题)$',candidate):break
                if re.match(r'^[一二三四五六七八九十]+[、.]',extra):break
                candidate+=extra
            if re.search(r'(?:数学|语文|英语|物理|化学|生物|政治|历史|地理).*(?:试卷|试题)',candidate):
                title=candidate;region=match[1];method='pdf_region_header';break
    if not title:
        candidate=compact(recognized_title)
        match=SCHOOL.match(candidate)
        if match:title=candidate;school=match[1];method='ai_page_title'
    if not title:return {}
    # Only characters from a heading are persisted, with bounded display length.
    title=re.sub(r'[\x00-\x1f]','',title)[:200]
    year=re.search(r'20\d{2}(?:[～~—-]20\d{2})?',title)
    grade=re.search(r'高[一二三]',title)
    return {'title':title,'school':school,'year':year[0] if year else '',
            'grade':grade[0] if grade else '', 'region':region,'method':method,'evidence_page':1}

def apply(file_id, metadata):
    if not metadata.get('title'):return {'updated':False,'questions':0}
    with db.transaction() as conn:
        f=conn.execute('SELECT * FROM source_file WHERE id=?',(file_id,)).fetchone()
        if not f:return {'updated':False,'questions':0}
        source=conn.execute('SELECT * FROM source WHERE id=?',(f['source_id'],)).fetchone()
        jobs=conn.execute('SELECT document_json FROM ai_job WHERE source_file_id=?',(file_id,)).fetchall()
        previous_titles=[json.loads(j[0] or '{}').get('paper',{}).get('title') for j in jobs]
        from pathlib import Path
        generic=source['name'] in (Path(f['filename']).stem,'待识别试卷来源','待确认试卷来源') or source['name'] in previous_titles
        if any(json.loads(j[0] or '{}').get('paper',{}).get('method')=='manual' for j in jobs):generic=False
        # Preserve a title explicitly edited by the teacher.
        title=metadata['title'] if generic else source['name']
        conn.execute('UPDATE source SET name=?,region=COALESCE(NULLIF(region,\'\'),NULLIF(?,\'\')) WHERE id=?',
                     (title,metadata.get('region',''),source['id']))
        count=provenance.rename_generated_titles(conn,source['id'],source['name'],title)
        for j in conn.execute('SELECT * FROM ai_job WHERE source_file_id=?',(file_id,)).fetchall():
            document=json.loads(j['document_json'] or '{}')
            previous=document.get('paper',{})
            if previous.get('method')=='manual':paper={**metadata,**previous,'title':title}
            else:paper={**metadata,'title':title}
            document['paper']=paper
            conn.execute('UPDATE ai_job SET title=?,document_json=?,updated_at=? WHERE id=?',(title,db.dumps(document),db.now(),j['id']))
        db.audit('ai_source_identity','source',source['id'],{'old_name':source['name'],'name':title,'school':metadata.get('school'),'evidence_page':metadata.get('evidence_page'),'renamed_questions':count})
    return {'updated':True,'title':title,'questions':count}

def for_job(job):
    if not job.get('source_file_id'):return {}
    row=db.one('SELECT s.*,f.id AS source_file_id FROM source s JOIN source_file f ON f.source_id=s.id WHERE f.id=?',(job['source_file_id'],))
    if row:
        from app import search_catalog
        row['classification']=search_catalog.metadata(row)
    return row or {}
