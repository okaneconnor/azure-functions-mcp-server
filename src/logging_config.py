"""Structured JSON logging for Application Insights.

AppInsights queries: traces | extend p = parse_json(message) | project p.tool_name, p.duration_ms
Safe at module level — does NOT call get_settings().
"""

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON.

    Known fields are extracted from the record's `extra` dict via LogRecord
    attributes. Any field passed via ``extra={}`` that isn't in the allowlist
    is silently dropped.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key in (
            "tool_name", "user", "principal_id", "client_ip", "project",
            "duration_ms", "status", "error_type",
            "tool_args", "run_id", "build_id", "result_count", "failure_count",
            "pipeline_id", "pipeline_name",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info and record.exc_info[1] is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Replace root logger handlers with a JSON-formatted stderr handler."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
