# Build context is the repo root (see docker-compose.yml): the backend is a
# Python package (`backend.*`, relative imports), so it must live at
# /app/backend and be launched as `backend.main:app` — `main:app` from inside
# the package dir fails with "attempted relative import with no known parent".
FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright and Selenium (chromium + a version-matched
# chromedriver from the same apt package — avoids the
# SessionNotCreatedException you get from webdriver-manager always fetching
# the latest chromedriver against an older apt-installed Chromium). gcc is
# kept for any Python deps without prebuilt wheels for this base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN playwright install --with-deps chromium

COPY backend ./backend
RUN mkdir -p backend/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
