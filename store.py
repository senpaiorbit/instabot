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

# Free tier: no persistent disks! Use /tmp which survives while instance is warm
# UptimeRobot every 5m keeps it warm, but on cold start /tmp resets -> profile check is fallback
CONFIGURED_PATH = _config_store_path()
CANDIDATE_PATHS = [
    Path(os.getenv("STORE_PATH", str(CONFIGURED_PATH) if CONFIGURED_PATH else "/tmp/instabot_dedup.json")),
    Path("/tmp/instabot_dedup.json"),   # free-tier safe (ephemeral but fine with UptimeRobot)
    Path(__file__).parent.parent / "dedup.json",
    Path("/data/dedup.json"),           # only if you upgrade to paid with disk
]

def get_store_path() -> Path:
    for p in CANDIDATE_PATHS:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            # Skip /data on free tier (does not exist)
            if str(p).startswith("/data") and not Path("/data").exists():
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

def is_duplicate_via_profile(cl, media: dict, amount: int = 12, logs: list = None) -> bool:
    """
    Free-tier resilient dedup: check your own recent posts for marker.
    We embed source code as #src_{code} or check caption substring.
    Works even after /tmp reset / cold start.
    """
    def log(m):
        if logs is not None:
            logs.append(m)
        logger.info(m)
    try:
        # Use marker we embed in caption: #src_{code}
        marker = f"#src_{media.get('code')}"
        # Also fallback: check if same original caption snippet already posted
        own_id = cl.user_id
        recent = cl.user_medias(own_id, amount=amount)
        log(f"Profile check: scanning last {len(recent)} posts for marker {marker}")
        for m in recent:
            cap = getattr(m, "caption_text", "") or ""
            if marker in cap:
                log(f"Duplicate via profile: found marker {marker} in your post {m.code}")
                return True
            # Fallback: if original caption first 40 chars already exists (avoid repost of same viral)
            orig_snippet = (media.get("caption") or "")[:40].strip()
            if orig_snippet and len(orig_snippet) > 15 and orig_snippet in cap:
                log(f"Possible duplicate via caption snippet: '{orig_snippet[:30]}' found in {m.code}")
                # Don't hard block on snippet alone, just log - require marker for strict
                pass
        log(f"No duplicate found via profile for {marker}")
        return False
    except Exception as e:
        log(f"Profile dedup check failed (non-fatal): {e}")
        return False

def get_stats():
    ids = load_posted_ids()
    free_tier = not Path("/data").exists()
    # Read actual dedup_mode from config
    try:
        cfg_path = Path(__file__).parent.parent / "config.json"
        dedup_mode = "local_plus_profile"
        if cfg_path.exists():
            with open(cfg_path) as f:
                dedup_mode = json.load(f).get("dedup_mode", "local_plus_profile")
    except:
        dedup_mode = "local_plus_profile"
    return {
        "posted_count": len(ids),
        "store_path": str(STORE_PATH),
        "free_tier_no_disk": free_tier,
        "dedup_mode": dedup_mode,
        "note": "Free tier: no persistent disk. Dedup uses /tmp + profile marker #src_{code}" if free_tier else "Paid tier with disk available"
    }
