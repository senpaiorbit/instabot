import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def log_msg(msg: str, logs=None):
    logger.info(msg)
    if logs is not None:
        logs.append(msg)

def build_caption(original: str, username: str = None, extra: str = None, template: str = None) -> str:
    """Build new caption preserving original, add credit via template"""
    original = (original or "").strip()
    if template and "{original}" in template:
        try:
            base = template.format(original=original, username=username or "unknown", extra=extra or "")
            # clean double newlines if original empty
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

    # Try Reel (clip) first - most videos are reels now
    last_err = None
    for method in ["clip_upload", "video_upload"]:
        try:
            func = getattr(cl, method)
            log_msg(f"Trying {method}...", logs)
            if tp:
                media = func(str(vp), caption=caption, thumbnail=str(tp))
            else:
                media = func(str(vp), caption=caption)
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
                    media = func(str(vp), caption=caption)
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
