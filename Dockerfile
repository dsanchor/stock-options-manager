# Stock Options Manager
# Python 3.12 + Playwright for TradingView data fetching

FROM python:3.12-slim AS base

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium + OS-level deps via Playwright (--with-deps handles apt packages)
RUN playwright install chromium --with-deps

# Application source + config
COPY config.yaml run.py run_web.py ./
COPY src/ src/
COPY web/ web/

# Mount-point directories (volumes override these at runtime)
RUN mkdir -p data logs

EXPOSE 8000

ENTRYPOINT ["python", "run.py"]
