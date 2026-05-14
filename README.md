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
- internal gateway session endpoints for Collector backend integration
- test coverage for the health, auth, and internal session endpoints

Planned scope:

- connect to the OpenSVC Collector MCP server with the same user credentials
- expose controlled backend endpoints for MCP access
- move from in-memory sessions to Redis or another shared store for production

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


## Internal Sessions

Collector can create a short-lived gateway session after a successful login.
This endpoint is intended for backend-to-backend calls only and requires a shared
internal token.

Required environment:

```bash
export OPENSVC_GATEWAY_INTERNAL_TOKEN=change-me
```

Create a gateway session:

```bash
curl -X POST http://127.0.0.1:8010/internal/v1/sessions \
  -H 'X-OpenSVC-Gateway-Token: change-me' \
  -H 'Content-Type: application/json' \
  -d '{"username":"user","password":"password","ttl_seconds":1800}'
```

Expected response:

```json
{"session_id":"...","username":"user","expires_at":"..."}
```

The optional `ttl_seconds` field lets the Collector align the gateway session
expiration with its own web session expiration.

Delete a gateway session:

```bash
curl -X DELETE http://127.0.0.1:8010/internal/v1/sessions/<session_id> \
  -H 'X-OpenSVC-Gateway-Token: change-me'
```

## MCP Client

The gateway can call the Collector MCP server with credentials recovered from a
gateway session. The MCP server still validates Basic Auth itself.

Optional environment:

```bash
export OPENSVC_MCP_URL=http://127.0.0.1:8001/mcp
export OPENSVC_MCP_REQUEST_TIMEOUT_SECONDS=10
```

List the MCP tools visible through the gateway session:

```bash
curl http://127.0.0.1:8010/api/v1/mcp/tools \
  -H 'X-OpenSVC-AI-Session: <session_id>'
```

Search the underlying Collector MCP tool catalog:

```bash
curl -X POST http://127.0.0.1:8010/api/v1/mcp/tools/search \
  -H 'X-OpenSVC-AI-Session: <session_id>' \
  -H 'Content-Type: application/json' \
  -d '{"query":"node inventory statistics summary distribution"}'
```

## Tests

```bash
./venv/bin/python -m pytest
```
