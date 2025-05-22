FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files
COPY pyproject.toml .

# Install dependencies
RUN uv pip install -e . --system

# Copy application code
COPY . .

# Expose the port
EXPOSE 8000

# Copy the code sync entrypoint script
COPY code-sync-entrypoint.sh /code-sync-entrypoint.sh
RUN chmod +x /code-sync-entrypoint.sh

# Default command does not run the sidecar launcher script
CMD ["fastapi", "run", "app.py", "--host", "0.0.0.0", "--port", "8000"]
