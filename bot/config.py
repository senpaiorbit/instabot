import json
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "target_username": None,
    "scrape_amount": 10,
    "extra_caption": "Follow for more 🔥",
    "caption_template": "{original}\n\n🎥 via @{username}",
    "upload_mode": "clip",
    "dedup_enabled": True,
    "dedup_mode": "local_plus_profile",
    "rate_limit_seconds": 90,
    "port": 10000,
    "session_path": "session.json",
    "store_path": "/tmp/instabot_dedup.json",
    "tmp_dir": "/tmp/instabot",
    "use_local_cover_fallback": True,
    "log_level": "INFO",
    "profile_check_amount": 12,
    "caption_marker": True
}

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    # 1. Load from file if exists
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                file_cfg = json.load(f)
                cfg.update(file_cfg)
                logger.info(f"Loaded config.json: {file_cfg}")
        except Exception as e:
            logger.warning(f"Failed to load config.json: {e}")
    # 2. Override from env vars (env wins)
    env_map = {
        "TARGET_USERNAME": "target_username",
        "SCRAPE_AMOUNT": "scrape_amount",
        "EXTRA_CAPTION": "extra_caption",
        "CAPTION_TEMPLATE": "caption_template",
        "UPLOAD_MODE": "upload_mode",
        "RATE_LIMIT_SECONDS": "rate_limit_seconds",
        "PORT": "port",
        "SESSION_PATH": "session_path",
        "CUSTOM_COVER": "custom_cover",
    }
    for env_key, cfg_key in env_map.items():
        val = os.getenv(env_key)
        if val is not None and val != "":
            # try int conversion for numeric fields
            if cfg_key in ("scrape_amount", "rate_limit_seconds", "port"):
                try:
                    val = int(val)
                except:
                    pass
            elif cfg_key == "target_username" and val.lower() in ("null", "none", ""):
                val = None
            cfg[cfg_key] = val
    return cfg

def get_config():
    return load_config()
