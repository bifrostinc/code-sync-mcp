from typing import Protocol, Tuple, Dict, Any
from abc import abstractmethod
from enum import Enum


class PushStatus(str, Enum):
    """Enum for push statuses."""

    PENDING = "pending"
    PUSHING = "pushing"
    PUSHED = "pushed"
    FAILED = "failed"


class PushRepository(Protocol):
    """Protocol for push repositories."""

    @abstractmethod
    def create(
        self,
        **kwargs: Any,
    ) -> Any:
        """Create a new push operation record."""
        ...

    @abstractmethod
    def update(self, push_id: str, **kwargs: Any) -> None:
        """Update the status of a push operation."""
        ...


class DeploymentVerifier(Protocol):
    """Protocol for deployment verifiers."""

    @abstractmethod
    def verify_deployment(
        self,
        app_id: str,
        deployment_id: str,
    ) -> Tuple[bool, str, Dict[str, str]]:
        """
        Verify that an app and deployment exist and are active.

        Returns:
            A tuple containing (is_valid, error_message, log_fields).
            - is_valid: True if the deployment is valid, False otherwise.
            - error_message: A string containing an error message if is_valid is False.
            - log_fields: A dictionary containing fields to include in log messages.
        """
        ...
