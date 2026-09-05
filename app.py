import os
import time
import logging
import traceback
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request
from dotenv import load_dotenv

load_dotenv()

# Global config
try:
    from bot.config import load_config
    GLOBAL_CONFIG = load_config()
except Exception as e:
    GLOBAL_CONFIG = {}
    print(f"config load failed: {e}")

# Logging setup
log_level = GLOBAL_CONFIG.get("log_level", "INFO") if isinstance(GLOBAL_CONFIG, dict) else "INFO"
logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory logs for /upload response (last N)
recent_logs = []
MAX_RECENT = 100

def add_log(msg: str):
    entry = f"{datetime.now().isoformat()} {msg}"
    logger.info(msg)
    recent_logs.append(entry)
    if len(recent_logs) > MAX_RECENT:
        recent_logs.pop(0)
    return msg

@app.route("/")
def index():
    return jsonify({
        "status": "ok",
        "service": "instabot2",
        "config": GLOBAL_CONFIG,
        "endpoints": {
            "/health": "GET - health check for UptimeRobot (use this for keep-alive every 5m)",
            "/upload": "GET/POST - scrape feed & upload next video. Query: ?target=username&amount=10&dry_run=1&force=1",
            "/auto": "GET - alias for /upload for UptimeRobot auto-upload (hit every 60m)",
            "/config": "GET - show global config.json (sanitized)",
            "/logs": "GET - recent logs",
            "/stats": "GET - dedup stats"
        },
        "time": datetime.now().isoformat()
    })

@app.route("/config")
def config():
    # Return sanitized config (hide secrets)
    safe = {k: v for k, v in GLOBAL_CONFIG.items()}
    # hide session
    return jsonify({
        "config": safe,
        "env_overrides": {
            "TARGET_USERNAME": os.getenv("TARGET_USERNAME"),
            "SCRAPE_AMOUNT": os.getenv("SCRAPE_AMOUNT"),
            "UPLOAD_SECRET_set": bool(os.getenv("UPLOAD_SECRET")),
            "SESSION_JSON_set": bool(os.getenv("SESSION_JSON"))
        }
    })

@app.route("/health")
def health():
    # UptimeRobot expects 200
    # Optionally check session.json exists
    session_ok = False
    try:
        from bot.client import load_session_dict
        s = load_session_dict()
        session_ok = bool(s.get("authorization_data", {}).get("sessionid"))
    except Exception as e:
        session_ok = False
        logger.warning(f"health session check failed: {e}")

    from bot.store import get_stats
    stats = get_stats()

    status = "ok" if session_ok else "degraded"
    code = 200 if session_ok else 200  # still 200 for UptimeRobot, but indicate degraded

    return jsonify({
        "status": status,
        "session_loaded": session_ok,
        "posted_count": stats["posted_count"],
        "store_path": stats["store_path"],
        "time": datetime.now().isoformat(),
        "uptime": time.time()
    }), code

@app.route("/logs")
def logs():
    return jsonify({
        "logs": recent_logs[-50:],
        "count": len(recent_logs)
    })

@app.route("/stats")
def stats():
    from bot.store import get_stats, load_posted_ids
    s = get_stats()
    ids = load_posted_ids()
    return jsonify({
        **s,
        "posted_ids_sample": list(ids)[:10]
    })

@app.route("/upload", methods=["GET", "POST"])
def upload():
    """
    Main endpoint for auto-upload.
    Triggered by UptimeRobot or manual.
    Query params:
      target=username (optional, scrape that user's feed, else timeline)
      amount=10 (number of medias to scan)
      dry_run=1 (only scrape, don't upload)
      force=1 (ignore dedup, re-upload even if already posted)
    Headers:
      X-Upload-Secret: if UPLOAD_SECRET env set, must match
    """
    logs = []
    def log(m):
        add_log(m)
        logs.append(m)
        print(m, flush=True)

    start = time.time()
    log("=== /upload triggered ===")
    log(f"Method={request.method} IP={request.remote_addr} Args={dict(request.args)}")

    # Optional secret protection
    required_secret = os.getenv("UPLOAD_SECRET")
    if required_secret:
        provided = request.headers.get("X-Upload-Secret") or request.args.get("secret")
        if provided != required_secret:
            log("Unauthorized: invalid secret")
            return jsonify({"status": "error", "error": "unauthorized", "logs": logs}), 401

    # Params - config.json is base, env and request override
    target = request.args.get("target") or os.getenv("TARGET_USERNAME") or GLOBAL_CONFIG.get("target_username")
    # Also allow POST json
    if request.is_json:
        body = request.get_json(silent=True) or {}
        target = body.get("target", target)
        dry_run = body.get("dry_run", request.args.get("dry_run"))
        amount = body.get("amount", request.args.get("amount"))
        force = body.get("force", request.args.get("force"))
        extra_caption = body.get("caption")
    else:
        dry_run = request.args.get("dry_run")
        amount = request.args.get("amount")
        force = request.args.get("force")
        extra_caption = request.args.get("caption")

    cfg_amount = GLOBAL_CONFIG.get("scrape_amount", 10)
    amount = int(amount) if amount and str(amount).isdigit() else int(os.getenv("SCRAPE_AMOUNT", cfg_amount))
    dry_run = str(dry_run).lower() in ("1", "true", "yes") if dry_run else False
    force = str(force).lower() in ("1", "true", "yes") if force else False

    log(f"Params: target={target or 'timeline'} amount={amount} dry_run={dry_run} force={force}")

    # Rate limit: prevent double upload within configured seconds
    rate_limit = int(GLOBAL_CONFIG.get("rate_limit_seconds", 90))
    lock_path = Path("/tmp/last_upload.txt")
    if not force and not dry_run:
        if lock_path.exists():
            try:
                last = float(lock_path.read_text().strip())
                if time.time() - last < rate_limit:
                    log(f"Rate limited: last upload {int(time.time()-last)}s ago, wait {rate_limit}s or use ?force=1")
                    return jsonify({"status": "skipped", "reason": "rate_limited", "seconds_since_last": int(time.time()-last), "logs": logs}), 429
            except:
                pass

    try:
        # 1. Login
        log("Step 1: Logging in via session.json...")
        from bot.client import get_client
        cl = get_client(logs=logs)
        log("Login OK")

        # 2. Scrape
        log(f"Step 2: Scraping feed (target={target or 'timeline'})...")
        from bot.feed import scrape_feed, get_next_video_not_posted
        from bot.store import load_posted_ids, save_posted_id, get_stats

        all_medias, videos = scrape_feed(cl, amount=amount, target_username=target, logs=logs)
        log(f"Scraped {len(all_medias)} total, {len(videos)} videos")

        if not videos:
            log("No videos found to upload")
            return jsonify({
                "status": "no_videos",
                "scraped_total": len(all_medias),
                "videos_found": 0,
                "logs": logs,
                "time_taken": round(time.time()-start, 2)
            })

        posted_ids = load_posted_ids() if not force else set()
        log(f"Dedup store: {len(posted_ids)} already posted, force={force}")

        media = get_next_video_not_posted(videos, posted_ids, logs=logs)
        if not media:
            log("All videos already posted (dedup)")
            return jsonify({
                "status": "all_posted",
                "scraped_total": len(all_medias),
                "videos_found": len(videos),
                "posted_count": len(posted_ids),
                "logs": logs,
                "time_taken": round(time.time()-start, 2)
            })

        log(f"Selected media id={media['id']} code={media['code']} caption={media['caption'][:80]}")

        if dry_run:
            log("DRY RUN - not downloading/uploading")
            def sanitize(m):
                return {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in m.items() if k != "_raw"}
            return jsonify({
                "status": "dry_run",
                "selected": sanitize(media),
                "all_videos": [sanitize(v) for v in videos],
                "logs": logs,
                "time_taken": round(time.time()-start, 2)
            })

        # 3. Download
        log("Step 3: Downloading video...")
        from bot.downloader import download_video
        dl = download_video(cl, media, logs=logs)
        log(f"Downloaded: {dl}")

        if not dl.get("video_path"):
            raise RuntimeError("Download failed - no video_path")

        # 4. Build caption - config template
        from bot.uploader import build_caption, upload_video
        extra = extra_caption or os.getenv("EXTRA_CAPTION") or GLOBAL_CONFIG.get("extra_caption")
        template = GLOBAL_CONFIG.get("caption_template", "{original}\n\n🎥 via @{username}")
        caption = build_caption(media["caption"], username=media["username"], extra=extra, template=template)
        log(f"Caption ({len(caption)} chars): {caption[:120]}...")

        # 5. Upload
        log("Step 4: Uploading to your account...")
        result = upload_video(cl, dl["video_path"], dl.get("thumbnail_path"), caption=caption, logs=logs)
        log(f"Upload result: {result}")

        # 6. Save dedup
        save_posted_id(media["id"], code=media["code"], pk=media["pk"])
        # Also save uploaded id
        save_posted_id(result["id"], code=result["code"])

        # Update lock
        lock_path.write_text(str(time.time()))

        # Cleanup tmp
        try:
            Path(dl["video_path"]).unlink(missing_ok=True)
            if dl.get("thumbnail_path"):
                Path(dl["thumbnail_path"]).unlink(missing_ok=True)
            log("Cleaned tmp files")
        except Exception as e:
            log(f"Cleanup warning: {e}")

        elapsed = round(time.time()-start, 2)
        log(f"=== DONE in {elapsed}s ===")
        def sanitize2(m):
            return {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in m.items() if k != "_raw"}
        return jsonify({
            "status": "uploaded",
            "source": sanitize2(media),
            "uploaded": result,
            "logs": logs,
            "time_taken": elapsed
        })

    except Exception as e:
        err = str(e)
        tb = traceback.format_exc()
        log(f"ERROR: {err}")
        log(tb)
        elapsed = round(time.time()-start, 2)
        return jsonify({
            "status": "error",
            "error": err,
            "traceback": tb.splitlines()[-10:],
            "logs": logs,
            "time_taken": elapsed
        }), 500

# For UptimeRobot auto-upload variant: hit /auto which does upload but returns 200 even if rate limited
@app.route("/auto")
def auto():
    # Same as /upload but silent and idempotent for cron-like behavior
    # UptimeRobot can hit /auto?secret=xxx every 30min
    return upload()

if __name__ == "__main__":
    port = int(os.getenv("PORT", str(GLOBAL_CONFIG.get("port", 10000))))
    app.run(host="0.0.0.0", port=port, debug=False)
