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
            raw = []
            # Paginate timeline feed to collect enough medias (feed is ad-heavy, need 2-3 pages)
            next_max_id = None
            pages = 0
            max_pages = 3
            seen_pks = set()
            while len(raw) < amount and pages < max_pages:
                try:
                    if next_max_id is None:
                        feed_dict = cl.get_timeline_feed()
                    else:
                        feed_dict = cl.get_timeline_feed(max_id=next_max_id)
                    pages += 1
                    feed_items = feed_dict.get("feed_items", [])
                    num_results = feed_dict.get("num_results", len(feed_items))
                    next_max_id = feed_dict.get("next_max_id")
                    more_available = feed_dict.get("more_available", False)
                    log_msg(f"Page {pages}: feed_items={len(feed_items)} num_results={num_results} more={more_available} next_max_id={str(next_max_id)[:20] if next_max_id else None}", logs)
                    if not feed_items:
                        log_msg("No feed_items in page, stopping pagination", logs)
                        break
                    # Upstream debug: log sample keys of first item when parsed 0
                    if pages == 1 and len(feed_items) > 0:
                        sample = feed_items[0]
                        log_msg(f"Sample feed_item keys: {list(sample.keys())}", logs)
                        # Try to show media_or_ad structure
                        try:
                            moa = sample.get("media_or_ad") or sample.get("media") or {}
                            log_msg(f"Sample media_or_ad keys: {list(moa.keys())[:10]} pk={moa.get('pk')} product_type={moa.get('product_type')} ad_metadata={bool(moa.get('ad_metadata'))}", logs)
                            # Also check nested media if exists
                            if "media" in moa and isinstance(moa["media"], dict):
                                log_msg(f"Nested media keys: {list(moa['media'].keys())[:10]}", logs)
                        except Exception as e:
                            log_msg(f"Sample logging failed: {e}", logs)

                    parsed_this_page = 0
                    skipped_ads = 0
                    skipped_no_pk = 0
                    for idx, item in enumerate(feed_items):
                        try:
                            media_or_ad = item.get("media_or_ad") or item.get("media")
                            if not media_or_ad:
                                # Could be suggested user or other
                                log_msg(f"Item {idx} has no media_or_ad/media, keys={list(item.keys())}", logs)
                                continue
                            # Handle nested media inside media_or_ad (some feed versions wrap)
                            # If media_or_ad contains 'media' dict, unwrap
                            if "media" in media_or_ad and isinstance(media_or_ad["media"], dict) and "pk" not in media_or_ad:
                                media_or_ad = media_or_ad["media"]
                            # Skip ads early
                            if "ad_metadata" in media_or_ad or media_or_ad.get("product_type") == "ad" or "ad" in str(media_or_ad.get("pk","")):
                                skipped_ads += 1
                                log_msg(f"Skipping ad idx={idx} pk={media_or_ad.get('pk')} product_type={media_or_ad.get('product_type')}", logs)
                                continue
                            pk = media_or_ad.get("pk") or media_or_ad.get("id")
                            if not pk:
                                skipped_no_pk += 1
                                log_msg(f"Item {idx} no pk, keys={list(media_or_ad.keys())[:8]}", logs)
                                continue
                            pk_str = str(pk).split("_")[0]
                            if pk_str in seen_pks:
                                continue
                            seen_pks.add(pk_str)
                            # Try media_info, but also log if it looks like video
                            is_video_hint = bool(media_or_ad.get("video_versions") or media_or_ad.get("video_url") or media_or_ad.get("media_type")==2)
                            log_msg(f"Fetching media_info for idx={idx} pk={pk_str} video_hint={is_video_hint}", logs)
                            try:
                                m = cl.media_info(pk_str)
                                raw.append(m)
                                parsed_this_page += 1
                                log_msg(f"Parsed ok pk={pk_str} code={getattr(m,'code','?')} type={getattr(m,'media_type','?')}", logs)
                                if len(raw) >= amount:
                                    break
                            except Exception as e:
                                log_msg(f"media_info failed for {pk_str}: {e} – trying dict fallback", logs)
                                # If dict already looks like video, try to use it without extra call
                                # Mark as failed but continue
                                continue
                        except Exception as e:
                            log_msg(f"Item {idx} parse exception: {e}", logs)
                            continue
                    log_msg(f"Page {pages} parsed {parsed_this_page} medias skipped_ads={skipped_ads} no_pk={skipped_no_pk} (total {len(raw)}/{amount})", logs)
                    if not more_available or not next_max_id:
                        log_msg("No more pages available", logs)
                        break
                    if len(raw) >= amount:
                        break
                except Exception as e:
                    log_msg(f"Timeline pagination error page {pages}: {e}", logs)
                    break

            log_msg(f"Timeline pagination done: {len(raw)} total raw, pages={pages}", logs)

            # If still 0 and filtered will be 0, log hint
            if not raw:
                log_msg(f"Fallback: timeline yielded 0 raw, trying cl.user_medias for self as last resort", logs)
                try:
                    self_id = cl.user_id
                    raw = cl.user_medias(self_id, amount=amount)
                    log_msg(f"Fallback self medias fetched {len(raw)}", logs)
                except Exception as e:
                    log_msg(f"Fallback failed: {e}", logs)
                    raw = []

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
        if len(videos) == 0 and len(medias) > 0:
            log_msg(f"HINT: Timeline feed is ad-heavy. Set TARGET_USERNAME in config.json:2 or Render env to scrape a specific user (e.g. \"instagram\") for reliable videos. Or increase scrape_amount (now {len(medias)} raw, try amount=20).", logs)
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
