"""Small HTTP security regression suite; synthetic files and an isolated DB only."""
import base64
import http.client
import io
import json
import os
import shutil
import socket
import sys
import threading
import unittest
import urllib.parse
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / 'build' / ('security-' + uuid.uuid4().hex)
(FIXTURE / 'data').mkdir(parents=True)
os.environ['QBANK_DB'] = str(FIXTURE / 'data' / 'fixture.db')
os.environ['QBANK_AI_WORKER'] = '0'
from app import db, full_backup
from app import server


class SecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.create_app()
        cls.patches = [patch.object(db, 'ROOT', FIXTURE), patch.object(server, 'ROOT', FIXTURE)]
        for item in cls.patches:
            item.start()
        (FIXTURE / 'data' / 'private.txt').write_text('PRIVATE_FIXTURE', encoding='utf-8')
        @server.get('/api/_security_error')
        def error(_):
            raise RuntimeError('INTERNAL_FIXTURE ' + str(FIXTURE / 'data' / 'private.txt'))
        class TestHandler(server.Handler):
            request_timeout = 0.2
        cls.httpd = server.LocalHTTPServer(('127.0.0.1', 0), TestHandler)
        cls.port = cls.httpd.server_port
        cls.host = '127.0.0.1:' + str(cls.port)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        code, _, raw = cls.call('POST', '/api/sources', {'name': '安全测试虚构资料', 'kind': 'exam'})
        assert code == 200, raw
        cls.source_id = json.loads(raw)['id']

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        db.close()
        for item in reversed(cls.patches):
            item.stop()
        assert FIXTURE.resolve().is_relative_to((ROOT / 'build').resolve())
        shutil.rmtree(FIXTURE)

    @classmethod
    def call(cls, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=2)
        data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        try:
            conn.request(method, path, body=data, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        except (http.client.RemoteDisconnected, socket.timeout):
            return 0, {}, b'no valid HTTP response'
        finally:
            conn.close()

    def upload(self, filename, blob):
        code, _, raw = self.call('POST', '/api/sources/' + self.source_id + '/files',
            {'filename': filename, 'content_b64': base64.b64encode(blob).decode()})
        self.assertEqual(code, 200, raw)
        return json.loads(raw)['url']

    def test_path_traversal_and_private_directories(self):
        for path in ('/files/data/private.txt', '/files/sources/../data/private.txt',
                     '/files/%2e%2e/data/private.txt', '/files/sources/%2e%2e/%2e%2e/data/private.txt',
                     '/%2e%2e/data/private.txt', '/files/C:/Windows/win.ini'):
            with self.subTest(path=path):
                code, _, raw = self.call('GET', path)
                self.assertIn(code, (400, 404), raw)
                self.assertNotIn(b'PRIVATE_FIXTURE', raw)
        self.assertEqual(self.call('GET', '/files/assets/a%00.png')[0], 400)

    def test_host_and_duplicate_host_headers(self):
        for host in ('attacker.invalid', '127.0.0.1.attacker.invalid', '127.0.0.1:1',
                     'localhost:1', self.host + '@attacker.invalid'):
            with self.subTest(host=host):
                self.assertEqual(self.call('GET', '/api/meta', headers={'Host': host})[0], 403)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            conn.putrequest('GET', '/api/meta', skip_host=True)
            conn.putheader('Host', self.host)
            conn.putheader('Host', 'attacker.invalid')
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            conn.close()

    def test_cross_site_writes_and_malformed_origins(self):
        before = db.scalar('SELECT count(*) FROM teacher')
        for origin in ('https://attacker.invalid', 'null', 'http://[', 'http://user@' + self.host):
            with self.subTest(origin=origin):
                self.assertEqual(self.call('POST', '/api/teachers', {'name': '不得写入'},
                    headers={'Origin': origin})[0], 403)
        self.assertEqual(self.call('POST', '/api/teachers', {'name': '不得写入'},
            headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(db.scalar('SELECT count(*) FROM teacher'), before)
        self.assertEqual(self.call('POST', '/api/teachers', {'name': '合法本机请求'},
            headers={'Origin': 'http://' + self.host})[0], 200)

    def test_active_uploads_are_downloads_not_application_pages(self):
        for ext in ('html', 'svg', 'js', 'xml'):
            with self.subTest(ext=ext):
                url = self.upload('fixture.' + ext, ('<script>/* inert ' + ext + ' fixture */</script>').encode())
                code, headers, _ = self.call('GET', url)
                self.assertEqual(code, 200)
                self.assertEqual(headers.get('Content-Type'), 'application/octet-stream')
                self.assertIn('attachment;', headers.get('Content-Disposition', ''))
                self.assertIn('sandbox', headers.get('Content-Security-Policy', ''))
                self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(self.call('POST', url, {})[0], 405)

    def test_pdf_images_and_response_headers_remain_usable(self):
        for name, raw, expected in (('fixture.pdf', b'%PDF-1.4 fixture', 'application/pdf'),
                                    ('fixture.png', b'\x89PNG\r\n\x1a\nfixture', 'image/png')):
            code, headers, body = self.call('GET', self.upload(name, raw))
            self.assertEqual(code, 200)
            self.assertEqual(body, raw)
            self.assertEqual(headers['Content-Type'], expected)
            self.assertNotIn('attachment', headers.get('Content-Disposition', ''))
            self.assertEqual(headers.get('X-Content-Type-Options'), 'nosniff')
        code, headers, _ = self.call('GET', '/')
        self.assertEqual(code, 200)
        self.assertEqual(headers.get('X-Frame-Options'), 'SAMEORIGIN')
        self.assertNotIn('Python/', headers.get('Server', ''))

    def test_internal_errors_do_not_disclose_exception_or_paths(self):
        code, _, raw = self.call('GET', '/api/_security_error')
        self.assertEqual(code, 500)
        self.assertNotIn(b'INTERNAL_FIXTURE', raw)
        self.assertNotIn(str(FIXTURE).encode(), raw)
        self.assertNotIn(b'RuntimeError', raw)
        self.assertEqual(json.loads(raw)['error']['code'], 'internal')

    def test_invalid_json_is_rejected_before_writes(self):
        before = db.scalar('SELECT count(*) FROM teacher')
        depth = sys.getrecursionlimit() + 200
        for raw in (b'[]', b'null', b'{', b'\xff', b'{"name":NaN}', b'{"name":' + b'[' * depth + b'0' + b']' * depth + b'}'):
            with self.subTest(length=len(raw)):
                self.assertEqual(self.call('POST', '/api/teachers', raw=raw)[0], 400)
        self.assertEqual(db.scalar('SELECT count(*) FROM teacher'), before)

    def test_request_size_and_truncated_body_are_rejected(self):
        for headers in ({'Content-Length': str(4 * 1024 * 1024 + 1)},
                        {'Content-Length': '-1'}, {'Transfer-Encoding': 'chunked'}):
            self.assertEqual(self.call('POST', '/api/teachers', raw=b'', headers=headers)[0], 413)
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            conn.request('POST', '/api/teachers', body=b'{', headers={'Content-Length': '20'})
            conn.sock.shutdown(socket.SHUT_WR)
            response = conn.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            conn.close()

    def test_stalled_body_times_out(self):
        code, _, _ = self.call('POST', '/api/teachers', raw=b'{', headers={'Content-Length': '20'})
        self.assertEqual(code, 408)

    def test_zip_traversal_and_malformed_archives_are_rejected(self):
        for name in ('../outside', 'app/../../outside', 'C:/outside', 'app/CON.py',
                     'app/name.', 'app/a:stream', 'data/private/key.dpapi'):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as z:
                z.writestr(name, 'fixture')
            self.assertEqual(self.call('POST', '/api/backups/import', raw=output.getvalue())[0], 400)
        self.assertEqual(self.call('POST', '/api/backups/import', raw=b'not a zip')[0], 400)
        self.assertFalse((FIXTURE.parent / 'outside').exists())

    def test_sql_metacharacters_are_data(self):
        name = "fixture' OR 1=1; DROP TABLE teacher;--"
        code, _, _ = self.call('POST', '/api/teachers', {'name': name})
        self.assertEqual(code, 200)
        self.assertTrue(db.one('SELECT id FROM teacher WHERE name=?', (name,)))
        path = '/api/audiences?keyword=' + urllib.parse.quote(name)
        self.assertEqual(json.loads(self.call('GET', path)[2])['items'], [])
        self.assertIsNotNone(db.scalar('SELECT count(*) FROM teacher'))


if __name__ == '__main__':
    unittest.main()
