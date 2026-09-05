import json
import os
import logging
from pathlib import Path

from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired

logger = logging.getLogger(__name__)

SESSION_PATH_ENV = "SESSION_JSON"
SESSION_FILE_FALLBACK = Path(__file__).parent.parent / "session.json"
TMP_SESSION = Path("/tmp/session.json")


def load_session_dict() -> dict:
    """
    Load session dict from:
    1. SESSION_JSON env var (raw JSON string or base64)
    2. /tmp/session.json (written on boot)
    3. ./session.json fallback (local dev)
    """
    # 1. Env var
    env_val = os.getenv(SESSION_PATH_ENV)
    if env_val:
        env_val = env_val.strip()
        # try raw JSON
        try:
            data = json.loads(env_val)
            logger.info("Loaded session from SESSION_JSON env (raw JSON)")
            return data
        except json.JSONDecodeError:
            pass
        # try base64
        try:
            import base64
            decoded = base64.b64decode(env_val).decode("utf-8")
            data = json.loads(decoded)
            logger.info("Loaded session from SESSION_JSON env (base64)")
            return data
        except Exception as e:
            logger.warning(f"SESSION_JSON env exists but failed to parse: {e}")

        # try as file path
        p = Path(env_val)
        if p.exists():
            with open(p, "r") as f:
                return json.load(f)

    # 2. /tmp/session.json
    if TMP_SESSION.exists():
        with open(TMP_SESSION, "r") as f:
            logger.info("Loaded session from /tmp/session.json")
            return json.load(f)

    # 3. fallback
    if SESSION_FILE_FALLBACK.exists():
        with open(SESSION_FILE_FALLBACK, "r") as f:
            logger.info(f"Loaded session from {SESSION_FILE_FALLBACK}")
            return json.load(f)

    raise FileNotFoundError(
        "No session.json found. Set SESSION_JSON env var or place session.json in project root."
    )


def get_client(logs: list = None) -> Client:
    """
    Create and login client using session.json.
    Flow: load_settings -> login_by_sessionid -> dump_settings
    """
    def log(msg):
        logger.info(msg)
        if logs is not None:
            logs.append(msg)

    session = load_session_dict()
    
    # Validate structure
    if "authorization_data" not in session or "sessionid" not in session["authorization_data"]:
        raise ValueError("Invalid session.json: missing authorization_data.sessionid")

    cl = Client()
    # Apply settings from file to mimic device
    try:
        # instagrapi wants settings dict, not raw file path for some versions
        cl.set_settings(session)
        log(f"Applied device settings: {session.get('device_settings', {}).get('model','unknown')} | UA: {session.get('user_agent','')[:50]}...")
    except Exception as e:
        log(f"set_settings warning: {e} (continuing)")

    # Also set UUIDs if present for better emulation
    try:
        if "uuids" in session:
            cl.uuids = session["uuids"]
        if "device_settings" in session:
            cl.set_device(session["device_settings"])
    except Exception:
        pass

    sessionid = session["authorization_data"]["sessionid"]
    # sessionid in file is URL-encoded: 4837...%3A... -> requests will handle, but login_by_sessionid expects raw
    # instagrapi handles both, but we try raw
    try:
        import urllib.parse
        decoded_sid = urllib.parse.unquote(sessionid)
        # login_by_sessionid expects the cookie value, instagrapi will set it
        cl.login_by_sessionid(decoded_sid)
        log(f"Logged in via sessionid for ds_user_id={session['authorization_data'].get('ds_user_id')}")
    except (LoginRequired, ChallengeRequired) as e:
        log(f"Login failed - session expired or challenge: {e}")
        raise
    except Exception as e:
        log(f"login_by_sessionid failed: {e}")
        # Fallback: try to set cookie directly
        try:
            cl.session.headers.update({"Authorization": f"Bearer {sessionid}"})
            # Verify by fetching account info
            cl.get_timeline_feed(amount=1)
            log("Fallback header set, timeline check passed")
        except Exception as e2:
            raise RuntimeError(f"Failed to login with session.json: {e2}") from e

    # Verify login
    try:
        user = cl.account_info()
        log(f"Verified login as @{user.username} (pk={user.pk})")
    except Exception as e:
        log(f"Warning: account_info failed after login: {e} - but session may still be valid")

    # Persist to /tmp for debugging (Render ephemeral)
    try:
        TMP_SESSION.parent.mkdir(parents=True, exist_ok=True)
        with open(TMP_SESSION, "w") as f:
            json.dump(cl.get_settings(), f, indent=2)
        log("Dumped refreshed settings to /tmp/session.json")
    except Exception as e:
        log(f"Could not dump settings: {e}")

    return cl
