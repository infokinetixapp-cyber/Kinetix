"""
Audita todos los ejercicios comprobando:
1. Si tienen gif disponible (existe en exercise_gif_map.json y archivo local)
2. Si sus YouTube IDs funcionan (HEAD request a oembed)
3. Genera un reporte con ejercicios sin GIF o con video roto

Output: /tmp/exercises_audit.json
"""
import os
import sys
import json
import requests
import concurrent.futures as cf
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from exercises_data import EXERCISES

GIF_DIR = os.path.join(os.path.dirname(__file__), "static", "gifs")
GIF_MAP_PATH = os.path.join(os.path.dirname(__file__), "exercise_gif_map.json")
YT_OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "youtube_overrides.json")

with open(GIF_MAP_PATH) as f:
    GIF_MAP = json.load(f)

with open(YT_OVERRIDES_PATH) as f:
    YT_OVERRIDES = json.load(f)


def check_youtube(yt_id: str) -> bool:
    """Returns True if the YouTube video exists (using oembed)."""
    if not yt_id:
        return False
    try:
        r = requests.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_id}&format=json",
            timeout=6,
        )
        return r.status_code == 200
    except Exception:
        return False


def main():
    missing_gif = []
    broken_yt = []
    ok_count = 0

    # Check GIFs
    for e in EXERCISES:
        eid = e.get("id")
        name = e.get("name")
        if eid in GIF_MAP:
            exdb_id = GIF_MAP[eid].get("exdb_id")
            path = os.path.join(GIF_DIR, f"{exdb_id}.gif")
            has_gif = os.path.exists(path)
            if not has_gif:
                missing_gif.append({"id": eid, "name": name, "exdb_id": exdb_id, "reason": "file_not_cached"})
        else:
            missing_gif.append({"id": eid, "name": name, "exdb_id": None, "reason": "no_mapping"})

    print(f"Exercises without local GIF: {len(missing_gif)}/{len(EXERCISES)}")

    # Check YouTube IDs in parallel
    print("Checking YouTube IDs (this may take ~1 min)...")
    to_check = []
    for e in EXERCISES:
        eid = e.get("id")
        yt_id = YT_OVERRIDES.get(eid, {}).get("youtube_id") or e.get("youtube_id")
        if yt_id:
            to_check.append((eid, e.get("name"), yt_id))

    print(f"  Total videos to check: {len(to_check)}")

    with cf.ThreadPoolExecutor(max_workers=15) as pool:
        futures = {pool.submit(check_youtube, yt): (eid, name, yt) for eid, name, yt in to_check}
        for i, fut in enumerate(cf.as_completed(futures)):
            eid, name, yt = futures[fut]
            try:
                ok = fut.result()
            except Exception:
                ok = False
            if ok:
                ok_count += 1
            else:
                broken_yt.append({"id": eid, "name": name, "youtube_id": yt})
            if (i + 1) % 100 == 0:
                print(f"  checked {i + 1}/{len(to_check)}")

    print(f"\n=== AUDIT RESULTS ===")
    print(f"Total exercises: {len(EXERCISES)}")
    print(f"Missing GIF: {len(missing_gif)}")
    print(f"YouTube OK: {ok_count}")
    print(f"YouTube BROKEN: {len(broken_yt)}")
    print(f"No YouTube ID: {len(EXERCISES) - len(to_check)}")

    out = {
        "total_exercises": len(EXERCISES),
        "missing_gif": missing_gif,
        "broken_youtube": broken_yt,
        "no_youtube_id": [
            {"id": e["id"], "name": e.get("name")}
            for e in EXERCISES
            if not (YT_OVERRIDES.get(e["id"], {}).get("youtube_id") or e.get("youtube_id"))
        ],
    }
    with open("/tmp/exercises_audit.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\nReport saved to /tmp/exercises_audit.json")


if __name__ == "__main__":
    main()
