import json
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def _config_store_path():
    # try to read from config.json
    try:
        cfg_path = Path(__file__).parent.parent / "config.json"
        if cfg_path.exists():
            import json as _j
            with open(cfg_path) as f:
                cfg = _j.load(f)
                if cfg.get("store_path"):
                    return Path(cfg["store_path"])
    except:
        pass
    return None

# Render persistent disk would be /data, else fallback to /tmp or local
CONFIGURED_PATH = _config_store_path()
CANDIDATE_PATHS = [
    Path(os.getenv("STORE_PATH", str(CONFIGURED_PATH) if CONFIGURED_PATH else "/data/dedup.json")),
    Path("/data/dedup.json"),           # Render Disk mount
    Path("/tmp/instabot_dedup.json"),   # ephemeral but survives restarts on same instance
    Path(__file__).parent.parent / "dedup.json",
]

def get_store_path() -> Path:
    for p in CANDIDATE_PATHS:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            # Prefer /data if exists/writable
            if str(p).startswith("/data"):
                if Path("/data").exists():
                    return p
                else:
                    continue
            return p
        except:
            continue
    return CANDIDATE_PATHS[-1]

STORE_PATH = get_store_path()

def load_posted_ids() -> set:
    if not STORE_PATH.exists():
        return set()
    try:
        with open(STORE_PATH, "r") as f:
            data = json.load(f)
            ids = set(data.get("posted_ids", []))
            # Also support posted_codes
            ids.update(data.get("posted_codes", []))
            return ids
    except Exception as e:
        logger.warning(f"Failed to load dedup {STORE_PATH}: {e}")
        return set()

def save_posted_id(media_id: str, code: str = None, pk: str = None):
    try:
        ids = []
        codes = []
        if STORE_PATH.exists():
            with open(STORE_PATH, "r") as f:
                data = json.load(f)
                ids = data.get("posted_ids", [])
                codes = data.get("posted_codes", [])
        if media_id not in ids:
            ids.append(media_id)
        if pk and pk not in ids:
            ids.append(pk)
        if code and code not in codes:
            codes.append(code)
        with open(STORE_PATH, "w") as f:
            json.dump({"posted_ids": ids, "posted_codes": codes}, f, indent=2)
        logger.info(f"Saved posted id {media_id} code={code} to {STORE_PATH}")
    except Exception as e:
        logger.error(f"Failed to save posted id: {e}")

def get_stats():
    ids = load_posted_ids()
    return {"posted_count": len(ids), "store_path": str(STORE_PATH)}
