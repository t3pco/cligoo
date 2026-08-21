# Use official lightweight Python image
FROM python:3.12-slim-bookworm

# Metadata labels
LABEL org.opencontainers.image.title="cligoo" \
      org.opencontainers.image.description="CLI client for Degoo cloud storage" \
      org.opencontainers.image.licenses="MIT"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/cligoo

# Install system dependencies & useful tools for backup scripts
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    gzip \
    tzdata \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd -g 1000 cligoo && \
    useradd -u 1000 -g cligoo -m -s /bin/bash cligoo

# Setup directories for data, scripts, and configuration
RUN mkdir -p /data /scripts /home/cligoo/.config/cligoo && \
    chown -R cligoo:cligoo /data /scripts /home/cligoo

WORKDIR /app

# Copy application sources
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install cligoo
RUN pip install .

# Copy and configure entrypoint
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root user and default workdir
USER cligoo
WORKDIR /data

# Volume for persisting authentication and config
VOLUME ["/home/cligoo/.config/cligoo"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["daemon"]
