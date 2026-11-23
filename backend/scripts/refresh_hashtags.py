"""Re-pick niche (viral) hashtags for existing clips that are not uploaded or removed yet.

Only the hashtag lists change; titles, captions and descriptions (including your edits) are kept.

    .venv/Scripts/python.exe scripts/refresh_hashtags.py            # apply
    .venv/Scripts/python.exe scripts/refresh_hashtags.py --dry-run  # show what would change
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import GeneratedClip, VideoSource
    from app.modules.metadata.viral_tags import detect_niche, viral_hashtags

    changed = 0
    with SessionLocal() as db:
        clips = db.scalars(select(GeneratedClip).where(GeneratedClip.removed_at.is_(None)))
        for clip in clips:
            if (clip.upload_status or "none") == "uploaded":
                continue  # already on YouTube: leave its record as published
            source = db.get(VideoSource, clip.video_source_id)
            niche = detect_niche(clip.transcript_text or "", category=clip.category,
                                 source_title=source.title if source else None)
            meta = dict(clip.social_metadata or {})
            yt, ig = dict(meta.get("youtube") or {}), dict(meta.get("instagram") or {})
            new_yt, new_ig = viral_hashtags(niche, "youtube"), viral_hashtags(niche, "instagram")
            if yt.get("hashtags") == new_yt and ig.get("hashtags") == new_ig:
                continue
            print(f"{(clip.title or '')[:50]:50} [{niche.niche}] {yt.get('hashtags')} -> {new_yt}")
            yt["hashtags"], ig["hashtags"] = new_yt, new_ig
            meta["youtube"], meta["instagram"] = yt, ig
            clip.social_metadata = meta
            changed += 1
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
    print(f"{changed} clip(s) {'would be ' if args.dry_run else ''}updated")


if __name__ == "__main__":
    main()
