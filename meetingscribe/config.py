from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".meetingscribe"
CONFIG_FILE = CONFIG_DIR / "config.json"


OPENROUTER_DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


@dataclass
class Config:
    output_dir: str = "~/MeetingNotes"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = OPENROUTER_DEFAULT_MODEL
    hf_token: str = ""
    whisper_model: str = "base"  # tiny | base | small | medium | large-v3
    use_diarization: bool = True
    audio_device_index: Optional[int] = None  # None = auto-detect
    mic_device_index: Optional[int] = None    # None = disabled
    user_name: str = "Me"                     # Speaker label for mic audio
    chunk_seconds: int = 30

    @property
    def effective_api_key(self) -> str:
        return self.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    @property
    def effective_openrouter_key(self) -> str:
        return self.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")

    @property
    def effective_hf_token(self) -> str:
        return self.hf_token or os.environ.get("HF_TOKEN", "")

    @property
    def resolved_output_dir(self) -> Path:
        return Path(self.output_dir.strip("'\"")).expanduser()


def load_config() -> Config:
    if not CONFIG_FILE.exists():
        return Config()
    try:
        data = json.loads(CONFIG_FILE.read_text())
        known = {f for f in Config.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return Config(**filtered)
    except Exception:
        return Config()


def save_config(config: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(config), indent=2))
    CONFIG_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
