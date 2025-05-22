import logging
from typing import Dict, Tuple, Any

from code_sync_proxy.ws.interfaces import PushRepository, DeploymentVerifier, PushStatus
from code_sync_proxy.config import settings

log = logging.getLogger(__name__)


class InMemoryPushRepository(PushRepository):
    """A simple in-memory implementation of PushRepository for standalone mode."""

    def __init__(self):
        self.pushes = {}

    def create(
        self,
        id: str,
        deployment_id: str,
        status: PushStatus,
        code_diff: str,
        change_description: str,
    ) -> Dict[str, Any]:
        """Create a new push operation record in memory."""
        push = {
            "id": id,
            "deployment_id": deployment_id,
            "status": status,
            "code_diff": code_diff,
            "change_description": change_description,
        }
        self.pushes[id] = push
        log.info(f"Created push record {id} with status {status}")
        return push

    def update(self, push_id: str, status: PushStatus) -> None:
        """Update the status of a push operation."""
        if push_id in self.pushes:
            self.pushes[push_id]["status"] = status
            log.info(f"Updated push {push_id} status to {status}")
        else:
            log.warning(f"Attempted to update non-existent push {push_id}")


class AlwaysTrueDeploymentVerifier(DeploymentVerifier):
    """A simple implementation that always confirms deployments as valid."""

    def verify_deployment(
        self,
        app_id: str,
        deployment_id: str,
    ) -> Tuple[bool, str, Dict[str, str]]:
        """
        Always returns valid=True for any deployment.

        Returns:
            (True, "", log_fields): Indicating the deployment is valid.
        """
        log_fields = {
            "module_name": "code_sync_proxy.ws.standalone",
            "app_id": app_id,
            "deployment_id": deployment_id,
            "worker_id": settings.worker_id,
        }
        return True, "", log_fields
