# Universal Proxy (uni_proxy)

Прозрачный транзитный прокси для обхода блокировок (Groq, OpenAI, Anthropic, Google и др.).
Принимает ЛЮБОЙ запрос и пересылает его на целевой API без изменений пути и заголовков.
Поддерживает принудительную подстановку `language=(ru` в multipart-тело (для STT-распознавания).

## Возможности
- **Универсальность**: работает с любым API (Groq, OpenAI, Anthropic, Google и т.д.)
- **Обход блокировок**: т.к. точка выхода — сервер в Нидерландах, 403 больше не будет
- **FORCE_LANG**: принудительно подставляет язык в multipart (например, `ru` для русского)
- **Максимально лёгкий**: только стандартная библиотека Python, никаких зависимостей

## Переменные окружения
| Переменная | Описание | По умолчанию |
|---|---|---|
| `TARGET_HOST` | целевой хост API | `api.groq.com` |
| `TARGET_SCHEME` | схема (http/https) | `https` |
| `GROQ_API_KEY` | API-ключ (пересылается как Bearer) | пусто |
| `FORCE_LANG` | принудительный язык для multipart (`""` = выключено) | `ru` |
| `PORT` | порт (Koyeb обычно ожидает 8080) | `8766` |

## Локальный запуск
```bash
export GROQ_API_KEY="gsk_..."
export FORCE_LANG="ru"
python3 universal_proxy.py 8766
```

## Деплой на Koyeb (бесплатный тариф)
1. Залейте этот репозиторий на GitHub.
2. Koyeb → **Create Service** → **GitHub** → выберите репозиторий.
3. Укажите **регион: Amsterdam (Нидерланды)** — это ключевой пункт, он даёт точку выхода в NL.
4. Порт контейнера: **8766** (или поменяйте `PORT` на 8080; Koyeb часто ожидает 8080).
5. В настройках укажите переменные окружения (особенно `GROQ_API_KEY`).
6. Деплой. Endpoint: `https://<app>.koyeb.app`

## Проверка
```bash
curl https://<app>.koyeb.app/health
```
Вернёт:
```json
{"status":"ok","mode":"universal-proxy","target":"api.groq.com","force_lang":"ru"}
```

## Использование в Operit (STT)
```
https://<app>.koyeb.app/v1/audio/transcriptions
```
Модель: `whisper-large-v3` (подставляется в запросе). Язык форсируется: `language=ru`.

## Для других заблокированных сервисов
Просто поменяйте `TARGET_HOST` (и при необходимости `TARGET_SCHEME`):
| Сервис | TARGET_HOST |
|---|---|
| Groq | `api.groq.com` |
| OpenAI | `api.openai.com` |
| Anthropic | `api.anthropic.com` |
| Google Gemini | `generativelanguage.googleapis.com` |
