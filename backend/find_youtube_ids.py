"""Busca YouTube IDs reales para los ejercicios sin video.
Scrape la primera página de búsqueda de YouTube y extrae el videoId."""
import re
import requests
import sys
import os
import json
sys.path.insert(0, os.path.dirname(__file__))

from exercises_extra_v3 import EXTRA_EXERCISES_V3

# Regex: match videoId":"XXXXXXXX" in YouTube search results HTML
VID_RE = re.compile(r'"videoId":"([a-zA-Z0-9_-]{11})"')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


def check_youtube(yt_id: str) -> bool:
    try:
        r = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_id}&format=json",
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def search_youtube(query: str) -> str | None:
    try:
        url = f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        # Find all videoIds, take first few
        candidates = VID_RE.findall(r.text)
        if not candidates:
            return None
        # Pick first unique
        seen = set()
        for c in candidates:
            if c not in seen:
                seen.add(c)
                if check_youtube(c):
                    return c
        return None
    except Exception as e:
        print(f"  search error for '{query}': {e}")
        return None


def main():
    updates = {}
    for e in EXTRA_EXERCISES_V3:
        if "youtube_id" in e and e.get("youtube_id"):
            continue
        name = e["name"]
        # Build English-friendly query
        q = f"{name} exercise tutorial technique form"
        print(f"\nSearching: {e['id']:35}  q='{name}'")
        vid = search_youtube(q)
        if vid:
            print(f"  -> FOUND: {vid}")
            updates[e["id"]] = vid
        else:
            print(f"  -> not found")

    print(f"\n=== RESULTS ===")
    print(f"Found {len(updates)} new YouTube IDs")

    # Save to overrides JSON (so we don't need to re-run on reload)
    out_path = os.path.join(os.path.dirname(__file__), "extra_v3_youtube_ids.json")
    with open(out_path, "w") as f:
        json.dump(updates, f, indent=2, ensure_ascii=False)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
