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

# Run the FastAPI application
CMD ["fastapi", "run", "src/code_sync_proxy/app.py", "--host", "0.0.0.0", "--port", "8000"]
