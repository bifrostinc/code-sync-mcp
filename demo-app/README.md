# Traffic Forking Demo

A simple FastAPI application demonstrating traffic forking capabilities for shadow deployments using Envoy proxy.

## Running Locally

1. Install `uv` if you haven't already:

```bash
pip install uv
```

2. Run it within the uv environment:

```bash
uv run fastapi dev
```

## Running with Docker Compose

1. Start all services:

```bash
docker compose up --build
```

2. Send test requests to Envoy:

```bash
# This will get forked to both services
curl http://localhost:8080/
curl -X POST http://localhost:8080/api/data -H "Content-Type: application/json" -d '{"test": "data"}'
```

## Testing Individual Services

You can also test the services directly:

1. Main service:

```bash
curl http://localhost:8000/api/health
```

2. Shadow service:

```bash
curl http://localhost:8001/api/health
```

## Configuration

The traffic forking configuration is handled by Envoy in `envoy.yaml`:

- Currently configured to fork 10% of POST /api/data requests
- All other requests go to the main service
- Forking is based on weighted routing in Envoy

## Deploying on K8s

```
./manage.sh deploy-image
./manage.sh deploy-helm
```