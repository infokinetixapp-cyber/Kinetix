"""Crea 3 rutinas premium de ejemplo listas para vender a 3,99€."""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient


PREMIUM_ROUTINES = [
    {
        "name": "💪 Full Body Hipertrofia 3x/sem",
        "description": "Plan completo 3 días por semana para ganar músculo. Programación de expertos con ejercicios compuestos y aislados.",
        "price_eur": 3.99,
        "exercises": [
            {"id": str(uuid.uuid4()), "name": "Sentadilla con Barra", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Cuádriceps"},
            {"id": str(uuid.uuid4()), "name": "Press Banca", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Pecho"},
            {"id": str(uuid.uuid4()), "name": "Peso Muerto Rumano", "sets": 4, "reps": 10, "weight": 0, "rest_seconds": 120, "muscle_group": "Isquios"},
            {"id": str(uuid.uuid4()), "name": "Remo con Barra", "sets": 4, "reps": 10, "weight": 0, "rest_seconds": 90, "muscle_group": "Espalda"},
            {"id": str(uuid.uuid4()), "name": "Press Militar con Barra", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 90, "muscle_group": "Hombros"},
            {"id": str(uuid.uuid4()), "name": "Dominadas", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Dorsal"},
            {"id": str(uuid.uuid4()), "name": "Curl con Barra", "sets": 3, "reps": 12, "weight": 0, "rest_seconds": 60, "muscle_group": "Bíceps"},
            {"id": str(uuid.uuid4()), "name": "Tríceps en Polea", "sets": 3, "reps": 12, "weight": 0, "rest_seconds": 60, "muscle_group": "Tríceps"},
            {"id": str(uuid.uuid4()), "name": "Plancha", "sets": 3, "reps": 60, "weight": 0, "rest_seconds": 60, "muscle_group": "Core"},
        ],
    },
    {
        "name": "🔥 Quema Grasa HIIT 20min",
        "description": "Sesiones intensas de 20 minutos tipo HIIT. Quema calorías como una caldera y mejora tu capacidad cardiovascular.",
        "price_eur": 3.99,
        "exercises": [
            {"id": str(uuid.uuid4()), "name": "Burpee", "sets": 5, "reps": 12, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
            {"id": str(uuid.uuid4()), "name": "Mountain Climbers", "sets": 5, "reps": 30, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
            {"id": str(uuid.uuid4()), "name": "Jumping Jacks", "sets": 5, "reps": 40, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
            {"id": str(uuid.uuid4()), "name": "Rodillas Arriba (High Knees)", "sets": 5, "reps": 30, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
            {"id": str(uuid.uuid4()), "name": "Sentadilla con salto", "sets": 4, "reps": 15, "weight": 0, "rest_seconds": 45, "muscle_group": "Piernas"},
            {"id": str(uuid.uuid4()), "name": "Flexión de pecho", "sets": 4, "reps": 12, "weight": 0, "rest_seconds": 45, "muscle_group": "Pecho"},
            {"id": str(uuid.uuid4()), "name": "Plancha", "sets": 3, "reps": 45, "weight": 0, "rest_seconds": 30, "muscle_group": "Core"},
        ],
    },
    {
        "name": "🏋️ Fuerza Pura 5x5",
        "description": "Método 5x5 clásico para ganar fuerza bruta. Pecho, espalda, piernas en 3 días. Progresión lineal.",
        "price_eur": 3.99,
        "exercises": [
            {"id": str(uuid.uuid4()), "name": "Sentadilla con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Cuádriceps"},
            {"id": str(uuid.uuid4()), "name": "Press Banca", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Pecho"},
            {"id": str(uuid.uuid4()), "name": "Peso Muerto", "sets": 3, "reps": 5, "weight": 0, "rest_seconds": 240, "muscle_group": "Espalda"},
            {"id": str(uuid.uuid4()), "name": "Press Militar con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Hombros"},
            {"id": str(uuid.uuid4()), "name": "Remo con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 150, "muscle_group": "Espalda"},
        ],
    },
]


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]

    # Find admin
    admin = await db.users.find_one({"email": "admin@fitness.com"})
    if not admin:
        print("No admin user found. Seed admin first.")
        return

    created = 0
    for r in PREMIUM_ROUTINES:
        # Skip if already exists
        if await db.routines.find_one({"name": r["name"], "is_premium_routine": True}):
            print(f"SKIP (exists): {r['name']}")
            continue
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": admin["id"],
            "name": r["name"],
            "description": r["description"],
            "exercises": r["exercises"],
            "is_public": True,
            "is_premium_routine": True,
            "price_eur": r["price_eur"],
            "is_predefined": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.routines.insert_one(doc)
        print(f"CREATED: {r['name']} ({r['price_eur']}€)")
        created += 1

    print(f"\nDone. Created {created} premium routines.")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())
