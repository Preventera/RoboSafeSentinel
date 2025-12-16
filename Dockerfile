# ============================================================================
# RoboSafe Sentinel - Dockerfile
# Système de supervision sécurité pour cellules robotisées
# ============================================================================

# Stage 1: Build
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY pyproject.toml .
COPY src/ src/

# Create wheel
RUN pip wheel --no-cache-dir --wheel-dir /wheels -e .

# ============================================================================
# Stage 2: Runtime
FROM python:3.11-slim as runtime

# Labels
LABEL maintainer="Preventera <support@preventera.com>"
LABEL description="RoboSafe Sentinel - Safety Supervision System"
LABEL version="0.1.0"

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy wheels from builder
COPY --from=builder /wheels /wheels

# Install package
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

# Copy application files
COPY src/ /app/src/
COPY config/ /app/config/
COPY data/templates/ /app/data/templates/

# Create non-root user
RUN useradd --create-home --shell /bin/bash robosafe \
    && chown -R robosafe:robosafe /app

USER robosafe

# Create directories for logs and data
RUN mkdir -p /app/logs /app/data/samples

# Expose ports
EXPOSE 9000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# Default command
ENTRYPOINT ["python", "-m", "robosafe.integration"]
CMD ["--simulate", "--port", "9000"]

# ============================================================================
# Usage:
#   docker build -t robosafe-sentinel .
#   docker run -p 9000:9000 robosafe-sentinel
#   docker run -p 9000:9000 robosafe-sentinel --simulate --port 9000
#   docker run -p 9000:9000 -v ./config:/app/config robosafe-sentinel --config /app/config/production.yaml
# ============================================================================
