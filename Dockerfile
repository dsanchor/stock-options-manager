# Stock Options Manager
# Python 3.12 + Node.js for npx-based Playwright MCP server

FROM python:3.12-slim AS base

# System deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
        # Chromium runtime deps
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
        libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) via NodeSource — required for npx @playwright/mcp@latest
RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-install Playwright MCP + Chromium browser (avoids cold-start download)
RUN npx @playwright/mcp@latest --help 2>/dev/null; \
    npx playwright install chromium 2>/dev/null || true

# Application source + config
COPY config.yaml run.py run_web.py ./
COPY src/ src/
COPY web/ web/

# Mount-point directories (volumes override these at runtime)
RUN mkdir -p data logs

EXPOSE 8000

ENTRYPOINT ["python", "run.py"]
