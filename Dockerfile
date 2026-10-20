# AnotherBPO HR Compliance Suite — production image
FROM python:3.11.9-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HR_DATA_DIR=/data \
    HR_TRUST_PROXY=1

WORKDIR /app

# Unprivileged runtime user; /data is the only writable location.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=root:root . .

USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]
