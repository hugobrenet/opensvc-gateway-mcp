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
- Basic Auth validation endpoint against the OpenSVC Collector
- test coverage for the health and auth endpoints

Planned scope:

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


## Auth Check

The gateway validates user Basic Auth against the Collector REST API before MCP
access is attempted.

Required environment:

```bash
export OPENSVC_COLLECTOR_API_BASE_URL=https://collector-host/init/rest/api
```

For local Collectors using self-signed TLS certificates:

```bash
export OPENSVC_COLLECTOR_TLS_VERIFY=false
```

Check credentials:

```bash
curl -u user:password http://127.0.0.1:8010/api/v1/auth/check
```

Expected response:

```json
{"authenticated":true,"username":"user"}
```

## Tests

```bash
./venv/bin/python -m pytest
```
