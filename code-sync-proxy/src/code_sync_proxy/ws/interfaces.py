from typing import Protocol, Optional, Tuple, Dict, Any
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


class VerificationRunner(Protocol):
    """Protocol for verification runners."""

    @abstractmethod
    async def run_verification(
        self,
        user_id: Optional[str],
        app_id: str,
        deployment_id: str,
        push_id: str,
        tests_payload: Dict[str, Any],
    ) -> None:
        """
        Run a verification task.

        Args:
            user_id: The ID of the user initiating the verification, if available.
            app_id: The ID of the app being verified.
            deployment_id: The ID of the deployment being verified.
            push_id: The ID of the push operation.
            tests_payload: The test configuration payload.
        """
        ...
