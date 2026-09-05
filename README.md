# InstaBot2 - Render (Free Tier Ready)

Web service bot that logs in via `session.json`, scrapes feed, and re-uploads video to your account. Optimized for Render **free** (no disk, no SSH, spins down).

## Free Tier Limitations & Fixes

Render free instances:
- Spin down after 15m inactivity
- No persistent disks, no SSH, no scaling, no one-off jobs

**How this bot handles it:**

| Limitation | Fix in this bot |
|---|---|
| No disk `/data` | `config.json:12` `store_path: "/tmp/instabot_dedup.json"` + profile fallback. Old `store_path: "/data/..."` removed. |
| Dedup lost on cold start (`/tmp` wiped) | Embeds `#src_{code}` marker in caption (`bot/uploader.py:38`) and checks your last 12 posts via API (`bot/store.py:81 is_duplicate_via_profile`). Even after wipe, won't repost same source. |
| Spins down | UptimeRobot every 5m on `/health` keeps warm |
| No cron | `UptimeRobot` hits `/auto` or `/upload` to trigger job |
| 512MB RAM free | `gunicorn --workers 1 --threads 2` in `render.yaml:8` |

Upgrade to Starter ($7) only if you want disk persistence + no spin-down.

## Global Config
Edit `config.json` (committed) + env vars override it.

`config.json:1`:
```json
{
  "target_username": null,      // null = timeline feed, or "username" to scrape that user
  "scrape_amount": 10,
  "extra_caption": "Follow for more 🔥",
  "caption_template": "{original}\n\n🎥 via @{username}",
  "dedup_mode": "local_plus_profile", // free-tier resilient
  "rate_limit_seconds": 90,
  "store_path": "/tmp/instabot_dedup.json",
  "profile_check_amount": 12,
  "caption_marker": true
}
```

Priority: `?target=` query > `TARGET_USERNAME` env > `config.json`

## Local Run
```bash
pip install -r requirements.txt
python app.py
curl http://localhost:10000/health
curl http://localhost:10000/config
curl "http://localhost:10000/upload?dry_run=1&amount=2&target=instagram" # scrape only
curl "http://localhost:10000/upload" # real upload
```

## Render Deploy (Free)
1. Push to GitHub
2. Render Dashboard -> New Web Service -> Connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300 --graceful-timeout 30`
5. Health Check: `/health`
6. Env Vars (Dashboard -> Environment):
   - `SESSION_JSON` = entire `session.json` content as single line (or base64). Get via `cat session.json | jq -c .`
   - `TARGET_USERNAME` = leave empty for timeline, or e.g. `instagram`
   - `UPLOAD_SECRET` = optional protect
   - `PYTHON_VERSION`=3.11.0

No disk to add on free. Blueprint `render.yaml` is ready for deploy.

## UptimeRobot Setup (required on free)

**Monitor 1 - Keep Alive (every 5 min):**
- Type: HTTP(s)
- URL: `https://your-app.onrender.com/health`
- Method: GET

**Monitor 2 - Auto Upload (every 60 min):**
- Type: HTTP(s)
- URL: `https://your-app.onrender.com/auto?secret=YOUR_SECRET&amount=5`
- Method: GET
- Note: 90s rate limit prevents double upload.

Logs in JSON response; also `GET /logs`, `GET /stats`.

## Endpoints
- `GET /` - info + config
- `GET /health` - 200 for UptimeRobot, shows `free_tier_no_disk:true`
- `GET /upload?target=&amount=&dry_run=1&force=1&secret=` - main (logs in resp)
- `GET /auto` - alias for UptimeRobot
- `GET /config` - merged config
- `GET /logs` - last 50 logs
- `GET /stats` - dedup count + store_path

## Flow
1. `bot/client.py:get_client()` loads `session.json` -> `cl.login_by_sessionid()` (tested @ts_not_tuff_lil_bro)
2. `bot/feed.py:scrape_feed()` -> `cl.user_medias()` or `cl.get_timeline_feed()` -> filter `media_type==2` non-ads
3. `bot/store.py:load_posted_ids()` (local `/tmp`) + `is_duplicate_via_profile()` (profile ` #src_code` check)
4. `bot/downloader.py:download_video()` -> `/tmp/instabot/{pk}.mp4` (ephemeral OK)
5. `bot/uploader.py:build_caption(..., source_code, add_marker)` -> `cl.clip_upload()` fallback `cl.video_upload()`
6. Save dedup to `/tmp` + profile marker ensures cold-start safety

## Verified 2026-09-05
- Login via `session.json:14` -> @ts_not_tuff_lil_bro
- `target=instagram` -> 2 reels fetched
- timeline -> 1 video parsed via `feed_items` + `media_info`
- Dry run JSON serializes `HttpUrl` -> `str` correctly
