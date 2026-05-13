# opensvc-gateway-mcp

FastAPI gateway for securing access to the OpenSVC Collector MCP server.

The gateway is intended to sit between OpenSVC-facing clients and the
`opensvc-collector-mcp` service. Its first responsibility is to centralize access
control before MCP calls are made.


## FastAPI

This project uses FastAPI as the web framework for exposing secured gateway
endpoints over HTTP.

If you are new to FastAPI, start with the official documentation:

- FastAPI: https://fastapi.tiangolo.com

Current scope:

- FastAPI application using a `src/` layout
- health endpoint for service checks
- test coverage for the health endpoint

Planned scope:

- validate user Basic Auth against the OpenSVC Collector
- connect to the OpenSVC Collector MCP server with the same user credentials
- expose controlled backend endpoints for MCP access
- avoid storing user passwords beyond the current request/session boundary

## Run

Use the local virtualenv:

```bash
. ./venv/bin/activate
```

Start the API:

```bash
PYTHONPATH=src uvicorn opensvc_gateway_mcp.main:app --host 127.0.0.1 --port 8010
```

## Health

```bash
curl http://127.0.0.1:8010/health
```

Expected response:

```json
{"status":"ok"}
```

## Tests

```bash
./venv/bin/python -m pytest
```
