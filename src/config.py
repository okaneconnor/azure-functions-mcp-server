"""Pydantic Settings — validated config loaded from env vars / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Azure DevOps
    azure_devops_org: str
    azure_devops_projects: str = ""  # comma-separated list from env var
    azure_devops_project: str | None = None  # backwards-compatible default (first project)

    azure_mi_client_id: str | None = None

    log_level: str = "INFO"

    api_retry_attempts: int = 3
    api_retry_delay_seconds: float = 2.0
    api_timeout_seconds: float = 30.0

    rate_limit_max_requests: int = 30
    rate_limit_window_seconds: float = 60.0

    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_cooldown_seconds: float = 60.0

    @property
    def allowed_projects(self) -> list[str]:
        """Parse the comma-separated project list into a validated list."""
        projects = [p.strip() for p in self.azure_devops_projects.split(",") if p.strip()]
        if not projects and self.azure_devops_project:
            projects = [self.azure_devops_project]
        return projects

    @property
    def default_project(self) -> str | None:
        """Return the default project (first in list, or explicit single project)."""
        if self.azure_devops_project:
            return self.azure_devops_project
        projects = self.allowed_projects
        return projects[0] if projects else None


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
