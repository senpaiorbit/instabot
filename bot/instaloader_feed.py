"""
Combine Instaloader for personalized reel scrape + instagrapi for upload.
Instaloader docs: https://instaloader.github.io/
We use Instaloader.get_feed_posts() which returns personalized feed (followed accounts)
and handles reels via Post.is_video / Post.typename == "GraphVideo"/"GraphClips".
Session is imported from instagrapi's session.json (sessionid cookie) to avoid double login.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict

logger = logging.getLogger(__name__)

def log_msg(msg: str, logs: list = None):
    logger.info(msg)
    if logs is not None:
        logs.append(msg)

def _get_sessionid_from_sessionjson() -> str:
    """Extract sessionid from instagrapi session.json"""
    import os
    from bot.client import load_session_dict
    try:
        s = load_session_dict()
        sid = s.get("authorization_data", {}).get("sessionid", "")
        # URL decode
        import urllib.parse
        return urllib.parse.unquote(sid) if sid else ""
    except Exception as e:
        logger.warning(f"Could not get sessionid: {e}")
        return ""

def _make_instaloader_with_session():
    """Create Instaloader instance and inject sessionid cookie (no password)"""
    try:
        import instaloader
    except ImportError as e:
        raise RuntimeError("instaloader not installed. Add to requirements.txt: instaloader==4.15.3") from e

    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        quiet=True,
    )
    # Inject sessionid from instagrapi session.json
    sessionid = _get_sessionid_from_sessionjson()
    if sessionid:
        # Instaloader uses .context._session
        try:
            L.context._session.cookies.set("sessionid", sessionid, domain=".instagram.com", path="/")
            # Also set ds_user_id if available
            from bot.client import load_session_dict
            s = load_session_dict()
            ds_user_id = s.get("authorization_data", {}).get("ds_user_id")
            if ds_user_id:
                L.context._session.cookies.set("ds_user_id", str(ds_user_id), domain=".instagram.com", path="/")
            # Try to set csrftoken/rur if present
            cookies = s.get("cookies", {})
            if cookies.get("rur"):
                import urllib.parse
                rur = urllib.parse.unquote(cookies["rur"])
                L.context._session.cookies.set("rur", rur, domain=".instagram.com", path="/")
            # Set username from session
            # need to get username via API or from session?
            # Instaloader needs test login to validate
            try:
                # This will validate via API without password
                username = L.test_login()
                log_msg(f"Instaloader test_login as {username}", None)
            except Exception as e:
                # test_login may fail if session is challenge, but we still try feed
                logger.warning(f"Instaloader test_login failed: {e}")
        except Exception as e:
            logger.warning(f"Failed to inject sessionid into Instaloader: {e}")
    return L

def scrape_personalized_reels_instaloader(amount: int = 10, logs: list = None) -> List[Dict]:
    """
    Use Instaloader.get_feed_posts() for personalized feed (followed accounts)
    Returns list of dicts compatible with feed.py's videos format:
    {id, pk, code, media_type, is_video, caption, video_url, thumbnail_url, username, product_type, _raw}
    Filters to video/reels only.
    """
    try:
        import instaloader
    except ImportError:
        log_msg("instaloader not installed, skipping", logs)
        return []

    L = _make_instaloader_with_session()
    medias = []
    try:
        log_msg(f"Instaloader: fetching personalized feed (amount={amount}) via get_feed_posts()", logs)
        # get_feed_posts returns iterator of Post
        count = 0
        for post in L.get_feed_posts():
            try:
                # Post attributes: shortcode, mediaid, is_video, typename, caption, etc.
                is_video = getattr(post, "is_video", False)
                typename = getattr(post, "typename", "")
                # Reels are GraphVideo with product_type clips, but we filter is_video
                if not is_video:
                    continue
                # Only reels/clips, not regular video - check typename
                # GraphVideo can be regular video or reel; we accept all video for now
                # If you want strict reels: typename == "GraphClips" or post.product_type == "clips"
                # Instaloader Post doesn't have product_type, so we use is_video
                code = getattr(post, "shortcode", "")
                pk = str(getattr(post, "mediaid", ""))
                caption = getattr(post, "caption", "") or ""
                owner = getattr(post, "owner_username", "") or getattr(getattr(post, "owner_profile", None), "username", "unknown")
                video_url = getattr(post, "video_url", None)
                # video_url may be None until loaded; try to get
                if not video_url:
                    try:
                        video_url = post.video_url
                    except:
                        video_url = None
                # Fallback: use url
                if not video_url:
                    video_url = getattr(post, "url", None)
                thumbnail_url = getattr(post, "url", None)  # thumbnail is url for image
                # Try to get display url
                try:
                    thumbnail_url = post.url
                except:
                    pass

                medias.append({
                    "id": pk,
                    "pk": pk,
                    "code": code,
                    "media_type": 2,
                    "is_video": True,
                    "caption": caption,
                    "video_url": str(video_url) if video_url else None,
                    "thumbnail_url": str(thumbnail_url) if thumbnail_url else None,
                    "username": owner,
                    "like_count": getattr(post, "likes", 0),
                    "comment_count": getattr(post, "comments", 0),
                    "taken_at": str(getattr(post, "date_utc", "")),
                    "product_type": "clips" if typename == "GraphClips" else "feed",
                    "_raw": post,
                    "_source": "instaloader_feed",
                })
                log_msg(f" - [{pk}] @{owner} code={code} caption={caption[:40]}", logs)
                count += 1
                if count >= amount:
                    break
            except Exception as e:
                log_msg(f"Failed to parse feed post: {e}", logs)
                continue
        log_msg(f"Instaloader fetched {len(medias)} videos from personalized feed", logs)
        return medias
    except Exception as e:
        log_msg(f"Instaloader get_feed_posts failed: {e}", logs)
        # Fallback: try get_explore_posts which is also personalized
        try:
            log_msg("Trying Instaloader get_explore_posts as fallback", logs)
            medias = []
            for post in L.get_explore_posts():
                if getattr(post, "is_video", False):
                    code = getattr(post, "shortcode", "")
                    pk = str(getattr(post, "mediaid", ""))
                    medias.append({
                        "id": pk, "pk": pk, "code": code, "media_type": 2, "is_video": True,
                        "caption": getattr(post, "caption", "") or "",
                        "video_url": str(getattr(post, "video_url", "") or ""),
                        "thumbnail_url": str(getattr(post, "url", "") or ""),
                        "username": getattr(post, "owner_username", "unknown"),
                        "product_type": "clips", "_raw": post, "_source": "instaloader_explore"
                    })
                    if len(medias) >= amount:
                        break
            log_msg(f"Instaloader explore fetched {len(medias)}", logs)
            return medias
        except Exception as e2:
            log_msg(f"Instaloader explore also failed: {e2}", logs)
            return []
