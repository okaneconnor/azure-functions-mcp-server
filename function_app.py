"""Azure Functions v2 entry point — Azure DevOps Pipelines MCP tools."""

import json
import logging
import os
import re
import time

import azure.functions as func
import requests

from src.azure_client import ADOUnavailableError, get_circuit_breaker_state, get_devops_client
from src.config import get_settings
from src.logging_config import configure_logging
from src.rate_limiter import RateLimiter

# Do NOT call get_settings() at module level — the Functions host imports this
configure_logging()
logger = logging.getLogger(__name__)

app = func.FunctionApp()

_MAX_LOG_LINES = 200

_DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"  # Azure DevOps application ID

_VALID_BUILD_STATUSES = {"completed", "inProgress", "cancelling", "notStarted", "postponed", "all", "none"}
_VALID_DEPLOYMENT_STATUSES = {"succeeded", "failed", "inProgress", "notDeployed", "partiallySucceeded", "undefined", "all"}

_MAX_BRANCH_LENGTH = 500
_MAX_PARAMETERS_BYTES = 10240  # 10 KB


_credential = None


def _get_devops_token(mi_client_id: str | None) -> str:
    """Get an Azure DevOps bearer token.

    Uses Managed Identity on Azure, or AzureCliCredential for local dev.
    Credential instance is cached — only created on first call.
    """
    global _credential
    if _credential is None:
        if mi_client_id:
            logger.info("Creating DefaultAzureCredential (MI on Azure)")
            from azure.identity import DefaultAzureCredential

            _credential = DefaultAzureCredential(managed_identity_client_id=mi_client_id)
        else:
            logger.info("No MI configured — creating AzureCliCredential (local dev)")
            from azure.identity import AzureCliCredential

            _credential = AzureCliCredential()
    return _credential.get_token(_DEVOPS_SCOPE).token


def _resolve_project(args: dict) -> str:
    """Resolve and validate the project from args or default config."""
    settings = get_settings()
    project = args.get("project") or settings.default_project
    if not project:
        raise ValueError("No project specified and no default project configured")
    allowed = settings.allowed_projects
    if not allowed:
        raise ValueError("No allowed projects configured")
    if project not in allowed:
        raise ValueError(
            f"Project '{project}' is not in the allowed list: {', '.join(allowed)}"
        )
    return project


def _validate_int(value, name: str) -> int:
    """Validate and convert a value to int, raising ValueError on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' must be an integer, got: {value!r}")


def _sanitise_args_for_log(args: dict) -> dict:
    """Return a copy of tool args safe for structured logging.

    Strips the 'parameters' value (may contain sensitive runtime params)
    and replaces it with just the parameter key names.
    """
    safe = {k: v for k, v in args.items() if k != "parameters"}
    if "parameters" in args:
        try:
            safe["parameter_keys"] = list(json.loads(args["parameters"]).keys())
        except (json.JSONDecodeError, TypeError, AttributeError):
            safe["parameter_keys"] = "(invalid)"
    return safe


def _sanitise_error_message(message: str) -> str:
    """Strip URLs that may contain tokens or secrets from error messages."""
    return re.sub(r"https?://\S+", "[URL redacted]", message)


def _error_response(exc) -> dict:
    """Build a structured error dict from an HTTP error."""
    resp = getattr(exc, "response", None)
    status = resp.status_code if resp is not None else None
    try:
        body = resp.json() if resp is not None else {}
    except Exception:
        body = {"text": resp.text[:500] if resp is not None else str(exc)}
    raw_message = body.get("message", str(exc))
    return {
        "error": True,
        "status_code": status,
        "message": _sanitise_error_message(str(raw_message)),
    }


def _format_datetime(value: str | None) -> str | None:
    """Format an ISO datetime string to a human-readable form."""
    if not value:
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return value


def _parse_duration(start: str | None, finish: str | None) -> str | None:
    """Compute a human-readable duration between two ISO timestamps."""
    if not start or not finish:
        return None
    try:
        from datetime import datetime

        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        f = datetime.fromisoformat(finish.replace("Z", "+00:00"))
        delta = f - s
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return None
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
    except (ValueError, AttributeError):
        return None



def _extract_user_identity(ctx: dict) -> dict:
    """Extract authenticated user info from the MCP transport context.

    The MCP extension (stable bundle 4.31.0+) forwards HTTP headers in
    transport.properties.headers.  EasyAuth validates the token but does NOT
    inject X-MS-CLIENT-PRINCIPAL-* headers into the MCP context, so we decode
    the JWT payload directly.  This is safe — EasyAuth already verified the
    token before the function runs.
    """
    import base64

    transport = ctx.get("transport", {})
    headers = transport.get("properties", {}).get("headers", {})
    client_ip = headers.get("X-Forwarded-For", "").split(",")[0].strip() or None

    auth_header = headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            payload_b64 = auth_header[7:].split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        except (IndexError, ValueError, json.JSONDecodeError):
            claims = {}
    else:
        claims = {}

    return {
        "principal_name": claims.get("preferred_username") or claims.get("name"),
        "principal_id": claims.get("oid"),
        "client_ip": client_ip,
    }


def _audit_log(tool_name: str, user: dict, project: str, args: dict) -> None:
    """Emit structured audit log for every tool invocation."""
    logger.info(
        "Tool invocation",
        extra={
            "tool_name": tool_name,
            "user": user.get("principal_name") or "anonymous",
            "principal_id": user.get("principal_id"),
            "client_ip": user.get("client_ip"),
            "project": project,
            "tool_args": _sanitise_args_for_log(args),
            "status": "started",
        },
    )


def _log_tool_result(
    tool_name: str,
    user: dict,
    project: str,
    start_time: float,
    *,
    status: str = "success",
    error_type: str | None = None,
    **result_meta,
) -> None:
    """Emit structured outcome log with timing for a tool invocation."""
    duration_ms = round((time.monotonic() - start_time) * 1000, 1)
    extra = {
        "tool_name": tool_name,
        "user": user.get("principal_name") or "anonymous",
        "principal_id": user.get("principal_id"),
        "project": project,
        "duration_ms": duration_ms,
        "status": status,
        "error_type": error_type,
    }
    extra.update(result_meta)
    logger.info("Tool result", extra=extra)


_rate_limiter: RateLimiter | None = None


def _get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        s = get_settings()
        _rate_limiter = RateLimiter(
            max_requests=s.rate_limit_max_requests,
            window_seconds=s.rate_limit_window_seconds,
        )
    return _rate_limiter


def _check_rate_limit(user: dict) -> str | None:
    """Return an error JSON string if rate-limited, else None."""
    user_key = user.get("principal_id") or user.get("principal_name") or "anonymous"
    if not _get_rate_limiter().check(user_key):
        return json.dumps({"error": True, "message": "Rate limit exceeded. Try again shortly."})
    return None



@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="list_pipeline_runs",
    description="List recent pipeline runs. Returns build IDs, statuses, branches, and durations.",
    toolProperties=json.dumps([
        {"propertyName": "project", "propertyType": "string", "description": "Azure DevOps project name. Defaults to the configured project."},
        {"propertyName": "pipeline_id", "propertyType": "integer", "description": "Filter to a specific pipeline ID. If omitted, returns runs across all pipelines."},
        {"propertyName": "status", "propertyType": "string", "description": "Filter by status: completed, inProgress, cancelling, notStarted."},
        {"propertyName": "top", "propertyType": "integer", "description": "Number of results to return (default 20, max 50)."},
    ]),
)
async def list_pipeline_runs(context: str) -> str:
    _tool_name = "list_pipeline_runs"
    user: dict = {}
    project: str = ""
    _start_time = time.monotonic()
    try:
        ctx = json.loads(context)
        args = ctx.get("arguments", {})

        user = _extract_user_identity(ctx)
        project = _resolve_project(args)
        _audit_log(_tool_name, user, project, args)

        rate_limit_error = _check_rate_limit(user)
        if rate_limit_error:
            _log_tool_result(_tool_name, user, project, _start_time, status="rate_limited")
            return rate_limit_error

        bearer_token = _get_devops_token(os.environ.get("AZURE_MI_CLIENT_ID"))
        client = get_devops_client()

        pipeline_id = args.get("pipeline_id")
        if pipeline_id is not None:
            pipeline_id = _validate_int(pipeline_id, "pipeline_id")

        status = args.get("status")
        if status and status not in _VALID_BUILD_STATUSES:
            return json.dumps({"error": True, "message": f"Invalid status '{status}'. Valid values: {', '.join(sorted(_VALID_BUILD_STATUSES))}"})

        raw_top = args.get("top", 20)
        top = _validate_int(raw_top, "top")
        if top < 1 or top > 50:
            return json.dumps({"error": True, "message": "top must be between 1 and 50"})

        if pipeline_id is not None:
            path = f"_apis/pipelines/{pipeline_id}/runs"
            params = {"$top": str(top)}
            data = client.get(path, project=project, params=params, bearer_token=bearer_token)
            runs = data.get("value", [])
            result = json.dumps(
                {
                    "project": project,
                    "pipeline_id": pipeline_id,
                    "count": len(runs),
                    "runs": [
                        {
                            "run_id": r.get("id"),
                            "name": r.get("name"),
                            "state": r.get("state"),
                            "result": r.get("result"),
                            "created_date": _format_datetime(r.get("createdDate")),
                            "finished_date": _format_datetime(r.get("finishedDate")),
                            "url": r.get("_links", {}).get("web", {}).get("href"),
                        }
                        for r in runs
                    ],
                }
            )
            _log_tool_result(_tool_name, user, project, _start_time, result_count=len(runs))
            return result
        else:
            path = "_apis/build/builds"
            params = {"$top": str(top), "queryOrder": "queueTimeDescending"}
            if status:
                params["statusFilter"] = status
            data = client.get(path, project=project, params=params, bearer_token=bearer_token)
            builds = data.get("value", [])
            result = json.dumps(
                {
                    "project": project,
                    "count": len(builds),
                    "runs": [
                        {
                            "build_id": b.get("id"),
                            "build_number": b.get("buildNumber"),
                            "pipeline_name": b.get("definition", {}).get("name"),
                            "pipeline_id": b.get("definition", {}).get("id"),
                            "status": b.get("status"),
                            "result": b.get("result"),
                            "source_branch": b.get("sourceBranch"),
                            "requested_by": b.get("requestedFor", {}).get("displayName"),
                            "queue_time": _format_datetime(b.get("queueTime")),
                            "finish_time": _format_datetime(b.get("finishTime")),
                            "duration": _parse_duration(b.get("startTime"), b.get("finishTime")),
                            "url": b.get("_links", {}).get("web", {}).get("href"),
                        }
                        for b in builds
                    ],
                }
            )
            _log_tool_result(_tool_name, user, project, _start_time, result_count=len(builds))
            return result
    except ADOUnavailableError:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type="ADOUnavailable")
        return json.dumps({
            "error": True,
            "message": "Azure DevOps is temporarily unavailable. The service may be experiencing issues. Please try again shortly.",
            "retry_after_seconds": 60,
        })
    except (requests.RequestException, ValueError, KeyError) as exc:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type=type(exc).__name__)
        logger.exception("%s failed", _tool_name)
        return json.dumps(_error_response(exc))


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="get_run_failure_logs",
    description="Get failure details and log snippets for a failed pipeline run.",
    toolProperties=json.dumps([
        {"propertyName": "project", "propertyType": "string", "description": "Azure DevOps project name. Defaults to the configured project."},
        {"propertyName": "build_id", "propertyType": "integer", "description": "The build ID to inspect (from list_pipeline_runs)."},
    ]),
)
async def get_run_failure_logs(context: str) -> str:
    _tool_name = "get_run_failure_logs"
    user: dict = {}
    project: str = ""
    _start_time = time.monotonic()
    try:
        ctx = json.loads(context)
        args = ctx.get("arguments", {})

        user = _extract_user_identity(ctx)
        project = _resolve_project(args)
        _audit_log(_tool_name, user, project, args)

        rate_limit_error = _check_rate_limit(user)
        if rate_limit_error:
            _log_tool_result(_tool_name, user, project, _start_time, status="rate_limited")
            return rate_limit_error

        bearer_token = _get_devops_token(os.environ.get("AZURE_MI_CLIENT_ID"))
        client = get_devops_client()

        raw_build_id = args.get("build_id")
        if raw_build_id is None:
            return json.dumps({"error": True, "message": "build_id is required"})
        build_id = _validate_int(raw_build_id, "build_id")

        build = client.get(f"_apis/build/builds/{build_id}", project=project, bearer_token=bearer_token)

        timeline = client.get(f"_apis/build/builds/{build_id}/timeline", project=project, bearer_token=bearer_token)
        records = timeline.get("records", [])

        failed = [
            r
            for r in records
            if r.get("result") == "failed" and r.get("type") in ("Task", "Job", "Phase")
        ]

        failure_details = []
        for record in failed:
            detail = {
                "name": record.get("name"),
                "type": record.get("type"),
                "state": record.get("state"),
                "result": record.get("result"),
                "start_time": _format_datetime(record.get("startTime")),
                "finish_time": _format_datetime(record.get("finishTime")),
                "duration": _parse_duration(record.get("startTime"), record.get("finishTime")),
                "error_count": record.get("errorCount", 0),
                "issues": [
                    {
                        "type": issue.get("type"),
                        "message": issue.get("message"),
                        "category": issue.get("category"),
                    }
                    for issue in (record.get("issues") or [])
                ],
                "log_snippet": None,
            }

            log_ref = record.get("log")
            if log_ref and record.get("type") == "Task":
                log_id = log_ref.get("id")
                if log_id:
                    try:
                        log_text = client.get_text(
                            f"_apis/build/builds/{build_id}/logs/{log_id}",
                            project=project,
                            bearer_token=bearer_token,
                        )
                        lines = log_text.strip().splitlines()
                        detail["log_snippet"] = "\n".join(lines[-_MAX_LOG_LINES:])
                        detail["log_total_lines"] = len(lines)
                    except (requests.RequestException, ADOUnavailableError):
                        detail["log_snippet"] = "(could not fetch log)"

            failure_details.append(detail)

        result = json.dumps(
            {
                "project": project,
                "build_id": build_id,
                "build_number": build.get("buildNumber"),
                "pipeline_name": build.get("definition", {}).get("name"),
                "status": build.get("status"),
                "result": build.get("result"),
                "source_branch": build.get("sourceBranch"),
                "requested_by": build.get("requestedFor", {}).get("displayName"),
                "start_time": _format_datetime(build.get("startTime")),
                "finish_time": _format_datetime(build.get("finishTime")),
                "duration": _parse_duration(build.get("startTime"), build.get("finishTime")),
                "failure_count": len(failed),
                "failures": failure_details,
            }
        )
        _log_tool_result(_tool_name, user, project, _start_time,
                         build_id=build_id, failure_count=len(failed))
        return result
    except ADOUnavailableError:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type="ADOUnavailable")
        return json.dumps({
            "error": True,
            "message": "Azure DevOps is temporarily unavailable. The service may be experiencing issues. Please try again shortly.",
            "retry_after_seconds": 60,
        })
    except (requests.RequestException, ValueError, KeyError) as exc:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type=type(exc).__name__)
        logger.exception("%s failed", _tool_name)
        return json.dumps(_error_response(exc))


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="list_deployments",
    description="List recent release deployments (Classic Releases).",
    toolProperties=json.dumps([
        {"propertyName": "project", "propertyType": "string", "description": "Azure DevOps project name. Defaults to the configured project."},
        {"propertyName": "top", "propertyType": "integer", "description": "Number of results to return (default 20, max 50)."},
        {"propertyName": "deployment_status", "propertyType": "string", "description": "Filter: succeeded, failed, inProgress, notDeployed, etc."},
    ]),
)
async def list_deployments(context: str) -> str:
    _tool_name = "list_deployments"
    user: dict = {}
    project: str = ""
    _start_time = time.monotonic()
    try:
        ctx = json.loads(context)
        args = ctx.get("arguments", {})

        user = _extract_user_identity(ctx)
        project = _resolve_project(args)
        _audit_log(_tool_name, user, project, args)

        rate_limit_error = _check_rate_limit(user)
        if rate_limit_error:
            _log_tool_result(_tool_name, user, project, _start_time, status="rate_limited")
            return rate_limit_error

        bearer_token = _get_devops_token(os.environ.get("AZURE_MI_CLIENT_ID"))
        client = get_devops_client()

        raw_top = args.get("top", 20)
        top = _validate_int(raw_top, "top")
        if top < 1 or top > 50:
            return json.dumps({"error": True, "message": "top must be between 1 and 50"})

        deployment_status = args.get("deployment_status")
        if deployment_status and deployment_status not in _VALID_DEPLOYMENT_STATUSES:
            return json.dumps({"error": True, "message": f"Invalid deployment_status '{deployment_status}'. Valid values: {', '.join(sorted(_VALID_DEPLOYMENT_STATUSES))}"})

        params = {"$top": str(top), "queryOrder": "descending"}
        if deployment_status:
            params["deploymentStatus"] = deployment_status

        data = client.get("_apis/release/deployments", project=project, params=params, vsrm=True, bearer_token=bearer_token)
        deployments = data.get("value", [])

        result = json.dumps(
            {
                "project": project,
                "count": len(deployments),
                "deployments": [
                    {
                        "id": d.get("id"),
                        "release_name": d.get("release", {}).get("name"),
                        "release_id": d.get("release", {}).get("id"),
                        "definition_name": d.get("releaseDefinition", {}).get("name"),
                        "definition_id": d.get("releaseDefinition", {}).get("id"),
                        "environment_name": d.get("releaseEnvironment", {}).get("name"),
                        "deployment_status": d.get("deploymentStatus"),
                        "operation_status": d.get("operationStatus"),
                        "requested_by": d.get("requestedBy", {}).get("displayName"),
                        "queued_on": _format_datetime(d.get("queuedOn")),
                        "started_on": _format_datetime(d.get("startedOn")),
                        "completed_on": _format_datetime(d.get("completedOn")),
                        "duration": _parse_duration(d.get("startedOn"), d.get("completedOn")),
                    }
                    for d in deployments
                ],
            }
        )
        _log_tool_result(_tool_name, user, project, _start_time, result_count=len(deployments))
        return result
    except ADOUnavailableError:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type="ADOUnavailable")
        return json.dumps({
            "error": True,
            "message": "Azure DevOps is temporarily unavailable. The service may be experiencing issues. Please try again shortly.",
            "retry_after_seconds": 60,
        })
    except (requests.RequestException, ValueError, KeyError) as exc:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type=type(exc).__name__)
        logger.exception("%s failed", _tool_name)
        return json.dumps(_error_response(exc))


@app.generic_trigger(
    arg_name="context",
    type="mcpToolTrigger",
    toolName="trigger_pipeline_run",
    description="Queue a new pipeline run.",
    toolProperties=json.dumps([
        {"propertyName": "project", "propertyType": "string", "description": "Azure DevOps project name. Defaults to the configured project."},
        {"propertyName": "pipeline_id", "propertyType": "integer", "description": "The pipeline definition ID to trigger."},
        {"propertyName": "branch", "propertyType": "string", "description": "Source branch to build (e.g. refs/heads/main). Defaults to pipeline default."},
        {"propertyName": "parameters", "propertyType": "string", "description": "JSON string of runtime parameters to pass to the pipeline."},
    ]),
)
async def trigger_pipeline_run(context: str) -> str:
    _tool_name = "trigger_pipeline_run"
    user: dict = {}
    project: str = ""
    _start_time = time.monotonic()
    try:
        ctx = json.loads(context)
        args = ctx.get("arguments", {})

        user = _extract_user_identity(ctx)
        project = _resolve_project(args)
        _audit_log(_tool_name, user, project, args)

        rate_limit_error = _check_rate_limit(user)
        if rate_limit_error:
            _log_tool_result(_tool_name, user, project, _start_time, status="rate_limited")
            return rate_limit_error

        bearer_token = _get_devops_token(os.environ.get("AZURE_MI_CLIENT_ID"))
        client = get_devops_client()

        raw_pipeline_id = args.get("pipeline_id")
        if raw_pipeline_id is None:
            return json.dumps({"error": True, "message": "pipeline_id is required"})
        pipeline_id = _validate_int(raw_pipeline_id, "pipeline_id")

        branch = args.get("branch")
        if branch and len(branch) > _MAX_BRANCH_LENGTH:
            return json.dumps({"error": True, "message": f"branch must be at most {_MAX_BRANCH_LENGTH} characters"})

        parameters_raw = args.get("parameters")

        body: dict = {}
        resources: dict = {"repositories": {"self": {}}}
        if branch:
            resources["repositories"]["self"]["refName"] = branch
        body["resources"] = resources

        if parameters_raw:
            if len(parameters_raw) > _MAX_PARAMETERS_BYTES:
                return json.dumps({"error": True, "message": f"parameters JSON must be at most {_MAX_PARAMETERS_BYTES} bytes"})
            try:
                body["templateParameters"] = json.loads(parameters_raw)
            except (json.JSONDecodeError, TypeError):
                return json.dumps({"error": True, "message": "parameters must be a valid JSON string"})

        api_result = client.post(
            f"_apis/pipelines/{pipeline_id}/runs",
            project=project,
            json_body=body,
            bearer_token=bearer_token,
        )

        result = json.dumps(
            {
                "triggered": True,
                "project": project,
                "run_id": api_result.get("id"),
                "name": api_result.get("name"),
                "state": api_result.get("state"),
                "pipeline_id": api_result.get("pipeline", {}).get("id"),
                "pipeline_name": api_result.get("pipeline", {}).get("name"),
                "created_date": _format_datetime(api_result.get("createdDate")),
                "url": api_result.get("_links", {}).get("web", {}).get("href"),
            }
        )
        _log_tool_result(_tool_name, user, project, _start_time,
                         run_id=api_result.get("id"),
                         pipeline_id=pipeline_id,
                         pipeline_name=api_result.get("pipeline", {}).get("name"))
        return result
    except ADOUnavailableError:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type="ADOUnavailable")
        return json.dumps({
            "error": True,
            "message": "Azure DevOps is temporarily unavailable. The service may be experiencing issues. Please try again shortly.",
            "retry_after_seconds": 60,
        })
    except (requests.RequestException, ValueError, KeyError) as exc:
        _log_tool_result(_tool_name, user, project, _start_time, status="error", error_type=type(exc).__name__)
        logger.exception("%s failed", _tool_name)
        return json.dumps(_error_response(exc))


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Lightweight health check — no get_settings() call, no side effects."""
    return func.HttpResponse(
        json.dumps({
            "status": "healthy",
            "server_name": "azure-devops-pipelines-mcp",
            "circuit_breaker": get_circuit_breaker_state(),
        }),
        status_code=200,
        mimetype="application/json",
    )
