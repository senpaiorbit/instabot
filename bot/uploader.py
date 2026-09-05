import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def log_msg(msg: str, logs=None):
    logger.info(msg)
    if logs is not None:
        logs.append(msg)

def build_caption(original: str, username: str = None, extra: str = None, template: str = None, source_code: str = None, add_marker: bool = True) -> str:
    """Build new caption preserving original, add credit via template. Adds #src_{code} marker for free-tier dedup."""
    original = (original or "").strip()
    if template and "{original}" in template:
        try:
            base = template.format(original=original, username=username or "unknown", extra=extra or "")
            if not original:
                base = base.replace("{original}", "").strip()
        except Exception:
            base = original
            if username:
                base += f"\n\n🎥 via @{username}"
            if extra:
                base += f"\n\n{extra}"
    else:
        base = original
        if username:
            credit = f"\n\n🎥 via @{username}"
            if credit not in base:
                base += credit
        if extra:
            base += f"\n\n{extra}"
    # Add source marker for free-tier dedup (hidden at end, survives cold start)
    if add_marker and source_code:
        marker = f"#src_{source_code}"
        if marker not in base:
            base += f"\n{marker}"
    if not base.strip():
        base = "🔥 #repost #viral"
    return base[:2200]

def upload_video(cl, video_path: str, thumbnail_path: str = None, caption: str = "", logs=None) -> dict:
    """
    Upload video as Reel/Feed video.
    Tries clip_upload (Reel) first, fallback to video_upload.
    Returns dict with media info.
    """
    vp = Path(video_path)
    if not vp.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    tp = Path(thumbnail_path) if thumbnail_path and Path(thumbnail_path).exists() else None

    log_msg(f"Uploading {vp} ({vp.stat().st_size} bytes) caption_len={len(caption)} thumb={tp}", logs)

    # Try video_upload first on free tier (512MB) - clip_upload does ffmpeg analyze + long sleep and OOMs on Render free
    # Use short configure_timeout to avoid gunicorn WORKER TIMEOUT (default 30s)
    last_err = None
    for method in ["video_upload", "clip_upload"]:
        try:
            func = getattr(cl, method)
            log_msg(f"Trying {method} (free-tier optimized)...", logs)
            # Pass short timeout for clip to avoid blocking worker
            kwargs = {"caption": caption}
            if tp:
                kwargs["thumbnail"] = str(tp)
            # Reduce sleep for clip to avoid WORKER TIMEOUT on gunicorn sync worker
            if method == "clip_upload":
                kwargs["configure_timeout"] = 5  # was 10, loop 50*10=500s -> OOM/timeout
            if tp:
                media = func(str(vp), **kwargs)
            else:
                media = func(str(vp), **kwargs)
            # media is Media object
            mid = getattr(media, "id", getattr(media, "pk", "unknown"))
            code = getattr(media, "code", "")
            log_msg(f"✅ Uploaded via {method}: id={mid} code={code} https://instagram.com/p/{code}/", logs)
            return {
                "success": True,
                "method": method,
                "id": str(mid),
                "pk": str(getattr(media, "pk", mid)),
                "code": code,
                "url": f"https://instagram.com/p/{code}/" if code else None,
                "caption": caption,
            }
        except Exception as e:
            last_err = e
            log_msg(f"{method} failed: {e}", logs)
            # If thumbnail caused failure, try without
            if tp and "thumbnail" in str(e).lower():
                try:
                    log_msg(f"Retrying {method} without thumbnail...", logs)
                    retry_kwargs = {"caption": caption}
                    if method == "clip_upload":
                        retry_kwargs["configure_timeout"] = 5
                    media = func(str(vp), **retry_kwargs)
                    mid = getattr(media, "id", getattr(media, "pk", "unknown"))
                    code = getattr(media, "code", "")
                    log_msg(f"✅ Uploaded via {method} (no thumb): id={mid} code={code}", logs)
                    return {
                        "success": True,
                        "method": method,
                        "id": str(mid),
                        "pk": str(getattr(media, "pk", mid)),
                        "code": code,
                        "url": f"https://instagram.com/p/{code}/" if code else None,
                        "caption": caption,
                    }
                except Exception as e2:
                    last_err = e2
                    log_msg(f"{method} without thumb also failed: {e2}", logs)

    raise RuntimeError(f"All upload methods failed. Last error: {last_err}")
