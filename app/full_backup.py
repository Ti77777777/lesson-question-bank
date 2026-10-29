"""Portable project snapshots; restore into a separate checkout, never over the live DB."""
import hashlib
import json
import re
import shutil
import sqlite3
import stat
import threading
import uuid
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from app import db
from app.server import ApiError

LOCK=threading.Lock()
DIRECTORIES=('app','sources','assets','templates','releases','imports','output')


@contextmanager
def workspace_temp(build):
    # Windows sandbox tokens can lose access to directories created with mode 0700.
    build=build.resolve();path=build/('full-backup-'+uuid.uuid4().hex)
    path.mkdir(parents=True)
    try:yield path
    finally:
        if path.resolve().is_relative_to(build):shutil.rmtree(path)


def folder():
    path=db.store_dir('backup')/'full';path.mkdir(parents=True,exist_ok=True)
    return path


def archive(bid):
    if not isinstance(bid,str) or not re.fullmatch(r'QB-[0-9a-f]{32}',bid):raise ApiError(400,'backup_id','备份编号无效')
    path=folder()/(bid+'.zip')
    if not path.is_file():raise ApiError(404,'backup','备份文件不存在')
    return path


def allowed(name):
    p=PurePosixPath(name)
    if '\\' in name or ':' in name or p.is_absolute() or any(x in ('..','.','') for x in name.split('/')):return False
    if any(x.startswith('.') or x=='__pycache__' for x in p.parts):return False
    if any(x.endswith((' ','.')) or re.fullmatch(r'(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',x,re.I) or any(ord(c)<32 for c in x) for x in p.parts):return False
    if p.suffix in ('.dpapi','.pyc','.tmp'):return False
    return (p.parts[0] in DIRECTORIES or name in ('run.py','README.md','package.json','恢复启动.bat')
            or name in ('data/qbank.db','data/schema.sql','data/catalog_plan.json')
            or name.startswith('build/_ai/documents/'))


def create():
    if not LOCK.acquire(blocking=False):raise ApiError(409,'busy','正在创建备份，请稍后再试')
    target=None
    try:
        bid='QB-'+uuid.uuid4().hex;target=folder()/(bid+'.zip')
        build=db.store_dir('build');build.mkdir(parents=True,exist_ok=True)
        with workspace_temp(build) as tmp:
            snapshot=Path(tmp)/'qbank.db'
            # Hold the write lock while an independent reader snapshots SQLite and files.
            with db.transaction() as conn:
                if conn.execute("SELECT 1 FROM ai_job WHERE state IN ('running','queued')").fetchone() or conn.execute("SELECT 1 FROM ai_step_run WHERE state='running'").fetchone():
                    raise ApiError(409,'running','请先暂停 AI 任务并等待当前请求结束，再创建完整备份')
                with closing(sqlite3.connect(db.DB_PATH)) as src,closing(sqlite3.connect(snapshot)) as dest:src.backup(dest)
                paths=[]
                for name in DIRECTORIES:
                    directory=db.ROOT/name
                    if directory.exists():paths.extend(p for p in directory.rglob('*') if p.is_file())
                docs=db.store_dir('build')/'_ai/documents'
                if docs.exists():paths.extend(p for p in docs.rglob('*') if p.is_file())
                for name in ('run.py','README.md','package.json','data/schema.sql','data/catalog_plan.json'):
                    if (db.ROOT/name).is_file():paths.append(db.ROOT/name)
                files={}
                with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
                    for p in sorted(set(paths)):
                        if p.is_symlink() or not p.resolve().is_relative_to(db.ROOT.resolve()):continue
                        name=p.relative_to(db.ROOT).as_posix()
                        # Map isolated test/demo document caches to the portable production layout.
                        if p.is_relative_to(docs):name='build/_ai/documents/'+p.relative_to(docs).as_posix()
                        if not allowed(name):continue
                        blob=p.read_bytes();z.writestr(name,blob)
                        files[name]={'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()}
                    blob=snapshot.read_bytes();z.writestr('data/qbank.db',blob)
                    files['data/qbank.db']={'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()}
                    manifest={'format':'qbank-full-v1','id':bid,'created_at':db.now(),
                              'original_root':str(db.ROOT.resolve()),'files':files,
                              'credentials_included':False,'questions':conn.execute('SELECT count(*) FROM question WHERE merged_into IS NULL').fetchone()[0]}
                    z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                db.audit('full_backup','system',bid,{'files':len(files)})
        return {'id':bid,'bytes':target.stat().st_size,'created_at':manifest['created_at'],'files':len(files),'questions':manifest['questions']}
    except Exception:
        if target:target.unlink(missing_ok=True)
        raise
    finally:LOCK.release()


def inspect(path):
    try:
        with zipfile.ZipFile(path) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(names)>30000 or len({n.casefold() for n in names})!=len(names) or sum(i.file_size for i in infos)>4*1024**3:
                raise ValueError('备份内容过大或包含重名文件')
            if any(i.filename!='manifest.json' and not allowed(i.filename) or stat.S_ISLNK(i.external_attr>>16) for i in infos):
                raise ValueError('备份包含不允许的路径')
            info=z.getinfo('manifest.json')
            if info.file_size>8*1024**2:raise ValueError('备份清单过大')
            manifest=json.loads(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!='qbank-full-v1' or not isinstance(manifest.get('files'),dict):raise ValueError('备份格式无效')
            if not isinstance(manifest.get('created_at'),str) or type(manifest.get('questions')) is not int:raise ValueError('备份信息不完整')
            files=manifest['files']
            if set(files)!=set(names)-{'manifest.json'} or not {'data/qbank.db','data/schema.sql','run.py'}<=set(files):raise ValueError('备份文件与清单不一致')
            with z.open('data/qbank.db') as database:
                if database.read(16)!=b'SQLite format 3\x00':raise ValueError('备份中的数据库文件无效')
            for name,item in files.items():
                if not isinstance(item,dict):raise ValueError('备份清单格式无效')
                h=hashlib.sha256();size=0
                with z.open(name) as stream:
                    while chunk:=stream.read(1024**2):h.update(chunk);size+=len(chunk)
                if size!=item.get('bytes') or h.hexdigest()!=item.get('sha256'):raise ValueError('备份文件校验失败：'+name)
            return manifest
    except (ValueError,KeyError,zipfile.BadZipFile,RuntimeError,OSError) as exc:
        raise ApiError(400,'invalid_backup',str(exc)) from None


def restore(bid):
    path=archive(bid);manifest=inspect(path)
    directory=db.store_dir('backup')/'restored'/('restore-'+uuid.uuid4().hex)
    directory.mkdir(parents=True)
    try:
        with zipfile.ZipFile(path) as z:
            for name in manifest['files']:
                target=directory/name
                if not target.resolve().is_relative_to(directory.resolve()):raise ApiError(400,'path','恢复路径无效')
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as src,target.open('wb') as out:shutil.copyfileobj(src,out)
        try:
            with closing(sqlite3.connect(directory/'data/qbank.db')) as conn:
                if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok' or conn.execute('PRAGMA foreign_key_check').fetchone():raise ApiError(400,'database','备份数据库检查未通过')
                questions=conn.execute('SELECT count(*) FROM question WHERE merged_into IS NULL').fetchone()[0]
        except sqlite3.DatabaseError:
            raise ApiError(400,'database','备份数据库损坏或不属于题库系统') from None
        # Cache manifests contain absolute page-image paths. Rebase only those generated paths.
        for p in (directory/'build/_ai/documents').glob('*/manifest.json'):
            try:
                value=json.loads(p.read_text(encoding='utf-8'))
                for page in value.get('pages',[]):
                    page['path']=str(p.parent/Path(page['path']).name)
                p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            except (ValueError,KeyError,TypeError):p.unlink(missing_ok=True)
        launcher='@echo off\r\ncd /d "%~dp0"\r\nset "QBANK_AI_WORKER=0"\r\nset "QBANK_DB=%~dp0data\\qbank.db"\r\npython run.py --port 8762\r\npause\r\n'
        (directory/'恢复启动.bat').write_bytes(launcher.encode('utf-8'))
        (directory/'恢复说明.txt').write_text('此目录是独立恢复副本，当前正式题库未被覆盖。\n双击“恢复启动.bat”，在8762端口核对数据。AI后台默认关闭，API密钥需重新配置。\n检查满意后，可停止旧服务并将本目录移到需要的位置使用。\n',encoding='utf-8')
        with db.transaction():db.audit('restore_copy','system',bid,{'directory':db.rel_to_root(directory),'questions':questions})
        return {'directory':str(directory.resolve()),'questions':questions,'files':len(manifest['files']),'port':8762}
    except Exception:
        # Only our freshly-created directory can reach this cleanup branch.
        if directory.resolve().is_relative_to((db.store_dir('backup')/'restored').resolve()):shutil.rmtree(directory)
        raise
