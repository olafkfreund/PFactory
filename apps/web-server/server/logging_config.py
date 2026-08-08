"""
Logging configuration for PFactory Web Server.

Sets up file-based logging with rotation for persistent error tracking and debugging.
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import get_data_dir

# Log directory - stored in user's home directory
LOG_DIR = get_data_dir() / "logs"

# Log file settings
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5  # Keep 5 backup files


def setup_logging(
    log_level: str = "INFO",
    log_dir: Path | None = None,
) -> None:
    """
    Configure logging for the application.

    Sets up:
    - Console logging (always)
    - File logging with rotation (server.log, errors.log)
    - Structured format with timestamps

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Optional custom log directory
    """
    log_dir = log_dir or LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)

    # Log file paths
    server_log = log_dir / "server.log"
    error_log = log_dir / "errors.log"
    agent_log = log_dir / "agent.log"

    # Formatters
    detailed_format = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    simple_format = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(simple_format)
    root_logger.addHandler(console_handler)

    # Server log handler (all logs)
    server_handler = RotatingFileHandler(
        server_log, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    server_handler.setLevel(logging.DEBUG)
    server_handler.setFormatter(detailed_format)
    root_logger.addHandler(server_handler)

    # Error log handler (errors and warnings only)
    error_handler = RotatingFileHandler(
        error_log, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(detailed_format)
    root_logger.addHandler(error_handler)

    # Agent-specific logger
    agent_logger = logging.getLogger("server.services.agent_service")
    agent_handler = RotatingFileHandler(
        agent_log, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    agent_handler.setLevel(logging.DEBUG)
    agent_handler.setFormatter(detailed_format)
    agent_logger.addHandler(agent_handler)

    # Suppress noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Log startup message
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized. Log directory: {log_dir}")
    logger.info("Log files: server.log, errors.log, agent.log")


def get_log_files() -> dict[str, Path]:
    """Get paths to all log files."""
    return {
        "server": LOG_DIR / "server.log",
        "errors": LOG_DIR / "errors.log",
        "agent": LOG_DIR / "agent.log",
        "frontend": LOG_DIR / "frontend.log",
    }


def get_recent_logs(
    log_type: str = "server", lines: int = 100, level_filter: str | None = None
) -> list[dict]:
    """
    Get recent log entries from a log file.

    Args:
        log_type: Type of log file (server, errors, agent)
        lines: Number of recent lines to return
        level_filter: Optional filter by log level

    Returns:
        List of log entries as dicts
    """
    log_files = get_log_files()
    log_file = log_files.get(log_type)

    if not log_file or not log_file.exists():
        return []

    entries = []
    try:
        with open(log_file, encoding="utf-8") as f:
            # Read all lines and get the last N
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

            for line in recent_lines:
                line = line.strip()
                if not line:
                    continue

                # Parse log entry
                entry = parse_log_line(line)
                if entry:
                    if level_filter and entry.get("level") != level_filter.upper():
                        continue
                    entries.append(entry)
    except Exception as e:
        entries.append(
            {
                "timestamp": datetime.now().isoformat(),
                "level": "ERROR",
                "logger": "logging_config",
                "message": f"Failed to read log file: {e}",
                "raw": str(e),
            }
        )

    return entries


def parse_log_line(line: str) -> dict | None:
    """Parse a log line into structured data."""
    try:
        # Format: "2024-01-05 16:49:31 | INFO     | server.main:39 | Message"
        parts = line.split(" | ", 3)
        if len(parts) >= 4:
            timestamp = parts[0].strip()
            level = parts[1].strip()
            logger_info = parts[2].strip()
            message = parts[3].strip()

            return {
                "timestamp": timestamp,
                "level": level,
                "logger": logger_info,
                "message": message,
                "raw": line,
            }
        else:
            # Fallback for lines that don't match the format
            return {"timestamp": "", "level": "INFO", "logger": "", "message": line, "raw": line}
    except Exception:
        return {"timestamp": "", "level": "INFO", "logger": "", "message": line, "raw": line}


def clear_logs(log_type: str | None = None) -> bool:
    """
    Clear log files.

    Args:
        log_type: Specific log type to clear, or None for all

    Returns:
        True if successful
    """
    log_files = get_log_files()

    if log_type:
        files_to_clear = {log_type: log_files.get(log_type)}
    else:
        files_to_clear = log_files

    try:
        for name, path in files_to_clear.items():
            if path and path.exists():
                # Truncate the file instead of deleting to preserve file handles
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
        return True
    except Exception:
        return False
