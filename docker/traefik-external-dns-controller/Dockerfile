# Use Python slim image for smaller footprint
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create non-root user for security with proper home directory
RUN groupadd -r appuser && useradd -r -g appuser -m -d /home/appuser appuser

# Install system dependencies
RUN apt-get update && apt-get install -y \
  --no-install-recommends \
  ca-certificates \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY controller.py .

# Set proper permissions
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose health check port
EXPOSE 8080

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV KOPF_LOG_LEVEL=INFO

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8080/healthz || exit 1

# Run the controller
CMD ["python", "controller.py"] 