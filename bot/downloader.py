import os
import requests
import logging
from pathlib import Path
import mimetypes

logger = logging.getLogger(__name__)

TMP_DIR = Path("/tmp/instabot")
TMP_DIR.mkdir(parents=True, exist_ok=True)

def log_msg(msg: str, logs: list = None):
    logger.info(msg)
    if logs is not None:
        logs.append(msg)

def download_video(cl, media: dict, logs: list = None) -> dict:
    """
    Download video file for media dict.
    Tries cl.video_download first, fallback to requests stream on video_url.
    Returns dict with paths: video_path, thumbnail_path
    """
    media_id = media["id"]
    pk = media["pk"]
    video_url = media["video_url"]
    thumb_url = media.get("thumbnail_url")

    video_path = TMP_DIR / f"{pk}.mp4"
    thumb_path = TMP_DIR / f"{pk}.jpg"

    # Method 1: instagrapi native (handles private API + headers)
    try:
        log_msg(f"Downloading video {media_id} via cl.video_download_by_url...", logs)
        # video_download needs pk or media_id; try helper
        # cl.video_download(media.pk) expects Media pk
        downloaded = cl.video_download(media["pk"], folder=str(TMP_DIR))
        # it returns Path
        if downloaded and Path(downloaded).exists():
            # Move/rename to our expected path if needed
            if Path(downloaded) != video_path:
                try:
                    Path(downloaded).rename(video_path)
                except:
                    video_path = Path(downloaded)
            log_msg(f"Downloaded via instagrapi to {video_path} ({video_path.stat().st_size} bytes)", logs)
        else:
            raise FileNotFoundError("video_download returned no file")
    except Exception as e:
        log_msg(f"instagrapi download failed: {e} -> fallback to requests", logs)
        # Method 2: direct requests with IG headers
        try:
            headers = {
                "User-Agent": cl.user_agent if hasattr(cl, "user_agent") else "Mozilla/5.0",
            }
            # Add session cookies if available
            cookies = {}
            try:
                cookies = {k: v for k, v in cl.private.cookies.items()}
            except:
                pass
            log_msg(f"Fetching video_url: {video_url[:80]}...", logs)
            with requests.get(video_url, headers=headers, cookies=cookies, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(video_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            log_msg(f"Downloaded via requests to {video_path} ({video_path.stat().st_size} bytes)", logs)
        except Exception as e2:
            log_msg(f"requests download failed: {e2}", logs)
            raise RuntimeError(f"Failed to download video {media_id}: {e2}") from e

    # Download thumbnail - custom cover support: /cover/{n}.{jpg,jpeg,png,webp} via ?cover=n or config
    # Check for custom cover request from global or caller
    custom_cover = None
    try:
        # Look for cover param in logs context? caller can set via media dict or env
        # Also support query via env var CUSTOM_COVER
        custom_n = os.getenv("CUSTOM_COVER") or (media.get("_cover") if isinstance(media, dict) else None)
        cover_dir = Path(__file__).parent.parent / "cover"
        if custom_n:
            # Try exact file or with extension
            for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                p = cover_dir / f"{custom_n}{ext}"
                if p.exists():
                    custom_cover = p
                    break
                # Also try direct filename
                p2 = cover_dir / str(custom_n)
                if p2.exists():
                    custom_cover = p2
                    break
        # If no specific, try to find any cover in folder
        if not custom_cover and cover_dir.exists():
            covers = sorted(cover_dir.glob("*.*"))
            # Filter image types
            covers = [c for c in covers if c.suffix.lower() in [".jpg",".jpeg",".png",".webp"]]
            if covers:
                # Pick by pk hash to be deterministic but varied
                try:
                    idx = int(str(pk)[-2:]) % len(covers) if pk and str(pk).isdigit() else 0
                except:
                    idx = 0
                custom_cover = covers[idx]
                log_msg(f"Auto-picked custom cover {custom_cover.name} from {len(covers)} covers", logs)
    except Exception as e:
        log_msg(f"Custom cover lookup failed: {e}", logs)

    if custom_cover and custom_cover.exists():
        try:
            import shutil
            shutil.copy(custom_cover, thumb_path)
            log_msg(f"Using custom cover {custom_cover} -> {thumb_path} ({custom_cover.stat().st_size} bytes)", logs)
            thumb_url = None  # skip remote download
        except Exception as e:
            log_msg(f"Custom cover copy failed: {e}", logs)

    if thumb_url and not (thumb_path.exists() and custom_cover):
        try:
            log_msg(f"Downloading thumbnail {thumb_url[:80]}...", logs)
            r = requests.get(thumb_url, timeout=30)
            r.raise_for_status()
            with open(thumb_path, "wb") as f:
                f.write(r.content)
            log_msg(f"Thumbnail saved to {thumb_path}", logs)
        except Exception as e:
            log_msg(f"Thumbnail download failed: {e} (will use default)", logs)
            if custom_cover and custom_cover.exists():
                import shutil
                shutil.copy(custom_cover, thumb_path)
                log_msg(f"Used custom cover fallback {custom_cover}", logs)
            else:
                # Try legacy fallback cover/2.jpg
                local_cover = Path(__file__).parent.parent / "cover" / "2.jpg"
                if local_cover.exists():
                    import shutil
                    shutil.copy(local_cover, thumb_path)
                    log_msg(f"Used local cover/2.jpg as thumbnail", logs)
                else:
                    thumb_path = None
    elif not thumb_url and not thumb_path.exists():
        if custom_cover and custom_cover.exists():
            import shutil
            shutil.copy(custom_cover, thumb_path)
            log_msg(f"Used custom cover {custom_cover} (no thumb_url)", logs)
        else:
            thumb_path = None
            log_msg("No thumbnail_url and no custom cover available", logs)

    return {
        "video_path": str(video_path) if video_path.exists() else None,
        "thumbnail_path": str(thumb_path) if thumb_path and Path(thumb_path).exists() else None,
        "size_bytes": video_path.stat().st_size if video_path.exists() else 0
    }
