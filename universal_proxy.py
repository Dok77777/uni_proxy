#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Универсальный транзитный прокси (мульти-target версия + SSE-стриминг).

Позволяет одним контейнером обслуживать НЕСКОЛЬКО целевых сервисов
(Groq, OpenAI, Anthropic, Google и т.д.) одновременно. Полностью
поддерживает стриминг ответов (SSE / chunked transfer), что критично
для AI-сервисов (чаты, транскрипции, генерация текста).

Способы выбора целевого сервиса (по приоритету):
  1. Заголовок X-Target: имя таргетa (например "groq", "openai").
  2. API-ключ в Authorization: маршрутизация по ключу (пользователь с своим ключом).
  3. Префикс пути: /groq/... → groq, /openai/... → openai (путь очищается).
  4. По умолчанию — первый таргет из списка.

Настройки — только через переменные окружения:
  TARGET_HOSTS  — список хостов через запятую (напр. "api.groq.com,api.openai.com")
  TARGET_NAMES  — имена таргетов (напр. "groq,openai"); если пусто, имена = хосты
  TARGET_SCHEMES— схема для каждого (напр. "https,https"); по умолчанию https
  FORCE_LANGS   — принудительный язык для каждого (напр. "ru,ru"); "" = не форсировать
  API_KEYS      — ключи (пересылаются как Authorization Bearer), через запятую
                    Если ключей меньше, чем хостов — недостающие считаются пустыми
  ROUTE_KEY     — если "1", маршрутизация по API-ключу (ключи должны быть уникальны)
  BUFFER_LIMIT  — максимум байт буфера для не-стриминговых ответов (по умолчанию 64 МБ)

Запуск:
    python3 universal_proxy.py [port]
"""
import os
import re
import json
import ssl
import argparse
import http.server
import cgi
import logging
import urllib.request
import urllib.error
import uuid
import io
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("universal_proxy")

# ---- Настройки из окружения ----
_hosts = [h.strip() for h in os.environ.get("TARGET_HOSTS", "api.groq.com").split(",") if h.strip()]
_names = [n.strip() for n in os.environ.get("TARGET_NAMES", "").split(",") if n.strip()]
_schemes = [s.strip().lower() for s in os.environ.get("TARGET_SCHEMES", "https").split(",")]
_langs = [l.strip() for l in os.environ.get("FORCE_LANGS", "ru").split(",")]
_keys = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
ROUTE_KEY = os.environ.get("ROUTE_KEY", "").strip().lower() in ("1", "true", "yes")
BUFFER_LIMIT = int(os.environ.get("BUFFER_LIMIT", str(64 * 1024 * 1024)))

# Нормализуем списки до длины количества хостов
n = len(_hosts)
if not _names:
    _names = [h.split(".")[0] for h in _hosts]
_names = (_names + [""] * n)[:n]
_schemes = (_schemes + ["https"] * n)[:n]
_langs = (_langs + [""] * n)[:n]
_keys = (_keys + [""] * n)[:n]

# Сопоставление: имя -> индекс, ключ -> индекс
NAME_IDX = {}
KEY_IDX = {}
for i, (nm, key) in enumerate(zip(_names, _keys)):
    if nm:
        NAME_IDX[nm.lower()] = i
    if key:
        KEY_IDX[key] = i


def resolve_target(handler):
    """Определяет индекс целевого таргета по запросу."""
    # 1. Заголовок X-Target
    xt = handler.headers.get("X-Target", "").strip().lower()
    if xt:
        if xt in NAME_IDX:
            return NAME_IDX[xt]
        # возможно, передан хост
        for i, h in enumerate(_hosts):
            if h.lower() == xt:
                return i
    # 2. Маршрутизация по API-ключу
    if ROUTE_KEY:
        auth = handler.headers.get("Authorization", "")
        m = re.match(r"(?i)bearer\s+(\S+)", auth)
        if m and m.group(1) in KEY_IDX:
            return KEY_IDX[m.group(1)]
    # 3. Префикс пути: /groq/... → groq
    first = handler.path.lstrip("/").split("/")[0].lower()
    if first in NAME_IDX:
        return NAME_IDX[first]
    if first == "health" or first == "healthz":
        return None
    # 4. По умолчанию — первый
    return 0


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
            self._json(200, {
                "status": "ok",
                "mode": "universal-proxy-multitarget",
                "targets": [
                    {"name": _names[i], "host": _hosts[i], "scheme": _schemes[i], "force_lang": _langs[i]}
                    for i in range(n)
                ],
            })
        else:
            self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    # Опции — просто отвечаем пустым 204
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def _stream_headers(self, status, resp_headers):
        """Отправляет заголовки ответа с потоковым телом (без Content-Length)."""
        self.send_response(status)
        for k, v in resp_headers.items():
            lk = k.lower()
            if lk in ("content-length", "connection", "transfer-encoding", "keep-alive"):
                continue
            self.send_header(k, v)
        # Передаём потоком: либо оригинальный chunked, либо включаем свой
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write_chunk(self, data):
        """Пишет чанк в chunked-формате."""
        if not data:
            return
        self.wfile.write(("%x\r\n" % len(data)).encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _forward(self):
        idx = resolve_target(self)
        if idx is None:
            self._json(404, {"error": {"message": "not found", "type": "invalid_request_error"}})
            return

        host = _hosts[idx]
        scheme = _schemes[idx]
        force_lang = _langs[idx]
        api_key = _keys[idx]

        # Если путь начинается с имени таргета (/groq/...) — срезаем его
        path = self.path
        first = path.lstrip("/").split("/")[0].lower()
        if first in NAME_IDX and NAME_IDX[first] == idx:
            stripped = path.lstrip("/")
            path = "/" + "/".join(stripped.split("/")[1:]) if "/" in stripped else "/"

        url = scheme + "://" + host + path
        log.info("FORWARDING %s -> %s (target=%s)", self.command, url, _names[idx])

        body = None
        cl = self.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > 0:
            body = self.rfile.read(int(cl))

        headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "accept-encoding", "x-target", "transfer-encoding", "te"):
                continue
            headers[k] = v

        # Форсирование языка в multipart (с сохранением реального типа файла)
        ctype = self.headers.get("Content-Type", "")
        if force_lang and body and ctype.startswith("multipart/form-data"):
            try:
                ctype_hdr, pdict = cgi.parse_header(ctype)
                if ctype_hdr == "multipart/form-data" and "boundary" in pdict:
                    b = pdict["boundary"].encode()
                    pdict["boundary"] = b
                    pdict["CONTENT-LENGTH"] = len(body)
                    fields = cgi.parse_multipart(io.BytesIO(body), pdict)
                    fields["language"] = [force_lang.encode()]
                    new_boundary = "----UniversalProxy" + uuid.uuid4().hex
                    payload = {k: (v[0] if isinstance(v, list) else v) for k, v in fields.items()}
                    body = build_multipart(payload, new_boundary)
                    headers["Content-Type"] = "multipart/form-data; boundary=" + new_boundary
                    headers["Content-Length"] = str(len(body))
                    log.info("FORCE_LANG=%s injected into multipart body (target=%s)", force_lang, _names[idx])
            except Exception as e:
                log.warning("Could not force lang: %s", e)

        if api_key:
            headers["Authorization"] = "Bearer " + api_key

        # Просим у апстрима стриминг: отдаём тело по мере поступления
        headers["Accept-Encoding"] = "identity"

        req = urllib.request.Request(url, data=body, method=self.command, headers=headers)
        try:
            ctx = ssl.create_default_context()
            # Если апстрим отдаёт content-type text/event-stream или запрос
            # содержал Accept: text/event-stream — стримим вручную.
            want_stream = "text/event-stream" in self.headers.get("Accept", "")

            with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
                upstream_ct = resp.headers.get("Content-Type", "").lower()
                chunked = resp.headers.get("Transfer-Encoding", "").lower() == "chunked"
                stream_response = want_stream or "text/event-stream" in upstream_ct or chunked

                if stream_response:
                    self._stream_headers(resp.status, resp.headers)
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            break
                        self._write_chunk(chunk)
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                else:
                    data = resp.read(BUFFER_LIMIT + 1)
                    if len(data) > BUFFER_LIMIT:
                        raise Exception("upstream response too large")
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() not in ("content-length", "connection", "transfer-encoding", "keep-alive"):
                            self.send_header(k, v)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        except urllib.error.HTTPError as e:
            err_body = e.read()
            log.error("UPSTREAM HTTP error %s (target=%s)", e.code, _names[idx])
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as e:
            log.exception("upstream request failed")
            # Если уже начали стримить — закрываем соединение
            try:
                self._json(502, {"error": {"message": str(e), "type": "bad_gateway"}})
            except Exception:
                pass


def build_multipart(fields, boundary):
    """Собирает multipart/form-data байты из dict {name: (value, ctype)} или {name: value}."""
    lines = []
    for name, value in fields.items():
        lines.append(("--" + boundary).encode())
        content_type = "application/octet-stream"
        raw = value
        if isinstance(value, tuple) and len(value) == 2:
            raw, content_type = value
        if isinstance(raw, bytes):
            filename = "audio.m4a"
            lines.append(('Content-Disposition: form-data; name="%s"; filename="%s"' % (name, filename)).encode())
            lines.append(("Content-Type: " + content_type).encode())
            lines.append(b"")
            lines.append(raw)
        else:
            lines.append(('Content-Disposition: form-data; name="%s"' % name).encode())
            lines.append(b"")
            lines.append(str(raw).encode("utf-8"))
        lines.append(b"")
    lines.append(("--" + boundary + "--").encode())
    lines.append(b"")
    return b"\r\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    default_port = int(os.environ.get("PORT", "8766"))
    ap.add_argument("port", nargs="?", type=int, default=default_port)
    args = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log.info("Universal multi-target proxy on 0.0.0.0:%d", args.port)
    for i in range(n):
        log.info("  target %d: %s -> %s://%s (lang=%s)", i, _names[i], _schemes[i], _hosts[i], _langs[i])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()