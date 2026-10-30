"""极简 REST 内核：路由、JSON 编解码、静态文件。

只用标准库，目的是让整套系统在任意一台装了 Python 3.10+ 的 Windows 机器上
`python app/server.py` 直接可用，不依赖虚拟环境与第三方包。
"""
from __future__ import annotations

import json
import mimetypes
import re
import os
import socket
import sys
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 以 `python -m app.server` 启动时，本模块被加载为 __main__，
# 而业务模块里的 `from app.server import get` 会再加载一份 app.server，
# 两份的 ROUTES 互不相干，表现为所有接口 404。此处让二者共享同一实例。
if __name__ == "__main__":
    sys.modules.setdefault("app.server", sys.modules["__main__"])

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

ROUTES: list[tuple[str, re.Pattern, callable, str]] = []


def _reject_json_constant(value):
    raise ValueError('JSON numbers must be finite')


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def route(method: str, pattern: str):
    """注册路由，pattern 中用 {name} 占位，如 /api/questions/{qid}。"""
    def deco(fn):
        regex = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")
        ROUTES.append((method.upper(), regex, fn, pattern))
        return fn
    return deco


def get(pattern): return route("GET", pattern)
def post(pattern): return route("POST", pattern)
def patch(pattern): return route("PATCH", pattern)
def put(pattern): return route("PUT", pattern)
def delete(pattern): return route("DELETE", pattern)


class Request:
    def __init__(self, method: str, path: str, query: dict, body: bytes, headers):
        self.method = method
        self.path = path
        self.query = query
        self._raw = body
        self.headers = headers
        self._json = None
        self.params: dict = {}

    @property
    def json(self) -> dict:
        if self._json is None:
            if not self._raw:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self._raw.decode("utf-8"), parse_constant=_reject_json_constant)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise ApiError(400, "bad_json", "请求体不是合法 JSON，或嵌套层数过深") from None
        if not isinstance(self._json, dict):
            raise ApiError(400, "bad_json", "请求体必须是 JSON 对象")
        return self._json

    def arg(self, name: str, default=None, cast=str):
        value = self.query.get(name)
        if value is None or value == "":
            return default
        try:
            return cast(value)
        except (TypeError, ValueError):
            raise ApiError(400, "bad_query", f"查询参数 {name} 取值不合法：{value}")

    def body(self, name: str, default=None, required: bool = False):
        value = self.json.get(name, default)
        if required and (value is None or value == ""):
            raise ApiError(400, "missing_field", f"缺少必填字段：{name}")
        return value


def json_response(data, status: int = 200):
    return status, "application/json; charset=utf-8", json.dumps(
        data, ensure_ascii=False, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "QBank/1.0"
    request_timeout = 30

    def version_string(self):
        return self.server_version

    def setup(self):
        super().setup()
        self.connection.settimeout(self.request_timeout)

    def finish(self):
        try:
            super().finish()
        finally:
            from app import db
            db.close()

    def log_message(self, fmt, *args):  # 静音默认访问日志
        if "--verbose" in sys.argv:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -------------------------------------------------------- 分发
    def _dispatch(self):
        hosts = self.headers.get_all('Host', [])
        host = hosts[0].lower() if len(hosts) == 1 else ''
        port = self.server.server_port
        allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if port == 80:
            allowed_hosts.update(('127.0.0.1', 'localhost'))
        if host not in allowed_hosts:
            return self._send(*json_response({'error':{'code':'host','message':'仅允许本机访问'}},403))
        try:
            if not self.path.startswith('/') or self.path.startswith('//'):
                raise ValueError('invalid request target')
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            if any(ord(c) < 32 for c in path):
                raise ValueError('invalid path')
            query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, max_num_fields=1000).items()}
        except ValueError:
            return self._send(*json_response({'error':{'code':'bad_path','message':'请求路径或查询参数无效'}},400))

        if self.command not in ('GET','HEAD'):
            origins = self.headers.get_all('Origin', [])
            if origins and (len(origins) != 1 or origins[0].lower() != 'http://' + host):
                return self._send(*json_response({'error':{'code':'origin','message':'拒绝跨站写入请求'}},403))
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self._send(*json_response({'error':{'code':'origin','message':'拒绝跨站写入请求'}},403))

        if path.startswith("/files/"):
            if self.command not in ('GET', 'HEAD'):
                return self._send(*json_response({'error':{'code':'method','message':'附件仅支持读取'}},405))
            return self._serve_file(path[len("/files/"):])

        if self.command in ("GET", "HEAD") and not path.startswith("/api/"):
            return self._serve_static(path)

        try:
            if len(self.headers.get_all('Content-Length', [])) > 1:
                raise ValueError('duplicate content length')
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(*json_response({'error':{'code':'body_size','message':'请求长度无效'}},400))
        limit = 512*1024*1024 if path == '/api/backups/import' else 50*1024*1024 if path == '/api/ai/files' else 70*1024*1024 if '/files' in path else 4*1024*1024
        if length < 0 or length > limit or self.headers.get('Transfer-Encoding'):
            self.close_connection = True
            return self._send(*json_response({'error':{'code':'body_size','message':'请求超过允许大小或格式不受支持'}},413))
        try:
            raw = self.rfile.read(length) if length else b""
        except TimeoutError:
            self.close_connection = True
            return self._send(*json_response({'error':{'code':'request_timeout','message':'接收请求超时，请重新提交'}},408))
        if len(raw) != length:
            self.close_connection = True
            return self._send(*json_response({'error':{'code':'bad_body','message':'请求内容未完整传输'}},400))

        for method, regex, fn, _pattern in ROUTES:
            if method != self.command:
                continue
            match = regex.match(path)
            if not match:
                continue
            req = Request(self.command, path, query, raw, self.headers)
            req.params = match.groupdict()
            try:
                result = fn(req)
            except ApiError as exc:
                payload = {"error": {"code": exc.code, "message": exc.message}}
                if exc.detail:
                    payload["error"]["detail"] = exc.detail
                return self._send(*json_response(payload, exc.status))
            except Exception as exc:  # noqa: BLE001
                reference = uuid.uuid4().hex[:12]
                print(f'Internal request error [{reference}]', file=sys.stderr)
                traceback.print_exc()
                return self._send(*json_response(
                    {"error": {"code": "internal", "message": "处理失败，请查看本地服务日志（编号：" + reference + "）",
                               "reference": reference}}, 500))
            if isinstance(result, tuple) and len(result) == 3 and isinstance(result[0], int):
                return self._send(*result)
            return self._send(*json_response(result if result is not None else {"ok": True}))

        if path.startswith("/api/"):
            return self._send(*json_response(
                {"error": {"code": "not_found", "message": f"没有这个接口：{self.command} {path}"}}, 404))
        return self._serve_static(path)

    def _send(self, status: int, ctype: str, payload: bytes, *, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        safe_headers = {'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'SAMEORIGIN',
                        'Content-Security-Policy': "frame-ancestors 'self'", 'Referrer-Policy': 'no-referrer',
                        'Cross-Origin-Resource-Policy': 'same-origin'}
        safe_headers.update(headers or {})
        for name, value in safe_headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    # -------------------------------------------------------- 静态资源
    def _serve_static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
            return self._send(404, "text/plain; charset=utf-8", b"Not Found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, ctype, target.read_bytes())

    def _serve_file(self, rel: str):
        target = (ROOT / rel).resolve()
        root = ROOT.resolve()
        if not target.is_relative_to(root) or not target.is_file() or target.relative_to(root).parts[0] not in ('sources','assets','releases'):
            return self._send(404, "text/plain; charset=utf-8", b"Not Found")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        inline_types = {'application/pdf', 'image/png', 'image/jpeg', 'image/gif',
                        'image/webp', 'image/bmp', 'image/x-icon', 'text/plain'}
        headers = {}
        if ctype not in inline_types:
            # Uploaded documents must never become scripts/pages on the app's origin.
            ctype = 'application/octet-stream'
            headers = {'Content-Disposition': "attachment; filename=download; filename*=UTF-8''" + urllib.parse.quote(target.name, safe=''),
                       'Content-Security-Policy': "sandbox; default-src 'none'; frame-ancestors 'self'"}
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, ctype, target.read_bytes(), headers=headers)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

def create_app():
    from app import db as database
    from app.api import register_all

    database.init_db()
    register_all()
    from app.ai import worker
    worker.init()
    worker.start()
    return database


def main():
    port = 8760
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    create_app()
    httpd = LocalHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"题库系统已启动：{url}")
    print("按 Ctrl+C 停止。")
    if "--no-browser" not in sys.argv:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
