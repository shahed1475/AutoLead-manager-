FROM python:3.11-slim

WORKDIR /app

# System deps for Playwright and Selenium (chromium + a version-matched
# chromedriver from the same apt package — avoids the
# SessionNotCreatedException you get from webdriver-manager always fetching
# the latest chromedriver against an older apt-installed Chromium). gcc is
# kept for any Python deps without prebuilt wheels for this base image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
