from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest
import pytest_asyncio

from code_sync_mcp.push_handler import (
    PushHandler,
    RSYNC_PATH as HANDLER_RSYNC_PATH,
)

# Determine the RSYNC_PATH that the RsyncHandler will actually use based on its internal logic.
# This is important for ensuring tests mock the correct path if it differs from a simple 'rsync'.
# The RsyncHandler module itself defines RSYNC_PATH, so we use that.
RSYNC_EXECUTABLE_TO_MOCK = HANDLER_RSYNC_PATH


@pytest_asyncio.fixture
def mock_shutil_which_for_rsync_handler():
    """Mocks shutil.which to always find the rsync executable."""
    with patch("shutil.which", return_value=RSYNC_EXECUTABLE_TO_MOCK) as mock_which:
        yield mock_which


@pytest_asyncio.fixture
async def rsync_handler(mock_shutil_which_for_rsync_handler):
    """Provides an instance of PushHandler."""
    # The RSYNC_PATH is passed to the constructor, which already determined the platform-specific path.
    return PushHandler(rsync_path=RSYNC_EXECUTABLE_TO_MOCK)


@pytest_asyncio.fixture
def mock_subprocess_for_rsync():
    """Fixture to mock asyncio.create_subprocess_exec for rsync tests."""
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"stdout data", b""))

    with patch(
        "asyncio.create_subprocess_exec", return_value=mock_proc
    ) as mock_create_subprocess:
        yield mock_create_subprocess, mock_proc


@pytest_asyncio.fixture
def mock_aiofiles_for_rsync():
    """Mocks aiofiles.open for rsync tests to control batch data content."""
    mock_aiofile_instance = AsyncMock()
    mock_aiofile_instance.read = AsyncMock(return_value=b"fake batch data")

    mock_aiofile_context = AsyncMock()
    mock_aiofile_context.__aenter__.return_value = mock_aiofile_instance

    with patch("aiofiles.open", return_value=mock_aiofile_context) as mock_open:
        yield mock_open, mock_aiofile_instance


@pytest_asyncio.fixture
def mock_os_path_for_rsync():
    """Mocks os.path.exists and os.path.getsize for rsync tests."""
    with patch("os.path.exists", return_value=True) as mock_exists, patch(
        "os.path.getsize", return_value=100
    ) as mock_getsize:
        yield mock_exists, mock_getsize


@pytest.mark.asyncio
async def test_generate_batch_success(
    rsync_handler: PushHandler,
    mock_subprocess_for_rsync,
    mock_aiofiles_for_rsync,
    mock_os_path_for_rsync,
    tmp_path: Path,  # Pytest fixture for temporary directory
):
    """Test successful rsync batch generation."""
    mock_create_subprocess, mock_proc = mock_subprocess_for_rsync
    mock_aiofiles_open, _ = mock_aiofiles_for_rsync
    _, _ = mock_os_path_for_rsync
    app_root_dir = tmp_path / "app_root"
    app_root_dir.mkdir()

    batch_data = await rsync_handler.generate_batch(str(app_root_dir))

    assert batch_data == b"fake batch data"

    # Check that create_subprocess_exec was called
    mock_create_subprocess.assert_awaited_once()
    args, kwargs = mock_create_subprocess.call_args

    # Verify rsync command structure (simplified check, focus on key parts)
    assert args[0] == RSYNC_EXECUTABLE_TO_MOCK
    assert "-a" in args
    assert "--delete" in args
    assert "--checksum" in args
    assert f"{app_root_dir.resolve()}/" in args

    # Verify batch file path was passed to only-write-batch
    # The batch file is created inside a TemporaryDirectory, so its name is dynamic.
    # We check that the argument starts with --only-write-batch=
    batch_arg_found = False
    for arg in args:
        if isinstance(arg, str) and arg.startswith("--only-write-batch="):
            batch_arg_path_str = arg.split("=", 1)[1]
            batch_arg_found = True
            break
    assert batch_arg_found, "--only-write-batch argument not found"

    mock_proc.communicate.assert_awaited_once()
    mock_aiofiles_open.assert_called_once()
    # The path passed to aiofiles.open will be the dynamic batch_file_path
    assert mock_aiofiles_open.call_args[0][0] == Path(batch_arg_path_str)
    assert mock_aiofiles_open.call_args[1]["mode"] == "rb"


@pytest.mark.asyncio
async def test_generate_batch_no_changes(
    rsync_handler: PushHandler,
    mock_subprocess_for_rsync,
    mock_aiofiles_for_rsync,  # mock_aiofiles is used to simulate empty batch file
    tmp_path: Path,
):
    """Test rsync batch generation when no changes are detected (empty batch file)."""
    mock_create_subprocess, mock_proc = mock_subprocess_for_rsync
    mock_aiofiles_open, mock_aiofile_read_instance = mock_aiofiles_for_rsync

    # Simulate rsync creating an empty batch file
    mock_aiofile_read_instance.read = AsyncMock(return_value=b"")

    # Also need to mock os.path.getsize for the empty file check or ensure the mock_aiofiles_open
    # interacts with a mock os.path.exists and os.path.getsize to simulate 0 size.
    # Easier: the PushHandler checks `if not os.path.exists(batch_file_path) or os.path.getsize(batch_file_path) == 0:`
    # Let's patch these os functions.

    app_root_dir = tmp_path / "app_root"
    app_root_dir.mkdir()

    with patch("os.path.exists", return_value=True) as mock_exists, patch(
        "os.path.getsize", return_value=0
    ) as mock_getsize:
        batch_data = await rsync_handler.generate_batch(str(app_root_dir))

    assert batch_data == b""
    mock_create_subprocess.assert_awaited_once()
    mock_proc.communicate.assert_awaited_once()

    # aiofiles.open might not be called if os.path.getsize is 0 before it.
    # The logic is: if not os.path.exists or os.path.getsize == 0 -> return b"".
    # So aiofiles.open shouldn't be called in this path.
    mock_aiofiles_open.assert_not_called()

    # Check that the os.path.exists and os.path.getsize were called for the batch file path
    # This requires knowing the batch file path, which is dynamic.
    # We can capture the batch_file_path from the rsync command args
    rsync_args = mock_create_subprocess.call_args[0]
    batch_file_path_in_rsync_cmd = ""
    for arg in rsync_args:
        if isinstance(arg, str) and arg.startswith("--only-write-batch="):
            batch_file_path_in_rsync_cmd = Path(arg.split("=", 1)[1])
            break
    assert (
        batch_file_path_in_rsync_cmd != ""
    ), "Batch file path not found in rsync command"

    mock_exists.assert_any_call(
        batch_file_path_in_rsync_cmd
    )  # It's called for other paths too by tempfile
    mock_getsize.assert_any_call(batch_file_path_in_rsync_cmd)


@pytest.mark.asyncio
async def test_generate_batch_rsync_fails(
    rsync_handler: PushHandler,
    mock_subprocess_for_rsync,
    mock_aiofiles_for_rsync,
    tmp_path: Path,
):
    """Test rsync batch generation when the rsync command itself fails."""
    mock_create_subprocess, mock_proc = mock_subprocess_for_rsync
    mock_aiofiles_open, _ = mock_aiofiles_for_rsync

    # Simulate rsync command failure
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"rsync error message"))

    app_root_dir = tmp_path / "app_root"
    app_root_dir.mkdir()

    with pytest.raises(
        RuntimeError, match="Rsync process failed \(code 1\): rsync error message"
    ):
        await rsync_handler.generate_batch(str(app_root_dir))

    mock_create_subprocess.assert_awaited_once()
    mock_proc.communicate.assert_awaited_once()
    mock_aiofiles_open.assert_not_called()  # Should not attempt to read batch file if rsync fails


@pytest.mark.asyncio
async def test_rsync_handler_initialization_rsync_not_found():
    """Test PushHandler initialization fails if rsync executable is not found by shutil.which."""
    with patch(
        "shutil.which", return_value=None
    ) as mock_which:  # Simulate rsync not found
        with pytest.raises(
            FileNotFoundError,
            match="rsync executable not found at 'nonexistent_rsync_path'",
        ):
            PushHandler(rsync_path="nonexistent_rsync_path")
        mock_which.assert_called_once_with("nonexistent_rsync_path")
