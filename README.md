# Universal Proxy (uni_proxy)

Прозрачный транзитный прокси для обхода блокировок (Groq, OpenAI, Anthropic, Google и др.).
**Мульти-target версия**: один контейнер обслуживает несколько сервисов одновременно.

Принимает ЛЮБОЙ запрос и пересылает его на целевой API без изменений пути и заголовков.
Поддерживает принудительную подстановку `language=ru` в multipart-тело (для STT-распознавания).

## Возможности
- **Универсальность**: работает с любым API (Groq, OpenAI, Anthropic, Google и т.д.)
- **Мульти-target**: несколько сервисов в одном прокси (выбор по заголовку / пути / API-ключу)
- **Обход блокировок**: т.к. точка выхода — сервер в Нидерландах, 403 больше не будет
- **FORCE_LANG**: принудительно подставляет язык в multipart (например, `ru` для русского)
- **Максимально лёгкий**: только стандартная библиотека Python, никаких зависимостей

## Маршрутизация (как выбрать целевой сервис)
Приоритет выбора:
1. Заголовок `X-Target` (например, `X-Target: openai`).
2. API-ключ в `Authorization` (если `ROUTE_KEY=1` и ключи уникальны).
3. Префикс пути (например, `/openai/chat/completions` → api.openai.com).
4. По умолчанию — первый таргет.

## Переменные окружения
| Переменная | Описание | По умолчанию |
|---|---|---|
| `TARGET_HOSTS` | хосты через запятую | `api.groq.com` |
| `TARGET_NAMES` | имена таргетов (для маршрутизации) | = первая часть хоста |
| `TARGET_SCHEMES` | схемы для каждого (http/https) | `https` |
| `FORCE_LANGS` | язык для каждого (`""` = выключено) | `ru` |
| `API_KEYS` | ключи через запятую (пересылаются как Bearer) | пусто |
| `ROUTE_KEY` | маршрутизация по API-ключу (`1`) | пусто |
| `PORT` | порт (Koyeb ожидает 8080) | `8766` |

### Пример (Groq + OpenAI)
```bash
export TARGET_HOSTS="api.groq.com,api.openai.com"
export TARGET_NAMES="groq,openai"
export TARGET_SCHEMES="https,https"
export FORCE_LANGS="ru,"
export API_KEYS="gsk_...,sk-..."
export ROUTE_KEY="1"
python3 universal_proxy.py 8766
```

## Локальный запуск
```bash
export TARGET_HOSTS="api.groq.com"
export API_KEYS="gsk_..."
export FORCE_LANGS="ru"
python3 universal_proxy.py 8766
```

## Деплой на Koyeb (бесплатный тариф)
1. Залейте этот репозиторий на GitHub.
2. Koyeb → **Create Service** → **GitHub** → выберите репозиторий.
3. Укажите **регион: Amsterdam (Нидерланды)** — это ключевой пункт, он даёт точку выхода в NL.
4. Порт контейнера: **8766** (или поменяйте `PORT` на 8080; Koyeb часто ожидает 8080).
5. В настройках укажите переменные окружения (особенно `API_KEYS`).
6. Деплой. Endpoint: `https://<app>.koyeb.app`

## Проверка
```bash
curl https://<app>.koyeb.app/health
```
Вернёт:
```json
{"status":"ok","mode":"universal-proxy-multitarget","targets":[{"name":"groq","host":"api.groq.com","scheme":"https","force_lang":"ru"},{"name":"openai","host":"api.openai.com","scheme":"https","force_lang":""}]}
```

## Использование в Operit (STT)
```
https://<app>.koyeb.app/v1/audio/transcriptions
```
Модель: `whisper-large-v3` (подставляется в запросе). Язык форсируется: `language=ru`.

## Для других заблокированных сервисов
Просто добавьте хост и имя в списки `TARGET_HOSTS` / `TARGET_NAMES`:
| Сервис | TARGET_HOST | name |
|---|---|---|
| Groq | `api.groq.com` | `groq` |
| OpenAI | `api.openai.com` | `openai` |
| Anthropic | `api.anthropic.com` | `anthropic` |
| Google Gemini | `generativelanguage.googleapis.com` | `gemini` |
