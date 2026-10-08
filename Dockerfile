# 덴트모아 서버 이미지
#   만들기·시작: sudo bash deploy/install.sh  (또는 deploy/ 폴더에서 docker compose up -d --build)
#
# 데비안 버전(bookworm)을 고정한다 — playwright --with-deps 가 확실히 지원하는 버전.
FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Seoul \
    DENTMOA_DATA=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 한글 글꼴(화면 캡처에서 한글이 깨지지 않게)과 시간대 정보
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/*

# 프로그램을 돌릴 일반 사용자. uid 1000 — install.sh 가 data 폴더 주인을 이 번호로 맞춘다.
RUN groupadd --gid 1000 dentmoa \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin dentmoa \
    && mkdir -p /data /ms-playwright \
    && chown dentmoa:dentmoa /data /ms-playwright

WORKDIR /app

# 파이썬 패키지와 크로미움. 코드보다 먼저 설치해서, 코드만 바뀌면 이 단계는 캐시를 쓴다.
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && chown -R dentmoa:dentmoa /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

COPY dentmoa ./dentmoa

USER dentmoa
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"]

CMD ["python", "-m", "dentmoa", "serve"]
