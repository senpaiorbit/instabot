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

# Logging setup - upstream to stdout for Render
log_level = GLOBAL_CONFIG.get("log_level", "INFO") if isinstance(GLOBAL_CONFIG, dict) else "INFO"
logging.basicConfig(level=getattr(logging, log_level, logging.INFO), format="%(asctime)s [%(levelname)s] %(message)s", force=True)
logger = logging.getLogger(__name__)
# Also log werkzeug
logging.getLogger("werkzeug").setLevel(logging.INFO)

# In-memory live logs - must be before handlers
recent_logs = []
MAX_RECENT = 200

def add_log(msg: str):
    entry = f"{datetime.now().isoformat()} {msg}"
    logger.info(msg)
    print(entry, flush=True)
    recent_logs.append(entry)
    if len(recent_logs) > MAX_RECENT:
        recent_logs.pop(0)
    return msg

app = Flask(__name__)

# Upstream request logging
@app.before_request
def log_request():
    logger.info(f"--> {request.method} {request.path} args={dict(request.args)} ip={request.remote_addr} ua={request.headers.get('User-Agent','')[:60]}")

@app.after_request
def log_response(response):
    logger.info(f"<-- {request.method} {request.path} {response.status_code}")
    return response

# Global error handler to always return logs + show Internal Server Error upstream
@app.errorhandler(500)
def handle_500(e):
    tb = traceback.format_exc()
    logger.error(f"Internal Server Error: {e}\n{tb}")
    add_log(f"Internal Server Error: {e}")
    for line in tb.splitlines()[-20:]:
        add_log(line)
    return jsonify({"status": "error", "error": str(e), "traceback": tb.splitlines()[-20:], "logs": recent_logs[-50:]}), 500
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
            "/live": "GET - LIVE LOG HTML (auto-refresh, shows Internal Server Error)",
            "/stream": "GET - SSE stream for upstream",
            "/config": "GET - show global config.json (sanitized)",
            "/logs": "GET - recent logs JSON",
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

@app.route("/live")
def live():
    # Live log HTML with auto-refresh for Render - shows upstream + Internal Server Error
    html = """<!doctype html>
<html><head><meta charset=utf-8><title>Live Logs - instabot2</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>body{font-family:monospace;background:#0f172a;color:#e2e8f0;margin:0;padding:12px}h1{font-size:18px;margin:0 0 8px}#log{white-space:pre-wrap;background:#1e293b;padding:10px;border-radius:8px;max-height:75vh;overflow:auto;font-size:12px;line-height:1.4}button{padding:6px 12px;border-radius:6px;border:0;background:#38bdf8;cursor:pointer;margin-right:6px}a{color:#7dd3fc}</style>
</head><body>
<h1>🔴 Live Logs - instabot2 <a href="/health">health</a> <a href="/upload?dry_run=1&amount=2">test upload</a></h1>
<div><button onclick="fetchLogs()">Refresh</button><button onclick="clearLogs()">Clear view</button><span id=status></span></div>
<div id=log>Loading...</div>
<script>
let timer=null;
async function fetchLogs(){
  document.getElementById('status').textContent=' fetching...';
  try{
    let r=await fetch('/logs'); let j=await r.json();
    let s=await fetch('/health'); let h=await s.json();
    let txt=`Health: ${JSON.stringify(h)}\\n--- Logs (${j.count} total, showing 50) ---\\n` + (j.logs||[]).join('\\n');
    document.getElementById('log').textContent=txt;
    document.getElementById('log').scrollTop=document.getElementById('log').scrollHeight;
    document.getElementById('status').textContent=' ok '+new Date().toLocaleTimeString();
  }catch(e){ document.getElementById('status').textContent=' error '+e; }
}
function clearLogs(){document.getElementById('log').textContent='';}
fetchLogs(); timer=setInterval(fetchLogs, 2000);
</script>
</body></html>"""
    return html, 200, {"Content-Type": "text/html"}

@app.route("/stream")
def stream():
    # SSE live stream - upstream friendly
    from flask import Response
    def gen():
        last = 0
        for _ in range(60):  # 60*2s = 2min stream
            if len(recent_logs) > last:
                for line in recent_logs[last:]:
                    yield f"data: {line}\\n\\n"
                last = len(recent_logs)
            time.sleep(2)
        yield "data: [stream end]\\n\\n"
    return Response(gen(), mimetype="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/stats")
def stats():
    from bot.store import get_stats, load_posted_ids
    s = get_stats()
    ids = load_posted_ids()
    return jsonify({
        **s,
        "posted_ids_sample": list(ids)[:10]
    })

def _wants_html():
    # Only return HTML if explicitly requested - default is JSON/auto for feed->scrape->upload
    # Use ?html=1 or ?format=html for HTML view; otherwise JSON for automatic work
    if request.args.get("html") == "1" or request.args.get("format") == "html":
        return True
    if request.args.get("json") == "1" or request.args.get("format") == "json":
        return False
    # No auto HTML - keep /upload automatic (JSON) as you requested
    return False

def _upload_html_shell():
    # Show your request present + live other logs - no JSON, pure HTML as requested
    # Preserve query string for JS fetch with json=1
    qs = request.query_string.decode()
    qs_json = (qs + "&json=1") if qs else "json=1"
    # Also include cover param handling
    html = f"""<!doctype html>
<html><head><meta charset=utf-8><title>Upload - Live Log</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{{font-family:monospace;background:#0f172a;color:#e2e8f0;margin:0;padding:12px}}
h1{{font-size:18px}} .card{{background:#1e293b;padding:12px;border-radius:8px;margin:8px 0}}
#log{{white-space:pre-wrap;background:#020617;padding:10px;border-radius:8px;max-height:60vh;overflow:auto;font-size:12px;line-height:1.4;border:1px solid #334155}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;margin-right:6px}}
.ok{{background:#22c55e;color:#000}} .err{{background:#ef4444}} .wait{{background:#38bdf8;color:#000}}
a{{color:#7dd3fc}} button{{padding:6px 12px;border-radius:6px;border:0;background:#38bdf8;cursor:pointer;margin:4px}}
input,select{{padding:6px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#e2e8f0}}
</style></head><body>
<h1>⬆️ /upload - Live Log <a href="/live">full live</a> <a href="/health">health</a> <a href="/stats">stats</a></h1>
<div class=card>
<b>Your request present:</b> <span class=badge id=req Badge>loading</span><br>
Method: <b>{request.method}</b> &nbsp; IP: {request.remote_addr} &nbsp; Target: <b id=target>-</b> &nbsp; Cover: <b id=coverShow>-</b><br>
URL: <code>{request.url}</code><br>
<form id=ctrl style="margin-top:8px" onsubmit="return false">
Target: <input id=inpTarget placeholder="timeline or username" style="width:160px"> 
Amount: <input id=inpAmount type=number value="5" style="width:60px">
Cover: <input id=inpCover placeholder="cover n or filename" style="width:120px" title="/cover/{{n}}.jpg"> 
Dry run: <select id=inpDry><option value="0">real upload</option><option value="1">dry_run</option></select>
<button onclick="startUpload()">Start Live Upload</button>
</form>
<div id=status class=card>Waiting to start...</div>
</div>
<div class=card><b>Live other logs (upstream):</b> <span id=liveStatus></span><div id=log>Connecting...</div></div>
<script>
const qs = new URLSearchParams(window.location.search);
document.getElementById('inpTarget').value = qs.get('target')||'';
document.getElementById('inpAmount').value = qs.get('amount')||'5';
document.getElementById('inpCover').value = qs.get('cover')||'';
document.getElementById('inpDry').value = qs.get('dry_run')||'0';
document.getElementById('target').textContent = qs.get('target')||'timeline (personalized feed)';
document.getElementById('coverShow').textContent = qs.get('cover')||'auto from /cover/*.*';
let logTimer=null;
let lastLogCount=0;
async function fetchLiveLogs(){{
  try{{
    let r=await fetch('/logs'); let j=await r.json();
    let txt = j.logs.slice(-80).join('\\n');
    document.getElementById('log').textContent = txt;
    document.getElementById('log').scrollTop = document.getElementById('log').scrollHeight;
    document.getElementById('liveStatus').textContent = 'live '+new Date().toLocaleTimeString()+' ('+j.count+' total)';
  }}catch(e){{document.getElementById('liveStatus').textContent='error '+e;}}
}}
async function startUpload(){{
  const t=document.getElementById('inpTarget').value;
  const a=document.getElementById('inpAmount').value;
  const c=document.getElementById('inpCover').value;
  const d=document.getElementById('inpDry').value;
  const url='/upload?json=1'+(t?'&target='+encodeURIComponent(t):'')+'&amount='+a+(c?'&cover='+encodeURIComponent(c):'')+'&dry_run='+d;
  document.getElementById('reqBadge').textContent='running...'; document.getElementById('reqBadge').className='badge wait';
  document.getElementById('status').innerHTML='<span class=badge wait>⏳ Upload started...</span> '+url+'<br>Fetching...';
  document.getElementById('status').scrollIntoView();
  try{{
    let r=await fetch(url); let j=await r.json();
    let ok = j.status==='uploaded' || j.status==='dry_run';
    document.getElementById('reqBadge').textContent=j.status; document.getElementById('reqBadge').className='badge '+(ok?'ok':'err');
    let html = '<b>Status:</b> '+j.status+'<br>';
    if(j.error) html+='<b>Error:</b> <span style=color:#f87171>'+j.error+'</span><br>';
    if(j.uploaded) html+='<b>Uploaded:</b> <a href="'+(j.uploaded.url||'#')+'" target=_blank>'+(j.uploaded.code||'')+'</a><br>';
    if(j.selected) html+='<b>Selected:</b> '+j.selected.code+' @'+j.selected.username+'<br>';
    html+='<b>Time:</b> '+(j.time_taken||'')+'s<br>';
    html+='<details open><summary>Full logs</summary><pre style=white-space:pre-wrap;background:#020617;padding:8px;border-radius:6px;max-height:40vh;overflow:auto>'+ (j.logs||[]).join('\\n') +'</pre></details>';
    if(j.traceback) html+='<details><summary>Traceback</summary><pre>'+j.traceback.join('\\n')+'</pre></details>';
    document.getElementById('status').innerHTML=html;
  }}catch(e){{
    document.getElementById('reqBadge').textContent='error'; document.getElementById('reqBadge').className='badge err';
    document.getElementById('status').innerHTML='<b>Fetch error:</b> '+e;
  }}
}}
// Auto-start if not already dry_run page load? Only auto if ?autostart=1 or coming from direct /upload click
if(qs.has('autostart') || window.location.search.includes('target') || window.location.search.includes('amount')){{
  // Don't auto-start to avoid accidental double upload on refresh - user clicks button
  document.getElementById('status').innerHTML='Click <b>Start Live Upload</b> to run (shows your request present + live other logs). Use <code>?json=1</code> for raw JSON.';
}}
fetchLiveLogs(); logTimer=setInterval(fetchLiveLogs, 2000);
</script>
</body></html>"""
    return html, 200, {"Content-Type": "text/html"}

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

    # HTML live log view - browser shows HTML, API/UptimeRobot gets JSON
    if _wants_html():
        return _upload_html_shell()

    start = time.time()
    log("=== /upload triggered ===")
    log(f"Method={request.method} IP={request.remote_addr} Args={dict(request.args)} UA={request.headers.get('User-Agent','')[:40]}")

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
    # Custom cover: /cover/{n}.jpg etc via ?cover=2 or ?cover=2.jpg
    cover = request.args.get("cover") or os.getenv("CUSTOM_COVER") or GLOBAL_CONFIG.get("custom_cover")
    if cover:
        log(f"Custom cover param: {cover} (will use /cover/{cover}.*)")
    # Force personalized feed if config says so (ignore random reel)
    if GLOBAL_CONFIG.get("force_personalized_feed") and target and target != "timeline":
        log(f"Config force_personalized_feed=true -> ignoring target={target}, using timeline (personalized) instead")
        target = None

    log(f"Params: target={target or 'timeline (personalized)'} amount={amount} dry_run={dry_run} force={force} cover={cover or 'auto'}")

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

        # 2. Scrape - combine Instaloader for personalized reels + instagrapi for upload
        reels_only = bool(GLOBAL_CONFIG.get("reels_only", True))
        use_instaloader = bool(GLOBAL_CONFIG.get("use_instaloader", True))
        # Allow query override ?reels=0/1 and ?use_instaloader=0/1
        if request.args.get("reels") is not None:
            reels_only = request.args.get("reels") in ("1","true","yes")
        if request.args.get("use_instaloader") is not None:
            use_instaloader = request.args.get("use_instaloader") in ("1","true","yes")
        src = "reels" if reels_only else "timeline"
        if target:
            src = f"@{target}"
        log(f"Step 2: Scraping {src} (reels_only={reels_only} use_instaloader={use_instaloader} target={target or 'personalized'})...")
        from bot.feed import scrape_feed, get_next_video_not_posted
        from bot.store import load_posted_ids, save_posted_id, get_stats

        all_medias, videos = scrape_feed(cl, amount=amount, target_username=target, reels_only=reels_only, use_instaloader=use_instaloader, logs=logs)
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
        log(f"Dedup store: {len(posted_ids)} already posted, force={force} (path={get_stats()['store_path']})")
        if get_stats().get("free_tier_no_disk"):
            log("Free tier: no persistent disk - will also check profile for dedup fallback")

        media = get_next_video_not_posted(videos, posted_ids, logs=logs)
        if not media:
            log("All videos already posted (local dedup)")
            return jsonify({
                "status": "all_posted",
                "scraped_total": len(all_medias),
                "videos_found": len(videos),
                "posted_count": len(posted_ids),
                "logs": logs,
                "time_taken": round(time.time()-start, 2)
            })

        # Free-tier resilient check: if not in local but already on profile, skip and try next video
        if not force and GLOBAL_CONFIG.get("dedup_mode") == "local_plus_profile":
            from bot.store import is_duplicate_via_profile
            profile_amount = int(GLOBAL_CONFIG.get("profile_check_amount", 12))
            # Check if selected media already on profile
            if is_duplicate_via_profile(cl, media, amount=profile_amount, logs=logs):
                log(f"Selected {media['code']} is already on profile -> trying next video")
                found_alt = None
                for alt in videos:
                    if alt["id"] == media["id"]:
                        continue
                    if alt["id"] in posted_ids or alt["pk"] in posted_ids or alt["code"] in posted_ids:
                        continue
                    if not is_duplicate_via_profile(cl, alt, amount=profile_amount, logs=logs):
                        found_alt = alt
                        break
                    else:
                        log(f"Alt {alt['code']} also on profile, skipping")
                if found_alt:
                    media = found_alt
                    log(f"Switched to alt non-duplicate: {media['code']}")
                else:
                    log(f"All {len(videos)} videos already on profile")
                    save_posted_id(media["id"], code=media["code"], pk=media["pk"])
                    return jsonify({
                        "status": "skipped_profile_duplicate",
                        "reason": "all videos already on profile via #src_ marker",
                        "skipped": {k: (str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v) for k, v in media.items() if k != "_raw"},
                        "logs": logs,
                        "time_taken": round(time.time()-start, 2)
                    }), 200

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

        # 3. Download - pass custom cover if set
        log("Step 3: Downloading video...")
        if cover:
            media["_cover"] = cover  # used by downloader for /cover/{n}.*
            # Also set env for downloader fallback
            os.environ["CUSTOM_COVER"] = str(cover)
        from bot.downloader import download_video
        dl = download_video(cl, media, logs=logs)
        log(f"Downloaded: {dl}")

        if not dl.get("video_path"):
            raise RuntimeError("Download failed - no video_path")

        # 4. Build caption - config template + marker for free-tier dedup
        from bot.uploader import build_caption, upload_video
        extra = extra_caption or os.getenv("EXTRA_CAPTION") or GLOBAL_CONFIG.get("extra_caption")
        template = GLOBAL_CONFIG.get("caption_template", "{original}\n\n🎥 via @{username}")
        add_marker = bool(GLOBAL_CONFIG.get("caption_marker", True))
        caption = build_caption(media["caption"], username=media["username"], extra=extra, template=template, source_code=media["code"], add_marker=add_marker)
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

    except BaseException as e:
        # Catch SystemExit from gunicorn WORKER TIMEOUT (clip_upload sleep) and OOM
        err = str(e) or e.__class__.__name__
        tb = traceback.format_exc()
        log(f"ERROR: {err} ({e.__class__.__name__})")
        log(tb)
        # Special handling for gunicorn timeout / OOM on free 512MB
        if "SystemExit" in e.__class__.__name__ or "timeout" in err.lower():
            log("UPSTREAM: gunicorn WORKER TIMEOUT / OOM on free 512MB during clip configure. Fix: render.yaml timeout 600 already set, but Render still uses default 30s. Workaround: using video_upload first + configure_timeout=5, or test locally.")
            log("TIP: Test locally: python app.py then curl localhost:10000/upload?dry_run=1 -- then real upload locally uses your PC RAM, not Render free.")
        elapsed = round(time.time()-start, 2)
        return jsonify({
            "status": "error",
            "error": err,
            "error_type": e.__class__.__name__,
            "traceback": tb.splitlines()[-15:],
            "logs": logs,
            "time_taken": elapsed,
            "hint": "If WORKER TIMEOUT/OOM: Render free 512MB can't handle clip analyze. Try ?target with smaller video, or run locally, or upgrade to Starter 1GB. video_upload is now tried first with short timeout."
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
