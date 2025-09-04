import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional

class GuildStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = {"channels": {}, "panels": {}}
        self._load()

    def _load(self):
        """Load data from JSON file, with fallback for corruption"""
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    self._data.update(json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: Could not load {self.path}: {e}")

    def _save(self):
        """Atomically save data to JSON file"""
        tmp_path = self.path.with_suffix(".tmp")
        try:
            with open(tmp_path, 'w') as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp_path, self.path)
            # Secure permissions
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as e:
            print(f"Error saving {self.path}: {e}")

    # ----- Transcription Channels -----
    def get_channels(self) -> Dict[str, int]:
        """Get all guild_id -> channel_id mappings"""
        return dict(self._data.get("channels", {}))

    def set_channel(self, guild_id: int, channel_id: int):
        """Set transcription channel for a guild"""
        with self._lock:
            self._data.setdefault("channels", {})[str(guild_id)] = int(channel_id)
            self._save()

    def get_channel(self, guild_id: int) -> Optional[int]:
        """Get transcription channel for a guild"""
        return self._data.get("channels", {}).get(str(guild_id))

    def remove_guild(self, guild_id: int):
        """Remove all data for a guild"""
        with self._lock:
            self._data.get("channels", {}).pop(str(guild_id), None)
            self._data.get("panels", {}).pop(str(guild_id), None)
            self._save()

    # ----- Control Panel Messages (Optional) -----
    def get_panel(self, guild_id: int) -> Optional[int]:
        """Get control panel message ID for a guild"""
        panel_id = self._data.get("panels", {}).get(str(guild_id))
        return int(panel_id) if panel_id is not None else None

    def set_panel(self, guild_id: int, message_id: int):
        """Set control panel message ID for a guild"""
        with self._lock:
            self._data.setdefault("panels", {})[str(guild_id)] = int(message_id)
            self._save()
