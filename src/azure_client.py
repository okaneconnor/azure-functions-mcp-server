"""Azure DevOps REST API client.

Tools never touch credentials directly — they call get_devops_client() instead.
All requests are authenticated via bearer tokens from Managed Identity.
"""

import logging
import random
import time

import requests

from src.circuit_breaker import CircuitBreaker
from src.config import get_settings

logger = logging.getLogger(__name__)

_API_VERSION = "7.1"
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class ADOUnavailableError(Exception):
    """Raised when the circuit breaker is open (ADO is considered unavailable)."""


class AzureDevOpsClient:
    """HTTP client for Azure DevOps REST APIs."""

    def __init__(
        self,
        org: str,
        retry_attempts: int = 3,
        retry_delay: float = 2.0,
        timeout: float = 30.0,
    ):
        self._org = org
        self._retry_attempts = retry_attempts
        self._retry_delay = retry_delay
        self._timeout = timeout


    def _build_url(self, path: str, *, project: str, vsrm: bool = False) -> str:
        """Build full API URL.

        Args:
            path: API path after /{project}/ — e.g. "_apis/build/builds"
            project: Azure DevOps project name.
            vsrm: If True, use vsrm.dev.azure.com subdomain (for releases).
        """
        host = "vsrm.dev.azure.com" if vsrm else "dev.azure.com"
        return f"https://{host}/{self._org}/{project}/{path}"


    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        bearer_token: str,
    ) -> dict:
        """Execute an HTTP request with retry logic for transient errors."""
        cb = _get_circuit_breaker()
        if not cb.allow_request():
            raise ADOUnavailableError("Azure DevOps circuit breaker is open")

        if params is None:
            params = {}
        params.setdefault("api-version", _API_VERSION)

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(1, self._retry_attempts + 1):
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=self._timeout,
            )

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._retry_attempts:
                retry_after = float(resp.headers.get("Retry-After", 0))
                if not retry_after:
                    retry_after = self._retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "Retryable %d. Retrying in %.1fs (attempt %d/%d)",
                    resp.status_code,
                    retry_after,
                    attempt,
                    self._retry_attempts,
                )
                time.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                cb.record_failure()
            else:
                cb.record_success()

            resp.raise_for_status()
            return resp.json()

        cb.record_failure()
        resp.raise_for_status()
        return resp.json()

    def get(
        self,
        path: str,
        *,
        project: str,
        params: dict | None = None,
        vsrm: bool = False,
        bearer_token: str,
    ) -> dict:
        """HTTP GET against Azure DevOps REST API."""
        url = self._build_url(path, project=project, vsrm=vsrm)
        return self._request_with_retry(
            "GET", url, params=params, bearer_token=bearer_token
        )

    def post(
        self,
        path: str,
        *,
        project: str,
        json_body: dict | None = None,
        params: dict | None = None,
        vsrm: bool = False,
        bearer_token: str,
    ) -> dict:
        """HTTP POST against Azure DevOps REST API."""
        url = self._build_url(path, project=project, vsrm=vsrm)
        return self._request_with_retry(
            "POST",
            url,
            params=params,
            json_body=json_body,
            bearer_token=bearer_token,
        )

    def get_text(
        self,
        path: str,
        *,
        project: str,
        params: dict | None = None,
        bearer_token: str,
    ) -> str:
        """HTTP GET returning raw text (used for log content), with retry."""
        cb = _get_circuit_breaker()
        if not cb.allow_request():
            raise ADOUnavailableError("Azure DevOps circuit breaker is open")

        url = self._build_url(path, project=project)
        if params is None:
            params = {}
        params.setdefault("api-version", _API_VERSION)

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "text/plain",
        }

        for attempt in range(1, self._retry_attempts + 1):
            resp = requests.get(url, headers=headers, params=params, timeout=self._timeout)

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._retry_attempts:
                retry_after = float(resp.headers.get("Retry-After", 0))
                if not retry_after:
                    retry_after = self._retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "Retryable %d on get_text. Retrying in %.1fs (attempt %d/%d)",
                    resp.status_code,
                    retry_after,
                    attempt,
                    self._retry_attempts,
                )
                time.sleep(retry_after)
                continue

            if resp.status_code >= 500:
                cb.record_failure()
            else:
                cb.record_success()

            resp.raise_for_status()
            return resp.text

        cb.record_failure()
        resp.raise_for_status()
        return resp.text


_circuit_breaker: CircuitBreaker | None = None


def _get_circuit_breaker() -> CircuitBreaker:
    """Lazy singleton — reads thresholds from settings on first call."""
    global _circuit_breaker
    if _circuit_breaker is None:
        s = get_settings()
        _circuit_breaker = CircuitBreaker(
            failure_threshold=s.circuit_breaker_failure_threshold,
            cooldown_seconds=s.circuit_breaker_cooldown_seconds,
        )
    return _circuit_breaker


def get_circuit_breaker_state() -> str:
    """Return circuit breaker state as a string (for health check)."""
    if _circuit_breaker is None:
        return "closed"
    return _circuit_breaker.state.value


_client: AzureDevOpsClient | None = None


def get_devops_client() -> AzureDevOpsClient:
    """Return a module-level cached AzureDevOpsClient (org only, project per-request)."""
    global _client
    if _client is None:
        s = get_settings()
        _client = AzureDevOpsClient(
            org=s.azure_devops_org,
            retry_attempts=s.api_retry_attempts,
            retry_delay=s.api_retry_delay_seconds,
            timeout=s.api_timeout_seconds,
        )
    return _client
