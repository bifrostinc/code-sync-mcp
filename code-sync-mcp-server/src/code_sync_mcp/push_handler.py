import asyncio
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
import uuid
import aiofiles
import websockets
from dataclasses import dataclass
from asyncio import Future

from code_sync_mcp.pb import ws_pb2

log = logging.getLogger(__name__)

PushStatusPb = ws_pb2.PushResponse.PushStatus

# Determine RSYNC_PATH
# On macOS, prefer Homebrew's rsync if available, otherwise error.
# On other POSIX systems, use system rsync.
# Other OSes are currently unsupported for rsync.
if sys.platform == "darwin":
    homebrew_rsync_path = "/opt/homebrew/bin/rsync"
    if os.path.exists(homebrew_rsync_path):
        RSYNC_PATH = homebrew_rsync_path
    else:
        # Consistent with original behavior, raise if Homebrew rsync is not found on macOS
        raise RuntimeError(
            "On macOS, rsync from Homebrew is required. "
            "Please install it using `brew install rsync`. "
            f"Checked path: {homebrew_rsync_path}"
        )
elif os.name == "posix":
    RSYNC_PATH = "rsync"  # For other POSIX systems (e.g., Linux)
else:
    raise OSError(
        f"Unsupported OS for rsync operations: {os.name}, platform: {sys.platform}"
    )


class PushFuture(Future):
    def __init__(self, code_diff: str, change_description: str):
        super().__init__()
        self.push_id = str(uuid.uuid4())
        self.code_diff = code_diff
        self.change_description = change_description


@dataclass
class PushResult:
    push_id: str
    status: str


class PushHandler:
    """
    Handles the generation of rsync batch files.
    """

    def __init__(self, rsync_path: str = RSYNC_PATH):
        self.rsync_path = rsync_path
        # Verify that the rsync executable is found at the determined path
        if not shutil.which(self.rsync_path):
            raise FileNotFoundError(
                f"rsync executable not found at '{self.rsync_path}'. "
                "Please ensure rsync is installed and in your PATH, "
                "or the path is correctly specified."
            )

    async def handle_push_request(
        self,
        websocket: websockets.ClientConnection,
        app_root: str,
        push_future: PushFuture,
    ):
        """
        Handles a push request from the client.
        """
        log.info(f"Starting push process for future {id(push_future)}...")

        # Ensure future isn't already done (e.g. cancelled during close)
        if push_future.done():
            log.warning(
                f"Push future {id(push_future)} was already done before processing started."
            )
            return

        # Generate rsync batch data using the new handler
        batch_data = await self.generate_batch(app_root)
        if not batch_data:
            log.info(
                "No changes detected by rsync (empty batch file from generator). Push considered successful."
            )
            if not push_future.done():
                push_future.set_result(PushResult(push_future.push_id, "no-changes"))
            return

        ws_msg = self._create_push_message(push_future, batch_data)
        log.info(f"Sending batch data ({len(batch_data)} bytes)...")
        await websocket.send(ws_msg.SerializeToString())

        log.info("Batch data sent. Waiting for potential ACK/response...")
        response_bytes = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        response_msg = ws_pb2.WebsocketMessage()
        response_msg.ParseFromString(response_bytes)

        msg_type = response_msg.message_type
        log.info(f"Received response from proxy: type={msg_type}")

        if msg_type == ws_pb2.WebsocketMessage.MessageType.PUSH_RESPONSE:
            status = response_msg.push_response.status
            log.info(f"Push status: {PushStatusPb.Name(status)}")
            if status == PushStatusPb.COMPLETED:
                if not push_future.done():
                    push_future.set_result(PushResult(push_future.push_id, "done"))
            else:
                err_msg = f"Push failed with status {PushStatusPb.Name(status)}: {response_msg.push_response.error_message}"
                log.error(err_msg)
                if not push_future.done():
                    push_future.set_exception(RuntimeError(err_msg))
        else:
            err_msg = f"Received unexpected message type {ws_pb2.WebsocketMessage.MessageType.Name(response_msg.message_type)} from proxy instead of PUSH_RESPONSE"
            log.error(err_msg)
            if not push_future.done():
                push_future.set_exception(RuntimeError(err_msg))

    def _create_push_message(
        self, push_future: PushFuture, batch_data: bytes
    ) -> ws_pb2.PushMessage:
        return ws_pb2.WebsocketMessage(
            message_type=ws_pb2.WebsocketMessage.MessageType.PUSH_REQUEST,
            push_message=ws_pb2.PushMessage(
                push_id=push_future.push_id,
                batch_file=batch_data,
                code_diff=push_future.code_diff,
                change_description=push_future.change_description,
            ),
        )

    async def generate_batch(self, app_root: str) -> bytes:
        """
        Generates an rsync batch file by comparing the app_root directory
        with an empty temporary directory.

        Args:
            app_root: The path to the application root directory.

        Returns:
            The rsync batch data as bytes. Returns empty bytes if no changes detected.

        Raises:
            RuntimeError: If the rsync process fails.
        """
        # TemporaryDirectory will manage the creation and cleanup of the base temporary directory.
        with tempfile.TemporaryDirectory() as temp_base_dir_str:
            temp_base_dir = Path(temp_base_dir_str)
            # Define a dummy directory within temp_base_dir to be the target for rsync.
            # rsync needs a target directory to compare against.
            dummy_target_dir = temp_base_dir / "dummy_target_for_rsync"
            os.makedirs(dummy_target_dir)

            # Define the path for the batch file, also within temp_base_dir for automatic cleanup.
            batch_file_path = temp_base_dir / "rsync_batch.bin"

            rsync_cmd = [
                self.rsync_path,
                "-a",  # Archive mode (recursive, preserves symlinks, permissions, times, group, owner)
                "--delete",  # Delete extraneous files from destination directory
                "--checksum",  # Skip based on checksum, not modification time & size
                f"--only-write-batch={batch_file_path}",  # Write batch to this file
                f"{Path(app_root).resolve()}/",  # Source directory (trailing slash is important)
                f"{dummy_target_dir.resolve()}/",  # Destination directory (trailing slash)
                "--include=**.gitignore",  # Ensure .gitignore files themselves are included
                "--exclude=/.git",  # Exclude the .git directory
                "--filter=:- .gitignore",  # Use .gitignore files for exclusion patterns
                "--delete-after",  # Receiver deletes after transfer, not before (safer)
            ]

            log.info(f"Running rsync command: {' '.join(map(str, rsync_cmd))}")
            process = await asyncio.create_subprocess_exec(
                *rsync_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await process.communicate()

            if process.returncode != 0:
                stderr_output = stderr_bytes.decode(errors="replace").strip()
                stdout_output = stdout_bytes.decode(errors="replace").strip()
                err_msg = (
                    f"Rsync process failed (code {process.returncode}): {stderr_output}"
                )
                log.error(err_msg)
                if stdout_output:
                    log.error(f"Rsync stdout: {stdout_output}")
                raise RuntimeError(err_msg)

            log.info(
                f"Rsync batch generation successful. Batch file at: {batch_file_path}"
            )

            # Check if batch file was created and has content
            if (
                not os.path.exists(batch_file_path)
                or os.path.getsize(batch_file_path) == 0
            ):
                log.info(
                    f"Rsync batch file {batch_file_path} is empty or not found. "
                    "This usually means no changes were detected."
                )
                return b""

            async with aiofiles.open(batch_file_path, mode="rb") as bf:
                batch_data = await bf.read()

            return batch_data
