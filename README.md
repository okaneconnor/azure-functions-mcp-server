# Azure DevOps Pipelines MCP Server

A remote [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that exposes Azure DevOps pipeline operations as AI-callable tools. Built on Azure Functions (Flex Consumption, Python 3.12) with [EasyAuth + Protected Resource Metadata](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp) handling OAuth 2.0 authentication.

Users authenticate via Entra ID. The Function App's Managed Identity authenticates directly to Azure DevOps — no PATs, no shared service accounts, no client secrets.

## Features

- **4 MCP tools** for Azure DevOps Pipelines: list runs, get failure logs, list deployments, trigger runs
- **Multi-project support** — target multiple ADO projects from a single server, validated against a configured allowlist
- **EasyAuth + PRM** — MCP clients discover auth requirements automatically via `/.well-known/oauth-protected-resource`
- **No secrets** — Managed Identity authenticates to ADO; Entra ID handles user auth
- **Group-based access control** — only members of an Entra ID security group can obtain tokens
- **Structured logging** — JSON-formatted logs queryable in Application Insights via `parse_json(message)`
- **Circuit breaker** — automatic fail-fast when ADO is experiencing issues, with graceful degradation
- **Rate limiting** — per-user sliding-window throttling (30 req/user/min default)
- **Infrastructure as Code** — full Terraform deployment (Entra ID, Function App, Storage, Monitoring, ADO identity)

## Architecture

```
MCP Client                     EasyAuth                        Entra ID
|                                |                                |
| GET /.well-known/              |                                |
| oauth-protected-resource       |                                |
|------------------------------->|                                |
|<-------------------------------| (auth metadata + scopes)       |
|                                |                                |
| OAuth 2.0 PKCE flow            |                                |
|---------------------------------------------------------------->|
|<----------------------------------------------------------------|
| (access_token)                 |                                |
|                                |                                |
| POST /runtime/webhooks/mcp     |                                |
| Authorization: Bearer {token}  |                                |
|------------------------------->|                                |
|                        | Validates token                        |
|                        | (audience, issuer, expiry)             |
|                        |                                        |
|                        |--> Function App                        |
|                        |    |--> Rate limiter (per-user)        |
|                        |    |--> Audit log (who, what, when)    |
|                        |    |--> Circuit breaker check          |
|                        |    |--> MI token --> ADO REST API      |
|                        |    |--> Structured result + timing     |
|                        |                                        |
|<-------------------------------| (tool result)                  |
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_pipeline_runs` | List recent pipeline runs with build IDs, statuses, branches, and durations. Filter by pipeline ID, status, or count. |
| `get_run_failure_logs` | Get failure details and log snippets for a failed pipeline run. Returns failed task names, error messages, and log tails. |
| `list_deployments` | List recent Classic Release deployments with status, environment, and timing. |
| `trigger_pipeline_run` | Queue a new pipeline run with optional branch and runtime parameters. |

All tools accept an optional `project` parameter to target a specific Azure DevOps project.

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (`az`) logged in
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5
- [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local) v4
- Python 3.12
- An Azure DevOps organisation (with Project Collection Administrator permissions)
- An Azure subscription with permission to create Entra ID app registrations and Function Apps

## Deploy

### 1. Clone the repository

```bash
git clone https://github.com/okaneconnor/azure-functions-mcp-server.git
cd azure-functions-mcp-server
```

### 2. Configure Terraform variables

Create `infra/terraform.tfvars`:

```hcl
subscription_id       = "your-azure-subscription-id"
azure_devops_org      = "your-devops-org"
azure_devops_projects = ["YourProject"]

# Entra ID object IDs of users who should have access
admin_user_object_ids = ["your-user-object-id"]
```

> You can find your user object ID with: `az ad signed-in-user show --query id -o tsv`

### 3. Deploy infrastructure

```bash
cd infra
terraform init
terraform apply
```

This provisions everything: Entra ID app registration, security group, Managed Identity, ADO entitlement, Function App with EasyAuth, Storage, and Application Insights.

### 4. Deploy function code

```bash
cd ..
func azure functionapp publish <your-function-app-name>
```

> The function app name is shown in the `terraform apply` output. It follows the pattern `azure-devops-pipelines-mcp-func-<suffix>`.

### 5. Verify

```bash
# Health check (excluded from EasyAuth — no token needed)
curl https://<your-function-app-name>.azurewebsites.net/api/health

# MCP endpoint (should return 401 without a token)
curl -s -o /dev/null -w "%{http_code}" https://<your-function-app-name>.azurewebsites.net/runtime/webhooks/mcp

# PRM discovery (should return auth metadata)
curl https://<your-function-app-name>.azurewebsites.net/.well-known/oauth-protected-resource
```

## Connect from VS Code

Add the MCP server to your VS Code settings (`.vscode/mcp.json` or user settings):

```json
{
  "servers": {
    "azure-devops-pipelines": {
      "type": "http",
      "url": "https://<your-function-app-name>.azurewebsites.net/runtime/webhooks/mcp"
    }
  }
}
```

On first use, VS Code opens a browser window for Entra ID sign-in. After that, re-authentication is handled transparently.

> The URL **must** end with `/runtime/webhooks/mcp`. The root URL returns a landing page and the MCP client will hang.

## Local development

No Azure infrastructure needed for local dev. The MI path is bypassed — `AzureCliCredential` authenticates to ADO using your `az login` session.

```bash
# Login to Azure
az login

# Create a .env file
cat > .env << 'EOF'
AZURE_DEVOPS_ORG=your-devops-org
AZURE_DEVOPS_PROJECT=YourProject
AZURE_DEVOPS_PROJECTS=YourProject
EOF

# Install dependencies
pip install -r requirements.txt

# Start the Function App
func start
```

Point your MCP client at `http://localhost:7071/runtime/webhooks/mcp`.

## Project structure

```
.
├── function_app.py              # MCP tool triggers, audit logging, health check
├── host.json                    # Extension bundle + MCP server config
├── requirements.txt             # Python dependencies (pinned)
├── src/
│   ├── azure_client.py          # ADO REST client (retry, circuit breaker)
│   ├── circuit_breaker.py       # Thread-safe circuit breaker (CLOSED/OPEN/HALF_OPEN)
│   ├── config.py                # Pydantic Settings (lazy singleton)
│   ├── logging_config.py        # Structured JSON logging for Application Insights
│   └── rate_limiter.py          # Per-user sliding-window rate limiter
└── infra/
    ├── providers.tf             # azurerm, azuread, azuredevops, random
    ├── variables.tf             # Input variables
    ├── outputs.tf               # Function app name, endpoints
    ├── resource-group.tf        # Resource group
    ├── entra-id.tf              # App registration, service principal, pre-authorized clients, security group
    ├── identity.tf              # User-Assigned MI, ADO entitlement, per-project group membership
    ├── function-app.tf          # Flex Consumption Function App + EasyAuth + PRM
    ├── storage.tf               # Storage account + MI RBAC
    └── monitoring.tf            # Log Analytics + Application Insights
```

## Configuration

All settings have sensible defaults. Only the ADO connection settings are required:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `subscription_id` | Yes | — | Azure subscription ID |
| `azure_devops_org` | Yes | — | ADO organisation name |
| `azure_devops_projects` | Yes | — | List of ADO project names |
| `admin_user_object_ids` | No | `[]` | Entra ID user object IDs for access |
| `project_name` | No | `azure-devops-pipelines-mcp` | Base name for resources |
| `location` | No | `uksouth` | Azure region |
| `maximum_instance_count` | No | `40` | Flex Consumption scale-out limit |
| `instance_memory_mb` | No | `2048` | Memory per instance (MB) |

Runtime settings are configured via app settings on the Function App (with defaults that work out of the box):

| Setting | Default | Description |
|---------|---------|-------------|
| `API_RETRY_ATTEMPTS` | `3` | ADO API retry count |
| `API_TIMEOUT_SECONDS` | `30.0` | ADO API request timeout |
| `RATE_LIMIT_MAX_REQUESTS` | `30` | Requests per user per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60.0` | Rate limit window |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Server errors before circuit opens |
| `CIRCUIT_BREAKER_COOLDOWN_SECONDS` | `60.0` | Seconds before half-open probe |

## Reference docs

- [MCP binding extension for Azure Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-bindings-mcp)
- [Configure built-in MCP server authorization](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp)
- [Secure MCP servers with Entra authentication (VS Code)](https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp-server-vscode)
- [Tutorial: Host an MCP server on Azure Functions](https://learn.microsoft.com/en-us/azure/azure-functions/functions-mcp-tutorial)
