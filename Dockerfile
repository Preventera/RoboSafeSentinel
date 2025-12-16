# RoboSafe Sentinel Docker Image
# Multi-stage build for production

# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY pyproject.toml ./
COPY src/ ./src/

# Build wheel
RUN pip install build && python -m build --wheel

# Stage 2: Runtime
FROM python:3.11-slim as runtime

LABEL maintainer="Preventera / GenAISafety <dev@genaisafety.com>"
LABEL description="RoboSafe Sentinel - Industrial Robot Safety System"

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheel from builder
COPY --from=builder /app/dist/*.whl ./

# Install the package
RUN pip install --no-cache-dir *.whl && rm *.whl

# Create non-root user
RUN useradd -m -u 1000 robosafe
USER robosafe

# Create data directories
RUN mkdir -p /app/data/logs /app/config

# Environment
ENV PYTHONUNBUFFERED=1
ENV ROBOSAFE_ENV=production

# Expose ports
EXPOSE 8080 9090

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health')" || exit 1

# Default command
ENTRYPOINT ["python", "-m", "robosafe.main"]
CMD ["--config", "/app/config/config.yaml"]
