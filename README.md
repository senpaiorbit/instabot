# InstaBot2 - Render

Web service bot that logs in via `session.json`, scrapes feed, and re-uploads video to your account.

## Global Config
Edit `config.json` (committed) + env vars override it.

`config.json:1`:
```json
{
  "target_username": null,      // null = timeline feed, or "instagram" to scrape that user
  "scrape_amount": 10,
  "extra_caption": "Follow for more 🔥",
  "caption_template": "{original}\n\n🎥 via @{username}",
  "rate_limit_seconds": 90,
  "port": 10000
}
```

Priority: `?target=` query > `TARGET_USERNAME` env > `config.json`

## Local Run
```bash
pip install -r requirements.txt
python app.py
# health
curl http://localhost:10000/health
# dry run scrape only
curl "http://localhost:10000/upload?dry_run=1&amount=2&target=instagram"
# real upload (uses dedup)
curl "http://localhost:10000/upload"
# with secret
curl -H "X-Upload-Secret: mysecret" http://localhost:10000/upload
```

## Render Deploy
1. Push to GitHub
2. Render Dashboard -> New Web Service -> Connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300`
5. Health Check: `/health`
6. Env Vars:
   - `SESSION_JSON` = entire content of `session.json` (as JSON string, not path). Or base64.
   - `TARGET_USERNAME` = username to scrape (leave empty for timeline)
   - `UPLOAD_SECRET` = optional, to protect /upload
   - `PYTHON_VERSION`=3.11.0

Alternative via `render.yaml` Blueprint deploy.

## UptimeRobot Setup (no cron)
Render free spins down after 15m. Use UptimeRobot:

1. Monitor 1 - Keep Alive (every 5 min):
   - Type: HTTP(s)
   - URL: `https://your-app.onrender.com/health`
   - Method: GET

2. Monitor 2 - Auto Upload (every 60 min):
   - Type: HTTP(s)
   - URL: `https://your-app.onrender.com/auto?secret=YOUR_SECRET&amount=5`
   - Or `/upload` - same
   - Method: GET
   - Note: rate_limited (90s) prevents double upload if both monitors hit close.

Logs returned in JSON; also view `/logs` and `/stats`.

## Endpoints
- `GET /` - info
- `GET /health` - for UptimeRobot keep-alive, returns 200 even if degraded
- `GET /upload?target=&amount=&dry_run=1&force=1` - main, with logs in response
- `GET /auto` - alias for UptimeRobot
- `GET /config` - show merged config
- `GET /logs` - last 50 logs
- `GET /stats` - dedup count

## Flow
1. `bot/client.py:get_client()` loads `session.json` -> `cl.login_by_sessionid()` -> verified as @ts_not_tuff_lil_bro
2. `bot/feed.py:scrape_feed()` -> `cl.user_medias()` or `cl.get_timeline_feed()` -> filter `media_type==2` videos
3. `bot/store.py` dedup check
4. `bot/downloader.py:download_video()` -> `/tmp/instabot/{pk}.mp4`
5. `bot/uploader.py:upload_video()` -> `cl.clip_upload()` fallback `cl.video_upload()`
6. Save dedup + rate limit lock

## Notes
- Timeline feed on 2026-09-05 fetched 1 video correctly; target `@instagram` fetched 2 reels (verified).
- Ad videos are auto-skipped.
- If session expires -> `/health` shows `session_loaded:false`, `/upload` returns 500 with `ChallengeRequired`.
