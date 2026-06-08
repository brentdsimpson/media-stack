#!/usr/bin/env python3
"""Delete Plex media watched more than a configurable number of hours ago."""

import argparse
import os
import sys
import time
import requests


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-path", default=None, help="Override LIBRARY_PATH env var")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Enable dry run")
    parser.add_argument("--no-dry-run", action="store_true", default=None, help="Disable dry run")
    parser.add_argument("--watched-age-hours", type=int, default=None, help="Override WATCHED_AGE_HOURS env var")
    return parser.parse_args()


ARGS = parse_args()

PLEX_URL = os.environ.get("PLEX_URL", "http://127.0.0.1:32400")
PLEX_TOKEN = os.environ["PLEX_TOKEN"]
LIBRARY_PATH = ARGS.library_path or os.environ.get("LIBRARY_PATH", "/home/brentsimpson/Videos/Plex/TV Shows")

if ARGS.dry_run:
    DRY_RUN = True
elif ARGS.no_dry_run:
    DRY_RUN = False
else:
    DRY_RUN = os.environ.get("DRY_RUN", "true").lower() in ("true", "1", "yes")

WATCHED_AGE_HOURS = ARGS.watched_age_hours or int(os.environ.get("WATCHED_AGE_HOURS", "24"))

# Comma-separated collection names whose items are never deleted
SKIP_COLLECTIONS = [c.strip() for c in os.environ.get("SKIP_COLLECTIONS", "Classics").split(",") if c.strip()]

HEADERS = {"X-Plex-Token": PLEX_TOKEN, "Accept": "application/json"}
CUTOFF = time.time() - (WATCHED_AGE_HOURS * 3600)

# Plex API type IDs and supported library types
LIBRARY_TYPE_MAP = {
    "show": {"item_type": 4, "label": "episodes"},   # type=4 is episodes
    "movie": {"item_type": 1, "label": "movies"},     # type=1 is movies
}


def get_section():
    """Find the library section whose path matches LIBRARY_PATH. Returns (key, type)."""
    resp = requests.get(f"{PLEX_URL}/library/sections", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    for section in resp.json()["MediaContainer"]["Directory"]:
        if section["type"] not in LIBRARY_TYPE_MAP:
            continue
        for loc in section["Location"]:
            if loc["path"].rstrip("/") == LIBRARY_PATH.rstrip("/"):
                return section["key"], section["type"]
    return None, None


def get_skip_ratingkeys(section_id):
    """Return a set of ratingKeys for items in any SKIP_COLLECTIONS collection.

    For movie libraries these are movie ratingKeys.
    For show libraries these are show ratingKeys (episodes are matched via
    grandparentRatingKey in should_skip()).
    """
    if not SKIP_COLLECTIONS:
        return set()

    resp = requests.get(
        f"{PLEX_URL}/library/sections/{section_id}/collections",
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()

    skip_keys = set()
    for coll in resp.json().get("MediaContainer", {}).get("Metadata", []):
        if coll.get("title") not in SKIP_COLLECTIONS:
            continue
        coll_key = coll["ratingKey"]
        items_resp = requests.get(
            f"{PLEX_URL}/library/collections/{coll_key}/children",
            headers=HEADERS,
            timeout=30,
        )
        items_resp.raise_for_status()
        for item in items_resp.json().get("MediaContainer", {}).get("Metadata", []):
            skip_keys.add(item["ratingKey"])

    return skip_keys


def should_skip(item, library_type, skip_keys):
    """Return True if this item belongs to a protected collection."""
    if not skip_keys:
        return False
    if library_type == "show":
        # Collections hold shows; episodes reference the show via grandparentRatingKey
        return item.get("grandparentRatingKey") in skip_keys
    return item["ratingKey"] in skip_keys


def get_watched_items(section_id, item_type):
    """Yield items that were watched before the cutoff."""
    start = 0
    size = 200
    while True:
        resp = requests.get(
            f"{PLEX_URL}/library/sections/{section_id}/all",
            headers=HEADERS,
            params={"type": item_type, "X-Plex-Container-Start": start, "X-Plex-Container-Size": size},
            timeout=60,
        )
        resp.raise_for_status()
        container = resp.json()["MediaContainer"]
        items = container.get("Metadata", [])
        if not items:
            break
        for item in items:
            viewed_at = item.get("lastViewedAt")
            view_count = item.get("viewCount", 0)
            if viewed_at and viewed_at < CUTOFF and view_count > 0:
                yield item
        start += size
        if start >= container.get("totalSize", 0):
            break


def format_title(item, library_type):
    """Format a human-readable title based on library type."""
    if library_type == "show":
        s = item.get('parentIndex', '?')
        e = item.get('index', '?')
        s_str = f"{s:02d}" if isinstance(s, int) else str(s)
        e_str = f"{e:02d}" if isinstance(e, int) else str(e)
        return f"{item.get('grandparentTitle', '?')} - S{s_str}E{e_str} - {item.get('title', '?')}"
    return f"{item.get('title', '?')} ({item.get('year', '?')})"


def delete_item(item, library_type):
    """Delete an item's files from disk and remove from Plex."""
    title = format_title(item, library_type)

    file_paths = []
    for media in item.get("Media", []):
        for part in media.get("Part", []):
            path = part.get("file")
            if path:
                file_paths.append(path)

    if DRY_RUN:
        print(f"[DRY RUN] Would delete: {title}")
        for fp in file_paths:
            print(f"           File: {fp}")
        return

    rating_key = item["ratingKey"]
    resp = requests.delete(f"{PLEX_URL}/library/metadata/{rating_key}", headers=HEADERS, timeout=30)
    if resp.status_code in (200, 204):
        print(f"[DELETED] {title}")
    else:
        print(f"[ERROR]   Plex API returned {resp.status_code} for {title}", file=sys.stderr)
        return

    for fp in file_paths:
        try:
            if os.path.exists(fp):
                os.remove(fp)
                print(f"           Removed file: {fp}")
            else:
                print(f"           File already gone: {fp}")
        except OSError as e:
            print(f"           Failed to remove {fp}: {e}", file=sys.stderr)


def main():
    mode = "DRY RUN" if DRY_RUN else "LIVE"
    print(f"=== Plex Watched Cleaner ({mode}) ===")
    print(f"    Library path: {LIBRARY_PATH}")
    print(f"    Cutoff: watched > {WATCHED_AGE_HOURS}h ago")
    print(f"    Protected collections: {', '.join(SKIP_COLLECTIONS) if SKIP_COLLECTIONS else '(none)'}")
    print()

    section_id, library_type = get_section()
    if not section_id:
        print(f"ERROR: No supported library found with path: {LIBRARY_PATH}", file=sys.stderr)
        sys.exit(1)

    type_info = LIBRARY_TYPE_MAP[library_type]
    print(f"    Library type: {library_type} (cleaning {type_info['label']})")

    skip_keys = get_skip_ratingkeys(section_id)
    if skip_keys:
        print(f"    Skipping {len(skip_keys)} items protected by collection(s)")
    print()

    count = 0
    skipped = 0
    for item in get_watched_items(section_id, type_info["item_type"]):
        if should_skip(item, library_type, skip_keys):
            print(f"[SKIPPED] {format_title(item, library_type)} (protected collection)")
            skipped += 1
            continue
        delete_item(item, library_type)
        count += 1

    if skipped:
        print(f"\nSkipped (protected): {skipped}")
    print(f"Total {type_info['label']} {'that would be deleted' if DRY_RUN else 'deleted'}: {count}")


if __name__ == "__main__":
    main()
