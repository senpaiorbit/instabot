import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

def log_msg(msg: str, logs: list = None):
    logger.info(msg)
    if logs is not None:
        logs.append(msg)

def scrape_feed(cl, amount: int = 10, target_username: str = None, logs: list = None):
    """
    Scrape feed videos.
    If target_username is None -> scrape authenticated user's timeline feed.
    Else -> scrape target user's medias.
    Returns tuple: (all_medias, videos)
    """
    medias = []
    try:
        if target_username:
            log_msg(f"Scraping @{target_username} medias (amount={amount})", logs)
            user_id = cl.user_id_from_username(target_username)
            log_msg(f"Resolved @{target_username} -> pk={user_id}", logs)
            raw = cl.user_medias(user_id, amount=amount)
        else:
            log_msg(f"Scraping timeline feed (amount={amount})", logs)
            # get_timeline_feed in 2.18 returns Dict, need to parse feed_items
            feed_dict = cl.get_timeline_feed()
            log_msg(f"get_timeline_feed returned keys: {list(feed_dict.keys())[:6]}", logs)
            raw = []
            # Try to parse feed_items -> extract Media via private API helper
            if isinstance(feed_dict, dict) and "feed_items" in feed_dict:
                parsed = 0
                for item in feed_dict.get("feed_items", []):
                    media_or_ad = item.get("media_or_ad") or item.get("media")
                    if media_or_ad:
                        try:
                            # Use private extractor if available
                            if hasattr(cl, "_parse_media"):
                                # not exist, try media_info path
                                pass
                            # Best effort: try to inject via cl.media_info using pk
                            pk = media_or_ad.get("pk") or media_or_ad.get("id")
                            if pk:
                                try:
                                    m = cl.media_info(str(pk).split("_")[0])  # media pk without user id suffix
                                    raw.append(m)
                                    parsed += 1
                                    if len(raw) >= amount:
                                        break
                                except Exception as e:
                                    log_msg(f"media_info parse failed for {pk}: {e}", logs)
                        except Exception as e:
                            log_msg(f"feed_items parse error: {e}", logs)
                log_msg(f"Parsed {parsed} medias from feed_items", logs)
            else:
                log_msg(f"Unexpected feed_dict format: {str(feed_dict)[:300]}", logs)

            # Fallback: if parsing failed, use self medias (still videos from following)
            if not raw:
                log_msg(f"Fallback: trying cl.user_medias for self (feed parse yielded 0)", logs)
                try:
                    self_id = cl.user_id
                    raw = cl.user_medias(self_id, amount=amount)
                    log_msg(f"Fallback self medias fetched {len(raw)}", logs)
                except Exception as e:
                    log_msg(f"Fallback failed: {e}", logs)
                    raw = []
                # If still empty, try explore? last resort: try to get home feed via private pagination
                if not raw:
                    try:
                        # Try pagination via get_timeline_feed with max_id
                        log_msg("Attempting pagination fetch via get_timeline_feed next_max_id", logs)
                        next_id = feed_dict.get("next_max_id")
                        if next_id:
                            feed2 = cl.get_timeline_feed(max_id=next_id)
                            log_msg(f"Second page keys: {list(feed2.keys())[:5]}", logs)
                    except Exception as e:
                        log_msg(f"Pagination attempt failed: {e}", logs)

        log_msg(f"Fetched {len(raw)} raw medias", logs)

        for m in raw:
            # m is Media object
            try:
                media_type = getattr(m, "media_type", 0)
                # 1=photo, 2=video, 8=album
                # We want videos/reels only
                is_video = media_type == 2
                # For album, check if contains video
                video_url = getattr(m, "video_url", None)
                if video_url is not None:
                    video_url = str(video_url)
                thumbnail_url = getattr(m, "thumbnail_url", None)
                if thumbnail_url is not None:
                    thumbnail_url = str(thumbnail_url)
                caption = getattr(m, "caption_text", "") or ""
                code = getattr(m, "code", "")
                mid = getattr(m, "id", "") or getattr(m, "pk", "")
                username = getattr(getattr(m, "user", None), "username", "unknown")

                # If album with video, try to extract first video
                if media_type == 8 and hasattr(m, "resources"):
                    for res in m.resources:
                        if getattr(res, "media_type", 0) == 2 and getattr(res, "video_url", None):
                            is_video = True
                            video_url = str(res.video_url)
                            thumbnail_url = str(res.thumbnail_url) if getattr(res, "thumbnail_url", None) else thumbnail_url
                            break

                medias.append({
                    "id": str(mid),
                    "pk": str(getattr(m, "pk", mid)),
                    "code": code,
                    "media_type": media_type,
                    "is_video": is_video,
                    "caption": caption,
                    "video_url": video_url,
                    "thumbnail_url": thumbnail_url,
                    "username": username,
                    "like_count": getattr(m, "like_count", 0),
                    "comment_count": getattr(m, "comment_count", 0),
                    "taken_at": str(getattr(m, "taken_at", "")),
                    "product_type": getattr(m, "product_type", ""),
                    "_raw": m,
                })
                log_msg(f" - [{mid}] @{username} type={media_type} is_video={is_video} code={code} caption={caption[:40]}", logs)
            except Exception as e:
                log_msg(f"Failed to parse media {getattr(m,'id','?')}: {e}", logs)

        # Filter videos only for upload - skip ads
        videos = [x for x in medias if x["is_video"] and x["video_url"] and x.get("product_type") != "ad"]
        skipped_ads = len([x for x in medias if x.get("product_type") == "ad"])
        if skipped_ads:
            log_msg(f"Skipped {skipped_ads} ad videos", logs)
        log_msg(f"Filtered {len(videos)} videos with downloadable URL out of {len(medias)}", logs)
        return medias, videos

    except Exception as e:
        log_msg(f"scrape_feed error: {e}", logs)
        raise


def get_next_video_not_posted(videos: List[Dict], posted_ids: set, logs: list = None):
    """Pick first video not in posted_ids"""
    for v in videos:
        if v["id"] not in posted_ids and v["pk"] not in posted_ids and v["code"] not in posted_ids:
            log_msg(f"Selected video {v['id']} ({v['code']}) not posted yet", logs)
            return v
    log_msg("No new videos found (all already posted)", logs)
    return None
