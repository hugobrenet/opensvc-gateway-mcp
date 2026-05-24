FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPENSVC_GATEWAY_HOST=127.0.0.1 \
    OPENSVC_GATEWAY_PORT=8010

WORKDIR /app

RUN groupadd --system opensvc \
    && useradd --system --gid opensvc --home-dir /app --shell /usr/sbin/nologin opensvc

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir .

USER opensvc

EXPOSE 8010

CMD ["opensvc-gateway-mcp"]
