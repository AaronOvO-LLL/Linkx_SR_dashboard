#!/usr/bin/env python3
# 灵石踏勘 数据服务：托管网页 + 项目/照片共享存储（零依赖，Python3自带库即可运行）
import hmac, os, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(ROOT, 'store')      # 所有踏勘数据存这里
INDEX = os.path.join(ROOT, 'index.html')
ASSETS = os.path.join(ROOT, 'assets')
ACCESS_KEY = os.environ.get('LS_ACCESS_KEY', 'linkx')
AUTH_COOKIE = 'ls_access'
os.makedirs(STORE, exist_ok=True)

def safe(path):
    key = urllib.parse.unquote(path).lstrip('/')
    p = os.path.normpath(os.path.join(STORE, key))
    return p if p.startswith(STORE) else None

def safe_asset(path):
    key = urllib.parse.unquote(path).lstrip('/')
    if not key.startswith('assets/'):
        return None
    p = os.path.normpath(os.path.join(ROOT, key))
    return p if p.startswith(ASSETS) else None

def asset_type(path):
    ext = os.path.splitext(path.lower())[1]
    return {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon',
    }.get(ext, 'application/octet-stream')

class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b'', ctype='text/plain'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, '_set_auth_cookie', False):
            ck = urllib.parse.quote(ACCESS_KEY, safe='')
            self.send_header('Set-Cookie', AUTH_COOKIE + '=' + ck + '; Path=/; Max-Age=2592000; SameSite=Lax')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,PUT,DELETE,HEAD,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _cookie_key(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == AUTH_COOKIE:
                return urllib.parse.unquote(value)
        return ''

    def _has_access(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if self.command in ('GET', 'HEAD') and u.path in ('/', '/index.html') and 'prefix' not in q and q.get('k', [''])[0] != ACCESS_KEY:
            return False
        key = q.get('k', [''])[0] or self._cookie_key()
        ok = bool(ACCESS_KEY) and hmac.compare_digest(key, ACCESS_KEY)
        self._set_auth_cookie = ok and q.get('k', [''])[0] == ACCESS_KEY
        return ok

    def _deny(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if self.command == 'GET' and u.path in ('/', '/index.html') and 'prefix' not in q:
            return self._send(200, b'', 'text/html; charset=utf-8')
        return self._send(403, b'')

    def do_OPTIONS(self):
        self._send(200)

    def do_GET(self):
        if not self._has_access():
            return self._deny()
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if 'prefix' in q:  # 列举对象（模拟COS ListObjects）
            prefix = q['prefix'][0]
            keys = []
            for dp, _, fns in os.walk(STORE):
                for fn in fns:
                    k = os.path.relpath(os.path.join(dp, fn), STORE).replace(os.sep, '/')
                    if k.startswith(prefix):
                        keys.append(k)
            xml = '<?xml version="1.0"?><ListBucketResult>' + ''.join(
                '<Contents><Key>%s</Key></Contents>' % k.replace('&', '&amp;').replace('<', '&lt;')
                for k in sorted(keys)) + '</ListBucketResult>'
            return self._send(200, xml.encode('utf-8'), 'application/xml')
        if u.path in ('/', '/index.html'):
            try:
                with open(INDEX, 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            except OSError:
                return self._send(404, b'index.html missing')
        ap = safe_asset(u.path)
        if ap and os.path.isfile(ap):
            with open(ap, 'rb') as f:
                return self._send(200, f.read(), asset_type(ap))
        p = safe(u.path)
        if p and os.path.isfile(p):
            ctype = 'image/jpeg' if p.endswith('.jpg') else 'application/json'
            with open(p, 'rb') as f:
                return self._send(200, f.read(), ctype)
        return self._send(404, b'not found')

    def do_HEAD(self):
        if not self._has_access():
            return self._deny()
        u = urllib.parse.urlparse(self.path)
        if u.path in ('/', '/index.html'):
            return self._send(200, b'', 'text/html; charset=utf-8')
        ap = safe_asset(u.path)
        if ap and os.path.isfile(ap):
            return self._send(200, b'', asset_type(ap))
        p = safe(u.path)
        if p and os.path.isfile(p):
            ctype = 'image/jpeg' if p.endswith('.jpg') else 'application/json'
            return self._send(200, b'', ctype)
        return self._send(404, b'')

    def do_PUT(self):
        if not self._has_access():
            return self._deny()
        p = safe(urllib.parse.urlparse(self.path).path)
        if not p:
            return self._send(400)
        n = int(self.headers.get('Content-Length', 0))
        if n > 30 * 1024 * 1024:
            return self._send(413)
        body = self.rfile.read(n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(body)
        return self._send(200)

    def do_DELETE(self):
        if not self._has_access():
            return self._deny()
        p = safe(urllib.parse.urlparse(self.path).path)
        if p and os.path.isfile(p):
            os.remove(p)
        return self._send(204)

    def log_message(self, *a):
        pass

if __name__ == '__main__':
    print('灵石踏勘服务已启动，端口80')
    ThreadingHTTPServer(('0.0.0.0', 80), H).serve_forever()
