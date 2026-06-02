# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# System deps for matplotlib (headless rendering — no display needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfreetype6 \
    libpng16-16 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (layer cached separately from source)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/
COPY tests/ ./tests/

# Create data directories (SQLite DB + charts)
RUN mkdir -p /app/data/charts && chown -R appuser:appuser /app/data

# Matplotlib non-interactive backend (must be before any import)
ENV MPLBACKEND=Agg

# Application configuration (override via .env or env vars)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DB_URL=sqlite:////app/data/spike_monitor.db
ENV CHART_OUTPUT_DIR=/app/data/charts

USER appuser

# Default: run the scheduler daemon
CMD ["python", "-m", "app.main"]
