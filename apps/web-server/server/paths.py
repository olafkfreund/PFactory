"""Centralized path helpers for PFactory data directory."""
import shutil
from pathlib import Path

AI_FACTORY_DIR = Path.home() / ".pfactory"


def migrate_legacy_data():
    """Safely migrate legacy PFactory data folder to PFactory."""
    legacy_dir = Path.home() / ".pfactory"
    if legacy_dir.exists() and not AI_FACTORY_DIR.exists():
        try:
            shutil.copytree(legacy_dir, AI_FACTORY_DIR, dirs_exist_ok=True)
            print(f"PFactory - Successfully migrated legacy data from {legacy_dir} to {AI_FACTORY_DIR}")
        except Exception as e:
            print(f"PFactory - Warning: failed to migrate legacy data: {e}")


# Run migration automatically on module load
migrate_legacy_data()


def get_data_dir() -> Path:
    """Return the PFactory data directory, creating it if needed."""
    AI_FACTORY_DIR.mkdir(parents=True, exist_ok=True)
    return AI_FACTORY_DIR


def get_data_file(filename: str) -> Path:
    """Get a file path in the PFactory data directory."""
    return get_data_dir() / filename
