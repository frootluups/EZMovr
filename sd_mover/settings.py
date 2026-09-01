"""Persistent settings stored in a JSON file next to the app."""

import json
from pathlib import Path
from typing import Any, Optional


def _get_settings_path() -> Path:
    """Return the path to settings.json next to the main script."""
    return Path(__file__).parent.parent / "settings.json"


def load_settings() -> dict:
    """Load settings from disk, or return defaults."""
    defaults = {
        "onboarding_done": False,
        "base_folder": str(Path.home() / "Pictures"),
        "default_mode": "all",
        "default_dest_mode": "date",
        "theme": "system",
        "rating_mode": "metadata",
    }

    path = _get_settings_path()
    if not path.exists():
        return defaults

    try:
        with open(path, "r") as f:
            saved = json.load(f)
        # Merge with defaults so new keys are always present
        defaults.update(saved)
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def save_settings(settings: dict) -> None:
    """Write settings to disk."""
    path = _get_settings_path()
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


def get(key: str) -> Any:
    """Get a single setting."""
    return load_settings().get(key)


def set(key: str, value: Any) -> None:
    """Set a single setting and save."""
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
