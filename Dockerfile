FROM python:3.11-slim
WORKDIR /app
COPY universal_proxy.py /app/
EXPOSE 8766
ENV PORT=8766
ENV TARGET_HOST=api.groq.com
ENV TARGET_SCHEME=https
ENV FORCE_LANG=ru
CMD ["python3", "-u", "universal_proxy.py"]
