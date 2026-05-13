"""
Script para traducir masivamente todos los nombres de ejercicios
de español a: inglés, chino, francés, italiano.

Usa Gemini via emergentintegrations en lotes.
Resultado: /app/backend/exercise_names_i18n.json

Estructura:
{
  "Press Banca": {"en":"Bench Press","zh":"卧推","fr":"Développé couché","it":"Panca piana"},
  ...
}
"""
import os
import asyncio
import json
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from exercises_data import EXERCISES
from emergentintegrations.llm.chat import LlmChat, UserMessage

OUT = os.path.join(os.path.dirname(__file__), "exercise_names_i18n.json")
BATCH_SIZE = 40


async def translate_batch(names: list[str]) -> dict:
    api_key = os.environ["EMERGENT_LLM_KEY"]
    chat = (
        LlmChat(
            api_key=api_key,
            session_id="ex-translate",
            system_message=(
                "You are a professional fitness translator. "
                "Given a JSON list of Spanish fitness exercise names, "
                "translate each to English, Simplified Chinese, French and Italian. "
                "Respond ONLY with a JSON object, no markdown, no explanations. "
                "Format: {\"Spanish name\":{\"en\":\"...\",\"zh\":\"...\",\"fr\":\"...\",\"it\":\"...\"}, ...}"
            ),
        )
        .with_model("gemini", "gemini-2.5-flash")
    )

    prompt = "Translate these Spanish fitness exercise names:\n" + json.dumps(names, ensure_ascii=False)
    try:
        resp = await chat.send_message(UserMessage(text=prompt))
        text = resp.strip()
        # Strip possible ```json fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
            if text.startswith("json"):
                text = text[4:].lstrip()
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"  ERROR batch: {e}")
    return {}


async def main():
    # Gather unique names
    names = sorted({e.get("name", "").strip() for e in EXERCISES if e.get("name")})
    print(f"Total unique exercise names: {len(names)}")

    # Load existing translations to skip
    existing: dict = {}
    if os.path.exists(OUT):
        try:
            with open(OUT) as f:
                existing = json.load(f)
            print(f"Loaded {len(existing)} existing translations")
        except Exception:
            pass

    pending = [n for n in names if n not in existing or not all(k in existing.get(n, {}) for k in ["en", "zh", "fr", "it"])]
    print(f"Pending to translate: {len(pending)}")

    total = len(pending)
    for i in range(0, total, BATCH_SIZE):
        batch = pending[i:i + BATCH_SIZE]
        print(f"\nBatch {i // BATCH_SIZE + 1}/{(total + BATCH_SIZE - 1) // BATCH_SIZE} ({len(batch)} names)")
        result = await translate_batch(batch)
        # Merge
        for k, v in result.items():
            existing[k] = v
        # Save after every batch (resume-safe)
        with open(OUT, "w") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f"  saved {len(result)} / total file: {len(existing)}")

    print(f"\n✅ DONE. Total translations: {len(existing)}")


if __name__ == "__main__":
    asyncio.run(main())
