"""Windows user-bound encrypted credentials. Never log credential values."""
import ctypes
import hashlib
import os
from pathlib import Path
from app import db

class Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]

def credential_path(config=None):
    from app.ai import settings
    cfg = config or settings.current()
    if cfg['base_url'] in ('https://api.deepseek.com', 'https://api.deepseek.com/v1'):
        name = 'deepseek.dpapi'
    else:
        identity = cfg['base_url'] + '|' + cfg['key_env']
        name = 'provider-' + hashlib.sha256(identity.encode()).hexdigest()[:24] + '.dpapi'
    return db.store_dir('data') / 'private' / name

def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('本机加密保存仅支持 Windows；其他系统请设置接口配置中的密钥环境变量')
    buffer = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.windll.crypt32
    if decrypt:
        ok = crypt.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        ok = crypt.CryptProtectData(ctypes.byref(incoming), 'QBank AI', None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise ValueError('Windows 凭据保护失败，请在运行题库的 Windows 用户下重新配置')
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.data)

def save_key(value, config=None):
    value = str(value or '').strip()
    if len(value) < 8 or len(value)>4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError('密钥格式不正确')
    encrypted = _crypt(value.encode())
    path = credential_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_bytes(encrypted)
    tmp.replace(path)

def get_key(config=None):
    from app.ai import settings
    cfg = config or settings.current()
    value = os.environ.get(cfg['key_env'], '').strip()
    if value: return value
    path = credential_path(cfg)
    return _crypt(path.read_bytes(), True).decode() if path.is_file() else ''

def configured(config=None):
    try:
        from app.ai import settings
        cfg = config or settings.current()
        return cfg['auth_mode'] == 'none' or bool(get_key(cfg))
    except Exception: return False
