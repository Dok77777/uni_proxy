FROM python:3.11-slim
WORKDIR /app
COPY universal_proxy.py /app/
EXPOSE 8080
ENV PORT=8080
# Мульти-target: хосты, имена, схемы, языки и ключи (через запятую).
# Имена таргетов используются для маршрутизации (заголовок X-Target или префикс пути).
# ВАЖНО: API-ключи НЕ хранятся в этом файле (репозиторий публичный).
# Задайте их как переменные окружения / secrets в панели Koyeb.
ENV TARGET_HOSTS="api.groq.com,api.openai.com"
ENV TARGET_NAMES="groq,openai"
ENV TARGET_SCHEMES="https,https"
ENV FORCE_LANGS="ru,"
ENV API_KEYS=""
# ROUTE_KEY="1" — маршрутизация по API-ключу (ключи должны быть уникальны)
ENV ROUTE_KEY="1"
CMD ["python3", "-u", "universal_proxy.py"]
