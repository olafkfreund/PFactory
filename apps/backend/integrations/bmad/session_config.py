"""
Session Segmentation Configuration
====================================

Configuration and utilities for BMad Method session segmentation pattern.
Session segmentation runs each story in a fresh subprocess with minimal context
to achieve 50-90% token reduction on large projects.
"""

import json
import os
from pathlib import Path


def is_session_segmentation_enabled() -> bool:
    """Check if session segmentation is enabled globally.

    Returns:
        True if session segmentation is enabled
    """
    # Check environment variable first
    env_value = os.environ.get("BMAD_SESSION_SEGMENTATION", "").lower()
    if env_value in ("1", "true", "yes", "on"):
        return True
    if env_value in ("0", "false", "no", "off"):
        return False

    # Check global config file
    config_file = Path.home() / ".pfactory" / "config.json"
    if config_file.exists():
        try:
            with open(config_file) as f:
                config = json.load(f)
                return config.get("bmad", {}).get("session_segmentation", False)
        except (json.JSONDecodeError, OSError):
            pass

    # Default: disabled (opt-in feature)
    return False


def is_session_segmentation_enabled_for_spec(spec_dir: Path) -> bool:
    """Check if session segmentation is enabled for a specific spec.

    Checks both global setting and spec-specific override.

    Args:
        spec_dir: Path to spec directory

    Returns:
        True if session segmentation is enabled for this spec
    """
    # Check spec-specific override first
    spec_config = spec_dir / "session_config.json"
    if spec_config.exists():
        try:
            with open(spec_config) as f:
                config = json.load(f)
                if "session_segmentation" in config:
                    return config["session_segmentation"]
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to global setting
    return is_session_segmentation_enabled()


def enable_session_segmentation(spec_dir: Path | None = None) -> None:
    """Enable session segmentation globally or for a specific spec.

    Args:
        spec_dir: If provided, enable only for this spec. Otherwise, enable globally.
    """
    if spec_dir:
        # Spec-specific enable
        spec_config = spec_dir / "session_config.json"
        config = {}
        if spec_config.exists():
            try:
                with open(spec_config) as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        config["session_segmentation"] = True

        with open(spec_config, "w") as f:
            json.dump(config, f, indent=2)
    else:
        # Global enable
        config_dir = Path.home() / ".pfactory"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "config.json"

        config = {}
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

        if "bmad" not in config:
            config["bmad"] = {}
        config["bmad"]["session_segmentation"] = True

        with open(config_file, "w") as f:
            json.dump(config, f, indent=2)


def disable_session_segmentation(spec_dir: Path | None = None) -> None:
    """Disable session segmentation globally or for a specific spec.

    Args:
        spec_dir: If provided, disable only for this spec. Otherwise, disable globally.
    """
    if spec_dir:
        # Spec-specific disable
        spec_config = spec_dir / "session_config.json"
        if spec_config.exists():
            try:
                with open(spec_config) as f:
                    config = json.load(f)
                config["session_segmentation"] = False
                with open(spec_config, "w") as f:
                    json.dump(config, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass
    else:
        # Global disable
        config_file = Path.home() / ".pfactory" / "config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                if "bmad" in config:
                    config["bmad"]["session_segmentation"] = False
                    with open(config_file, "w") as f:
                        json.dump(config, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass


def get_session_config(spec_dir: Path) -> dict:
    """Get session configuration for a spec.

    Args:
        spec_dir: Path to spec directory

    Returns:
        Dictionary with session configuration
    """
    return {
        "session_segmentation": is_session_segmentation_enabled_for_spec(spec_dir),
        "global_enabled": is_session_segmentation_enabled(),
    }
