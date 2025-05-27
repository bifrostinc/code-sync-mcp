"""
Tests for the MCP server tools
"""

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

import pytest
import pytest_asyncio

from code_sync_mcp.server import push_changes


@dataclass
class MockContext:
    """Mock context for MCP tools"""

    class RequestContext:
        class LifespanContext:
            def __init__(self, client_manager):
                self.client_manager = client_manager

        def __init__(self, client_manager):
            self.lifespan_context = self.LifespanContext(client_manager)

    def __init__(self, client_manager):
        self.request_context = self.RequestContext(client_manager)


@pytest_asyncio.fixture
def mock_client_manager():
    """Mock client manager with mock client"""
    mock_client = AsyncMock()
    mock_client.push = AsyncMock()
    mock_client.verify = AsyncMock()

    mock_manager = AsyncMock()
    mock_manager.get_or_create = AsyncMock(return_value=mock_client)

    return mock_manager, mock_client


@pytest.mark.asyncio
async def test_push_changes_with_uncommitted_changes(mock_client_manager):
    """Test push_changes with uncommitted git changes"""
    mock_manager, mock_client = mock_client_manager
    ctx = MockContext(mock_manager)

    # Mock git diff with uncommitted changes
    mock_result = MagicMock()
    mock_result.stdout = "diff --git a/file.py b/file.py\n+new line"
    mock_result.returncode = 0

    # Mock client push result
    mock_push_result = MagicMock()
    mock_push_result.status = "success"
    mock_push_result.push_id = "push-123"
    mock_client.push.return_value = mock_push_result

    with patch("subprocess.run", return_value=mock_result) as mock_subprocess:
        result = await push_changes(
            ctx=ctx,
            app_id="app-123",
            deployment_id="deploy-456",
            app_root="/path/to/app",
            change_description="Test changes",
        )

    # Verify git diff was called correctly
    mock_subprocess.assert_called_once_with(
        ["git", "diff", "HEAD"],
        cwd="/path/to/app",
        capture_output=True,
        text=True,
        check=True,
    )

    # Verify client was created and push was called
    mock_manager.get_or_create.assert_called_once_with(
        "app-123", "/path/to/app", "deploy-456"
    )
    mock_client.push.assert_called_once_with(
        "diff --git a/file.py b/file.py\n+new line", "Test changes"
    )

    # Verify result
    assert "Pushed code to deployment deploy-456 for app-123" in result
    assert "status: success" in result
    assert "push_id: push-123" in result


@pytest.mark.asyncio
async def test_push_changes_with_committed_changes_only(mock_client_manager):
    """Test push_changes when no uncommitted changes, falls back to last commit"""
    mock_manager, mock_client = mock_client_manager
    ctx = MockContext(mock_manager)

    # Mock git diff - first call returns empty (no uncommitted), second returns commit diff
    mock_results = [
        MagicMock(stdout="", returncode=0),  # No uncommitted changes
        MagicMock(
            stdout="diff --git a/file.py b/file.py\n+committed line", returncode=0
        ),  # Last commit
    ]

    mock_push_result = MagicMock()
    mock_push_result.status = "success"
    mock_push_result.push_id = "push-456"
    mock_client.push.return_value = mock_push_result

    with patch("subprocess.run", side_effect=mock_results) as mock_subprocess:
        result = await push_changes(
            ctx=ctx,
            app_id="app-123",
            deployment_id="deploy-456",
            app_root="/path/to/app",
            change_description="Test changes",
        )

    # Verify both git commands were called
    assert mock_subprocess.call_count == 2
    mock_subprocess.assert_any_call(
        ["git", "diff", "HEAD"],
        cwd="/path/to/app",
        capture_output=True,
        text=True,
        check=True,
    )
    mock_subprocess.assert_any_call(
        ["git", "diff", "HEAD~1", "HEAD"],
        cwd="/path/to/app",
        capture_output=True,
        text=True,
        check=True,
    )

    # Verify push was called with commit diff
    mock_client.push.assert_called_once_with(
        "diff --git a/file.py b/file.py\n+committed line", "Test changes"
    )


@pytest.mark.asyncio
async def test_push_changes_no_changes_found(mock_client_manager):
    """Test push_changes when no git changes are found"""
    mock_manager, mock_client = mock_client_manager
    ctx = MockContext(mock_manager)

    # Mock git diff - both calls return empty
    mock_result = MagicMock(stdout="", returncode=0)

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(ValueError, match="No git changes found to deploy"):
            await push_changes(
                ctx=ctx,
                app_id="app-123",
                deployment_id="deploy-456",
                app_root="/path/to/app",
                change_description="Test changes",
            )


@pytest.mark.asyncio
async def test_push_changes_git_command_fails(mock_client_manager):
    """Test push_changes when git command fails"""
    mock_manager, mock_client = mock_client_manager
    ctx = MockContext(mock_manager)

    # Mock git command failure
    git_error = subprocess.CalledProcessError(
        1, "git diff", stderr="fatal: not a git repository"
    )

    with patch("subprocess.run", side_effect=git_error):
        with pytest.raises(
            ValueError, match="Failed to generate git diff: fatal: not a git repository"
        ):
            await push_changes(
                ctx=ctx,
                app_id="app-123",
                deployment_id="deploy-456",
                app_root="/path/to/app",
                change_description="Test changes",
            )
