"""
Log viewing and management routes.

Provides API endpoints to view, search, and manage application logs.
"""


from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from ..logging_config import LOG_DIR, clear_logs, get_log_files, get_recent_logs

router = APIRouter()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class LogEntry(BaseModel):
    """A single log entry."""

    timestamp: str
    level: str
    logger: str
    message: str
    raw: str


class LogsResponse(BaseModel):
    """Response containing log entries."""

    entries: list[LogEntry]
    total: int
    log_type: str
    log_file: str


class LogFilesResponse(BaseModel):
    """Response listing available log files."""

    files: list[dict]
    log_dir: str


class ClearLogsResponse(BaseModel):
    """Response for log clearing operation."""

    success: bool
    message: str


class FrontendLogEntry(BaseModel):
    """A log entry from the frontend."""

    timestamp: str
    level: str
    category: str
    message: str
    data: dict | None = None
    stack: str | None = None


class FrontendLogsRequest(BaseModel):
    """Request containing frontend log entries."""

    entries: list[FrontendLogEntry]


class FrontendLogsResponse(BaseModel):
    """Response for frontend log submission."""

    success: bool
    received: int


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/", response_model=LogFilesResponse)
async def list_log_files():
    """List all available log files with their sizes."""
    log_files = get_log_files()
    files = []

    for name, path in log_files.items():
        file_info = {
            "name": name,
            "filename": path.name if path else "",
            "path": str(path) if path else "",
            "exists": path.exists() if path else False,
            "size": path.stat().st_size if path and path.exists() else 0,
            "size_human": _human_readable_size(path.stat().st_size) if path and path.exists() else "0 B",
        }
        files.append(file_info)

    return LogFilesResponse(
        files=files,
        log_dir=str(LOG_DIR)
    )


@router.post("/frontend", response_model=FrontendLogsResponse)
async def receive_frontend_logs(request: FrontendLogsRequest):
    """
    Receive log entries from the frontend and save to frontend.log.

    Only error logs should be sent from the frontend to minimize overhead.
    """
    log_files = get_log_files()
    frontend_log = log_files.get("frontend")

    if not frontend_log:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Frontend log file not configured"
        )

    # Ensure log directory exists
    frontend_log.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(frontend_log, "a", encoding="utf-8") as f:
            for entry in request.entries:
                # Format: timestamp | LEVEL | category | message | data | stack
                data_str = ""
                if entry.data:
                    import json
                    data_str = f" | {json.dumps(entry.data)}"

                stack_str = ""
                if entry.stack:
                    # Indent stack trace for readability
                    stack_str = f"\n  Stack: {entry.stack.replace(chr(10), chr(10) + '  ')}"

                log_line = f"{entry.timestamp} | {entry.level.upper():<5} | {entry.category} | {entry.message}{data_str}{stack_str}\n"
                f.write(log_line)

        return FrontendLogsResponse(
            success=True,
            received=len(request.entries)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write frontend logs: {e}"
        )


@router.get("/{log_type}", response_model=LogsResponse)
async def get_logs(
    log_type: str,
    lines: int = Query(100, ge=1, le=10000, description="Number of lines to return"),
    level: str | None = Query(None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR)"),
):
    """
    Get recent log entries from a specific log file.

    Args:
        log_type: Type of log (server, errors, agent)
        lines: Number of recent lines to return
        level: Optional filter by log level
    """
    log_files = get_log_files()

    if log_type not in log_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown log type: {log_type}. Available: {list(log_files.keys())}"
        )

    entries = get_recent_logs(log_type, lines, level)

    return LogsResponse(
        entries=[LogEntry(**e) for e in entries],
        total=len(entries),
        log_type=log_type,
        log_file=str(log_files[log_type])
    )


@router.get("/{log_type}/raw", response_class=PlainTextResponse)
async def get_raw_logs(
    log_type: str,
    lines: int = Query(100, ge=1, le=10000, description="Number of lines to return"),
):
    """Get raw log file content as plain text."""
    log_files = get_log_files()

    if log_type not in log_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown log type: {log_type}. Available: {list(log_files.keys())}"
        )

    log_file = log_files[log_type]
    if not log_file.exists():
        return PlainTextResponse("")

    try:
        with open(log_file, encoding="utf-8") as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return PlainTextResponse("".join(recent_lines))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read log file: {e}"
        )


@router.get("/{log_type}/download")
async def download_logs(log_type: str):
    """Download a log file."""
    log_files = get_log_files()

    if log_type not in log_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown log type: {log_type}. Available: {list(log_files.keys())}"
        )

    log_file = log_files[log_type]
    if not log_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Log file does not exist"
        )

    def iter_file():
        with open(log_file, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{log_file.name}"'
        }
    )


@router.delete("/{log_type}", response_model=ClearLogsResponse)
async def clear_log_file(log_type: str):
    """Clear a specific log file."""
    log_files = get_log_files()

    if log_type not in log_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown log type: {log_type}. Available: {list(log_files.keys())}"
        )

    success = clear_logs(log_type)

    if success:
        return ClearLogsResponse(
            success=True,
            message=f"Log file '{log_type}' cleared successfully"
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear log file"
        )


@router.delete("/", response_model=ClearLogsResponse)
async def clear_all_logs():
    """Clear all log files."""
    success = clear_logs()

    if success:
        return ClearLogsResponse(
            success=True,
            message="All log files cleared successfully"
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to clear log files"
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _human_readable_size(size: int) -> str:
    """Convert bytes to human readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
