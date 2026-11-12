# e2proxy – arm64 image for the Raspberry Pi (built by .github/workflows/build-deploy.yml).
# Debian bookworm ffmpeg (libx264/libx265/aac) is installed at BUILD time: the old
# compose file ran apt-get on every container start (slow, network-dependent, flaky).
FROM python:3.11-slim-bookworm

RUN apt-get update -qq \
 && apt-get install -y -q --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ARG GIT_SHA=dev
ARG GIT_BRANCH=main
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    E2PROXY_DATA_DIR=/data \
    E2PROXY_BUILD_BRANCH=${GIT_BRANCH} \
    E2PROXY_BUILD_SEQ=${GIT_SHA}

WORKDIR /app
COPY e2proxy/ /app/e2proxy/
COPY selftest.py /app/selftest.py
RUN python3 -m compileall -q /app/e2proxy

EXPOSE 8888
# /api/health answers without touching receivers (a hung box once made the container "unhealthy").
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/health', timeout=5)"

CMD ["python3", "-m", "e2proxy"]
