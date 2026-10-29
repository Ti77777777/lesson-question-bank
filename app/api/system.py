"""系统接口：元信息、备份、变更记录。对应 docs/API.md §1。"""
from __future__ import annotations
import secrets
import time
import uuid
import json
import zipfile

from app import db
from app.server import get, post, ApiError
from app import full_backup
_BACKUP_DOWNLOADS = {}


@get('/api/backups')
def full_backups(req):
    items=[]
    for p in sorted(full_backup.folder().glob('QB-*.zip'),key=lambda x:x.stat().st_mtime,reverse=True):
        item={'id':p.stem,'bytes':p.stat().st_size}
        try:
            with zipfile.ZipFile(p) as z:
                if z.getinfo('manifest.json').file_size<=8*1024**2:
                    m=json.loads(z.read('manifest.json'));item.update(created_at=m.get('created_at'),questions=m.get('questions'))
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):pass
        items.append(item)
    return {'items':items[:100]}


@post('/api/backups')
def create_full_backup(req):
    return full_backup.create()


@post('/api/backups/import')
def import_full_backup(req):
    if not req._raw or len(req._raw)>512*1024**2:raise ApiError(400,'size','请选择512MB以内的完整备份 ZIP')
    bid='QB-'+uuid.uuid4().hex;path=full_backup.folder()/(bid+'.zip')
    try:
        path.write_bytes(req._raw)
        manifest=full_backup.inspect(path)
        with db.transaction():db.audit('import_full_backup','system',bid,{'files':len(manifest['files'])})
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'id':bid,'files':len(manifest['files']),'questions':manifest['questions']}


@get('/api/backups/{bid}/download')
def download_full_backup(req):
    return 200,'application/zip',full_backup.archive(req.params['bid']).read_bytes()


@post('/api/backups/{bid}/check')
def check_full_backup(req):
    result=full_backup.inspect(full_backup.archive(req.params['bid']))
    return {'id':req.params['bid'],'created_at':result['created_at'],'questions':result['questions'],'files':len(result['files']),'verified':True}


@post('/api/backups/{bid}/restore')
def restore_full_backup(req):
    if req.body('confirmed') is not True:raise ApiError(400,'confirm','请先确认创建独立恢复副本')
    return full_backup.restore(req.params['bid'])


@get("/api/meta")
def meta(req):
    conn = db.db()
    counts = {
        "questions": db.scalar("SELECT COUNT(*) FROM question WHERE merged_into IS NULL"),
        "versions": db.scalar("SELECT COUNT(*) FROM question_version"),
        "sources": db.scalar("SELECT COUNT(*) FROM source"),
        "collections": db.scalar("SELECT COUNT(*) FROM collection"),
        "releases": db.scalar("SELECT COUNT(*) FROM release"),
        "inbox_pending": db.scalar("SELECT COUNT(*) FROM inbox_item WHERE status IN ('pending','extracted')"),
        "tags": db.scalar("SELECT COUNT(*) FROM tag WHERE status = 'active'"),
    }
    return {
        "schema_version": db.scalar("SELECT value FROM meta WHERE key = 'schema_version'"),
        "root": db.ROOT.as_posix(),
        "db_path": db.rel_to_root(db.DB_PATH),
        "draft_scope": db.rel_to_root(db.DB_PATH) + ':' + str(db.scalar("SELECT value FROM meta WHERE key='created_at'")),
        "counts": counts,
        "today": db.today(),
    }


@post("/api/backup")
def backup(req):
    target = db.backup()
    db.audit("backup", "system", None, {"file": db.rel_to_root(target)})
    db.db().commit()
    token=secrets.token_urlsafe(24)
    _BACKUP_DOWNLOADS[token]=(target,time.monotonic()+600)
    for old,(_,expires) in list(_BACKUP_DOWNLOADS.items()):
        if expires<time.monotonic():_BACKUP_DOWNLOADS.pop(old,None)
    return {
        "file": db.rel_to_root(target),
        "url": "/api/backup-download/" + token,
        "bytes": target.stat().st_size,
        "created_at": db.now(),
    }

@get('/api/backup-download/{token}')
def backup_download(req):
    item=_BACKUP_DOWNLOADS.get(req.params['token'])
    if not item or item[1]<time.monotonic():raise ApiError(404,'expired','备份下载链接已过期，请重新备份')
    return 200,'application/octet-stream',item[0].read_bytes()


@get("/api/audit")
def audit_list(req):
    target_type = req.arg("target_type")
    target_id = req.arg("target_id")
    limit = req.arg("limit", 50, int)
    sql = "SELECT * FROM audit_log WHERE 1 = 1"
    params: list = []
    if target_type:
        sql += " AND target_type = ?"
        params.append(target_type)
    if target_id:
        sql += " AND target_id = ?"
        params.append(target_id)
    sql += " ORDER BY at DESC LIMIT ?"
    params.append(limit)
    items = db.all_(sql, params)
    return {"items": items, "total": len(items)}
