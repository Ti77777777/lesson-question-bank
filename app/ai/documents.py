import json
import os
import subprocess
import sys
import hashlib
import uuid
import math
from pathlib import Path
from app import db

def runtime():
    override=os.environ.get('QBANK_DOCUMENT_PYTHON')
    if override and Path(override).is_file():return override
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if bundled.is_file():return str(bundled)
    return sys.executable

def tool(args):
    env=os.environ.copy()
    for key in list(env):
        if key.endswith(('_API_KEY','_TOKEN')) or key=='QBANK_AI_KEY':env.pop(key,None)
    tmp=db.store_dir('build')/'_ai'/'temp';tmp.mkdir(parents=True,exist_ok=True)
    env['TEMP']=env['TMP']=str(tmp.resolve())
    run=subprocess.run([runtime(),str(Path(__file__).with_name('document_tool.py'))],
                       input=json.dumps(args),capture_output=True,text=True,encoding='utf-8',env=env,timeout=180)
    try:result=json.loads(run.stdout)
    except ValueError:raise ValueError('文档组件不可用，请检查内置 Python 的 PDF/Pillow 依赖') from None
    if not isinstance(result,dict):raise ValueError('文档组件返回了无效的结果格式')
    if run.returncode or result.get('error'):raise ValueError(result.get('error','文档处理失败'))
    return result

def _valid_manifest(result,sha):
    if not isinstance(result,dict) or result.get('sha256')!=sha:return False
    version=result.get('layout_version',0)
    if type(version) is not int or version<0:return False
    pages=result.get('pages')
    if not isinstance(pages,list) or not 1<=len(pages)<=50:return False
    for index,page in enumerate(pages,1):
        if not isinstance(page,dict) or type(page.get('page')) is not int or page['page']!=index:return False
        if not isinstance(page.get('path'),str):return False
        try:
            path=Path(page['path']).resolve()
            if not path.is_relative_to(db.ROOT.resolve()) or not path.is_file() or path.stat().st_size==0:return False
        except (OSError,ValueError):return False
        if not isinstance(page.get('text',''),str) or not isinstance(page.get('words',[]),list):return False
        for field in ('words','number_boxes'):
            if not isinstance(page.get(field,[]),list):return False
            for word in page.get(field,[]):
                if not isinstance(word,dict) or not isinstance(word.get('text'),str):return False
                box=word.get('bbox')
                if not isinstance(box,list) or len(box)!=4 or any(type(v) not in (float,int) or not math.isfinite(v) for v in box):return False
    return True

def _save_manifest(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)

def prepare(file_id):
    row=db.one('SELECT * FROM source_file WHERE id=?',(file_id,))
    if not row:raise ValueError('原文件不存在')
    source=db.abs_path(row['rel_path']).resolve()
    if not source.is_relative_to(db.ROOT.resolve()):raise ValueError('来源路径超出项目')
    directory=db.store_dir('build')/'_ai'/'documents'/file_id
    cached=directory/'manifest.json'
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    result=None
    if cached.exists():
        try:
            result=json.loads(cached.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
        if isinstance(result,dict) and 'source_sha256' in result:result['sha256']=result.pop('source_sha256')
    if _valid_manifest(result,sha):
        if source.suffix.lower()=='.pdf' and result.get('layout_version',0)<1:
            metadata=tool({'action':'layout','source':str(source)})
            indexed={p['page']:p for p in metadata['pages']}
            for page in result['pages']:page['number_boxes']=indexed[page['page']]['number_boxes']
            result['layout_version']=metadata['layout_version']
            _save_manifest(cached,result)
    else:
        result=tool({'action':'prepare','source':str(source),'directory':str(directory.resolve())})
        if not _valid_manifest(result,sha):raise ValueError('文档组件返回的页面缓存无效')
        _save_manifest(cached,result)
    if row.get('pages')!=len(result['pages']):
        with db.transaction() as conn:
            conn.execute('UPDATE source_file SET pages=? WHERE id=?',(len(result['pages']),file_id))
    return result

def crop(page, bbox, target):
    Path(target).parent.mkdir(parents=True,exist_ok=True)
    tool({'action':'crop','source':page['path'],'target':str(Path(target).resolve()),'bbox':bbox})
    return str(target)
