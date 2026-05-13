"""Refresca el cache de ExerciseDB y descarga GIFs de más ejercicios."""
import os
import sys
import json
import requests
import concurrent.futures as cf
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from exercises_data import EXERCISES

API_KEY = os.environ["RAPIDAPI_KEY"]
HOST = os.environ["EXERCISEDB_HOST"]
GIF_DIR = os.path.join(os.path.dirname(__file__), "static", "gifs")
GIF_MAP_PATH = os.path.join(os.path.dirname(__file__), "exercise_gif_map.json")
EXDB_CACHE = "/tmp/exdb_all.json"


def fetch_all_exdb():
    """Download the full ExerciseDB catalog."""
    if os.path.exists(EXDB_CACHE):
        with open(EXDB_CACHE) as f:
            return json.load(f)

    print("Fetching all exercises from ExerciseDB...")
    url = f"https://{HOST}/exercises?limit=2000"
    r = requests.get(
        url,
        headers={"X-RapidAPI-Key": API_KEY, "X-RapidAPI-Host": HOST},
        timeout=30,
    )
    data = r.json()
    if not isinstance(data, list):
        print("Unexpected response:", data)
        return []
    with open(EXDB_CACHE, "w") as f:
        json.dump(data, f)
    print(f"Cached {len(data)} exercises")
    return data


def download_gif(exdb_id: str) -> bool:
    path = os.path.join(GIF_DIR, f"{exdb_id}.gif")
    if os.path.exists(path):
        return True
    url = f"https://{HOST}/image?exerciseId={exdb_id}&resolution=360&rapidapi-key={API_KEY}"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and r.content[:3] == b"GIF":
            with open(path, "wb") as f:
                f.write(r.content)
            return True
    except Exception:
        pass
    return False


def main():
    # Fetch full catalog
    exdb = fetch_all_exdb()
    if not exdb:
        print("No ExerciseDB data available, aborting.")
        return

    # Re-run matching
    print("Running fresh matching...")
    from exercise_gif_matcher import best_match, apply_overrides

    exdb_by_bp = {}
    for e in exdb:
        exdb_by_bp.setdefault(e.get("bodyPart", ""), []).append(e)

    matched = {}
    for ex in EXERCISES:
        best_ex, score = best_match(ex, exdb, exdb_by_bp)
        if best_ex and score >= 0.30:  # Lowered threshold
            matched[ex["id"]] = {
                "exdb_id": best_ex["id"],
                "exdb_name": best_ex["name"],
                "score": round(score, 2),
            }

    apply_overrides(matched, exdb)
    print(f"New matching: {len(matched)}/{len(EXERCISES)}")

    # Save map
    with open(GIF_MAP_PATH, "w") as f:
        json.dump(matched, f, ensure_ascii=False, indent=2)

    # Download missing gifs
    os.makedirs(GIF_DIR, exist_ok=True)
    to_download = []
    for eid, info in matched.items():
        exdb_id = info["exdb_id"]
        path = os.path.join(GIF_DIR, f"{exdb_id}.gif")
        if not os.path.exists(path):
            to_download.append(exdb_id)

    # Dedup
    to_download = list(set(to_download))
    print(f"To download: {len(to_download)} new gifs")

    success = 0
    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(download_gif, x): x for x in to_download}
        for i, fut in enumerate(cf.as_completed(futures)):
            try:
                if fut.result():
                    success += 1
            except Exception:
                pass
            if (i + 1) % 50 == 0:
                print(f"  progress: {i + 1}/{len(to_download)} | success: {success}")

    print(f"\n=== DONE ===")
    print(f"Downloaded successfully: {success}")
    # Final state
    total = sum(1 for eid, info in matched.items()
                if os.path.exists(os.path.join(GIF_DIR, f'{info["exdb_id"]}.gif')))
    print(f"Exercises with cached GIF: {total}/{len(EXERCISES)} ({total * 100 // len(EXERCISES)}%)")


if __name__ == "__main__":
    main()
