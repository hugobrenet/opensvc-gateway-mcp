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
- Redis-backed gateway sessions for production-style deployments

Planned scope:

- connect to the OpenSVC Collector MCP server with the same user credentials
- expose controlled backend endpoints for MCP access

## Run

Use the local virtualenv:

```bash
. ./venv/bin/activate
```

Start the API:

```bash
PYTHONPATH=src uvicorn opensvc_gateway_mcp.main:app --host 127.0.0.1 --port 8010
```

When using the package entrypoint, bind host and port come from the runtime
configuration:

```bash
OPENSVC_COLLECTOR_API_BASE_URL=http://127.0.0.1:8001/init/rest/api \
opensvc-gateway-mcp
```

## Health

```bash
curl http://127.0.0.1:8010/health
```

Expected response:

```json
{"status":"ok"}
```

## Runtime Configuration

The gateway is designed to run inside the Collector network namespace. In that
deployment mode, keep it bound to loopback and let the Collector frontend remain
the only public HTTPS entrypoint.

Recommended namespace values:

```bash
export OPENSVC_GATEWAY_HOST=127.0.0.1
export OPENSVC_GATEWAY_PORT=8010
export OPENSVC_COLLECTOR_API_BASE_URL=http://127.0.0.1:8001/init/rest/api
export OPENSVC_COLLECTOR_TLS_VERIFY=false
export OPENSVC_GATEWAY_SESSION_STORE=redis
export OPENSVC_GATEWAY_REDIS_URL=redis://127.0.0.1:6379/0
export OPENSVC_MCP_URL=http://127.0.0.1:8011/mcp
export OPENSVC_GATEWAY_INTERNAL_TOKEN=<shared-secret>
```

Variables:

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `OPENSVC_GATEWAY_HOST` | no | `127.0.0.1` | Uvicorn bind host. Keep loopback in the shared Collector namespace. |
| `OPENSVC_GATEWAY_PORT` | no | `8010` | Uvicorn bind port. |
| `OPENSVC_COLLECTOR_API_BASE_URL` | yes | none | Collector REST API base URL used by the gateway. |
| `OPENSVC_COLLECTOR_REQUEST_TIMEOUT_SECONDS` | no | `10.0` | Timeout for Collector REST calls. |
| `OPENSVC_COLLECTOR_TLS_VERIFY` | no | `true` | TLS verification for Collector REST calls. Use `false` only for internal HTTP or lab self-signed TLS. |
| `OPENSVC_COLLECTOR_AI_CONFIG_PATH` | no | `/ai/llm/config` | Collector REST path used to fetch the user's LLM profile. |
| `OPENSVC_GATEWAY_INTERNAL_TOKEN` | required for Collector integration | none | Shared backend secret used by Collector to create/delete gateway sessions and by gateway to fetch LLM config. |
| `OPENSVC_GATEWAY_SESSION_TTL_SECONDS` | no | `1800` | Default gateway session TTL when Collector does not send one. |
| `OPENSVC_GATEWAY_SESSION_STORE` | no | `memory` | Session backend: `memory` for dev, `redis` for production-style runtime. |
| `OPENSVC_GATEWAY_REDIS_URL` | no | `redis://127.0.0.1:6379/0` | Redis URL for gateway session storage. |
| `OPENSVC_GATEWAY_REDIS_KEY_PREFIX` | no | `ai_gateway:session:` | Redis key prefix for gateway sessions. |
| `OPENSVC_MCP_URL` | no | `http://127.0.0.1:8011/mcp` | Collector MCP HTTP endpoint. |
| `OPENSVC_MCP_REQUEST_TIMEOUT_SECONDS` | no | `10.0` | Timeout for MCP calls. |
| `OPENSVC_LLM_REQUEST_TIMEOUT_SECONDS` | no | `60.0` | Timeout for LLM provider calls. |

Legacy aliases without the `OPENSVC_` prefix exist for some variables, but
new deployments should use the names above.


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
export OPENSVC_MCP_URL=http://127.0.0.1:8011/mcp
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

Call one of the Collector MCP tools returned by search:

```bash
curl -X POST http://127.0.0.1:8010/api/v1/mcp/tools/call \
  -H 'X-OpenSVC-AI-Session: <session_id>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"get_nodes_inventory_stats","arguments":{"request":{}}}'
```

## AI Chat Orchestration

The gateway can orchestrate an OpenAI-compatible LLM with the Collector MCP
server. The Collector remains the source of truth for the LLM profile and system
prompt.

Expected flow:

```text
client -> gateway /api/v1/ai/chat
  -> Collector REST: fetch LLM profile and system prompt
  -> MCP tools/list: expose only search_tools and call_tool to the LLM
  -> LLM /chat/completions
  -> MCP tool calls requested by the LLM
  -> LLM final answer
```

Collector AI configuration endpoint:

```bash
export OPENSVC_COLLECTOR_AI_CONFIG_PATH=/ai/llm/config
```

The endpoint is called with the user credentials stored in the gateway session
and should return either the profile directly or under `config`, `llm`, or
`data`:

```json
{
  "provider": "openai_compatible",
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "qwen3:8b",
  "api_key": "ollama",
  "system_prompt": "You are the OpenSVC assistant. Use MCP tools when needed.",
  "temperature": 0.2,
  "max_tool_iterations": 5,
  "tool_result_max_chars": 20000
}
```

Call the orchestrator:

```bash
curl -X POST http://127.0.0.1:8010/api/v1/ai/chat \
  -H 'X-OpenSVC-AI-Session: <session_id>' \
  -H 'Content-Type: application/json' \
  -d '{"message":"How many nodes are down?"}'
```

The LLM only sees the two MCP proxy tools, `search_tools` and `call_tool`. It
must search the tool catalog first, then call a selected Collector tool through
the proxy.

## Tests

```bash
./venv/bin/python -m pytest
```
