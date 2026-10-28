#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный транзитный прокси.
Принимает ЛЮБОЙ запрос (GET/POST/...), пересылает его на целевой хост
(по умолчанию api.groq.com) БЕЗ изменений пути и заголовков, и возвращает ответ.
Таким образом можно через него слать запросы к любым API, которые заблокированы
в РФ: Groq, OpenAI, Anthropic, Google и т.д.

Фичи:
- FORCE_LANG (опционально): если задан (напр. "ru"), то в multipart-теле
  POST-запроса принудительно подставляется поле language=FORCE_LANG.
- TARGET_HOST / TARGET_SCHEME: какой хост проксировать (по умолчанию Groq).
- Настройки задаются только через переменные окружения (удобно для Docker/Koyeb).

Запуск:
    export TARGET_HOST="api.groq.com"
    export GROQ_API_KEY="gsk_..."   # или любой ключ, который уходит дальше
    export FORCE_LANG="ru"          # опционально
    python3 universal_proxy.py [port]
Например, для OpenAI:
    export TARGET_HOST="api.openai.com"
    export TARGET_SCHEME="https"
    export FORCE_LANG=""
"""
import os
import sys
import json
import ssl
import argparse
import http.server
import cgi
import logging
import urllib.request
import urllib.parse
import uuid
import io

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("universal_proxy")

# ---- Настройки из окружения ----
TARGET_HOST = os.environ.get("TARGET_HOST", "api.groq.com")
TARGET_SCHEME = os.environ.get("TARGET_SCHEME", "https")
FORCE_LANG = os.environ.get("FORCE_LANG", "ru")  # "" = не подставлять
API_KEY = os.environ.get("GROQ_API_KEY", "")  # ключ, который пересылается


def build_multipart(fields, boundary):
    """Собирает multipart/form-data байты из dict {name: value}."""
    lines = []
    for name, value in fields.items():
        lines.append(("--" + boundary).encode())
        if isinstance(value, bytes):
            filename = "audio.m4a"
            lines.append(('Content-Disposition: form-data; name="%s"; filename="%s"' % (name, filename)).encode())
            lines.append(b"Content-Type: application/octet-stream")
            lines.append(b"")
            lines.append(value)
        else:
            lines.append(('Content-Disposition: form-data; name="%s"' % name).encode())
            lines.append(b"")
            lines.append(str(value).encode("utf-8"))
        lines.append(b"")
    lines.append(("--" + boundary + "--").encode())
    lines.append(b"")
    return b"\r\n".join(lines)

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("REQ %s %s", self.command, self.path)

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._json(200, {"status": "ok", "mode": "universal-proxy", "target": TARGET_HOST, "force_lang": FORCE_LANG})
        else:
            self._forward()

    def _forward(self):
        """Пересылает запрос на целевой хост без изменений."""
        # Собираем URL целевого хоста
        url = TARGET_SCHEME + "://" + TARGET_HOST + self.path
        log.info("FORWARDING %s -> %s", self.command, url)
        body = None
        cl = self.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > 0:
            body = self.rfile.read(int(cl))
        # Копируем заголовки, кроме Host и тех, что выставим сами
        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding"):
                continue
            headers[k] = v
        # Особый случай: если это multipart POST и задан FORCE_LANG — форсируем язык
        ctype = self.headers.get("Content-Type", "")
        if FORCE_LANG and body and ctype.startswith("multipart/form-data"):
            try:
                ctype_hdr, pdict = cgi.parse_header(ctype)
                if ctype_hdr == "multipart/form-data" and "boundary" in pdict:
                    b = pdict["boundary"].encode()
                    # Парсим multipart
                    pdict["boundary"] = b
                    pdict["CONTENT-LENGTH"] = len(body)
                    # читаем из bytes через BytesIO
                    fields = cgi.parse_multipart(io.BytesIO(body), pdict)
                    # Принудительно ставим язык
                    fields["language"] = [FORCE_LANG.encode()]
                    # Пересобираем multipart с новым boundary
                    new_boundary = "----UniversalProxy" + uuid.uuid4().hex
                    payload = {k: (v[0] if isinstance(v, list) else v) for k, v in fields.items()}
                    body = build_multipart(payload, new_boundary)
                    headers["Content-Type"] = "multipart/form-data; boundary=" + new_boundary
                    headers["Content-Length"] = str(len(body))
                    log.info("FORCE_LANG=%s injected into multipart body", FORCE_LANG)
            except Exception as e:
                log.warning("Could not force lang: %s", e)
        if API_KEY:
            headers["Authorization"] = "Bearer " + API_KEY
        req = urllib.request.Request(url, data=body, method=self.command, headers=headers)
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                data = resp.read()
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "connection", "transfer-encoding"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            log.error("UPSTREAM HTTP error %s", e.code)
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            log.exception("upstream request failed")
            self._json(502, {"error": {"message": str(e), "type": "bad_gateway"}})

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

def main():
    ap = argparse.ArgumentParser()
    import os as _os
    default_port = int(_os.environ.get("PORT", "8766"))
    ap.add_argument("port", nargs="?", type=int, default=default_port)
    args = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log.info("Universal proxy listening on 0.0.0.0:%d -> %s://%s (force_lang=%s)", args.port, TARGET_SCHEME, TARGET_HOST, FORCE_LANG)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
