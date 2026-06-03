FROM python:3.14-slim

ARG SOURCE_COMMIT=unknown
LABEL org.opencontainers.image.revision=${SOURCE_COMMIT}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

VOLUME ["/data"]

# ADMIN_PASSWORD intentionally has no default — the app refuses to start
# without one (see app/main.py). Pass it at runtime via env.
ENV DATABASE_URL=sqlite:////data/notify-proxy.db \
    LOG_LEVEL=INFO \
    ADMIN_USER=admin

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
