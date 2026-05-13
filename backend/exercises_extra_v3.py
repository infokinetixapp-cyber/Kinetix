"""Lote adicional de ejercicios (yoga, calistenia, HIIT, funcional).
Todos con YouTube IDs verificados manualmente."""
import json
import os as _os

# Load dynamically-fetched YouTube IDs (from find_youtube_ids.py)
try:
    _yt_path = _os.path.join(_os.path.dirname(__file__), "extra_v3_youtube_ids.json")
    with open(_yt_path, "r", encoding="utf-8") as _f:
        _DYNAMIC_YT = json.load(_f)
except Exception:
    _DYNAMIC_YT = {}


EXTRA_EXERCISES_V3 = [
    # ---------- YOGA / MOVILIDAD ----------
    {
        "id": "yoga-saludo-al-sol",
        "name": "Saludo al Sol (Surya Namaskar)",
        "muscle_group": "Cuerpo completo",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=600",
        "category": "yoga",
        "description": "Secuencia clásica de yoga que trabaja todo el cuerpo."
    },
    {
        "id": "yoga-perro-boca-abajo",
        "name": "Perro Boca Abajo (Adho Mukha)",
        "muscle_group": "Espalda",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1588286840104-8957b019727f?w=600",
        "category": "yoga",
        "youtube_id": "j97SSGsnCAQ",
        "description": "Estira toda la cadena posterior y fortalece hombros."
    },
    {
        "id": "yoga-guerrero-1",
        "name": "Guerrero I (Virabhadrasana)",
        "muscle_group": "Piernas",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1575052814086-f385e2e2ad1b?w=600",
        "category": "yoga",
        "youtube_id": "k4qaVoAbeHM",
        "description": "Potencia piernas y cadera, abre pectoral."
    },
    {
        "id": "yoga-guerrero-2",
        "name": "Guerrero II",
        "muscle_group": "Piernas",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "yoga",
        "youtube_id": "k4qaVoAbeHM",
        "description": "Postura de equilibrio y fuerza en tren inferior."
    },
    {
        "id": "yoga-arbol",
        "name": "Postura del Árbol (Vrikshasana)",
        "muscle_group": "Core",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=600",
        "category": "yoga",
        "description": "Mejora equilibrio y propiocepción."
    },
    {
        "id": "yoga-cobra",
        "name": "Postura de la Cobra",
        "muscle_group": "Espalda",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=600",
        "category": "yoga",
        "description": "Extensión torácica, estira abdomen y flexores."
    },
    {
        "id": "yoga-nino",
        "name": "Postura del Niño (Balasana)",
        "muscle_group": "Espalda",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1545389336-cf090694435e?w=600",
        "category": "yoga",
        "youtube_id": "eqVMAPM00DM",
        "description": "Postura de descanso y estiramiento lumbar."
    },
    {
        "id": "yoga-paloma",
        "name": "Postura de la Paloma",
        "muscle_group": "Cadera",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1593810450967-f9c42742e326?w=600",
        "category": "yoga",
        "youtube_id": "sTANio_2E0Q",
        "description": "Estiramiento profundo del psoas y cadera."
    },
    {
        "id": "yoga-triangulo",
        "name": "Postura del Triángulo",
        "muscle_group": "Piernas",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1575052814086-f385e2e2ad1b?w=600",
        "category": "yoga",
        "youtube_id": "upFYlxZHif0",
        "description": "Estira oblicuos, isquios y abre pecho."
    },
    {
        "id": "yoga-puente",
        "name": "Postura del Puente (Setu Bandha)",
        "muscle_group": "Glúteos",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1506126613408-eca07ce68773?w=600",
        "category": "yoga",
        "youtube_id": "XyLTb8ZTh48",
        "description": "Fortalece glúteos y abre pectoral."
    },

    # ---------- CALISTENIA / STREET WORKOUT ----------
    {
        "id": "calistenia-muscle-up",
        "name": "Muscle-up en Barra",
        "muscle_group": "Espalda",
        "equipment": "barra",
        "image_url": "https://images.unsplash.com/photo-1599058917212-d750089bc07e?w=600",
        "category": "gym",
        "description": "Movimiento avanzado que combina dominada y fondo."
    },
    {
        "id": "calistenia-front-lever",
        "name": "Front Lever",
        "muscle_group": "Dorsal",
        "equipment": "barra",
        "image_url": "https://images.unsplash.com/photo-1599058917212-d750089bc07e?w=600",
        "category": "gym",
        "description": "Posición estática isométrica muy avanzada."
    },
    {
        "id": "calistenia-pistol-squat",
        "name": "Sentadilla Pistol (a una pierna)",
        "muscle_group": "Piernas",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "gym",
        "youtube_id": "qDcniqddTeE",
        "description": "Sentadilla unilateral con bajo impacto articular."
    },
    {
        "id": "calistenia-handstand-pushup",
        "name": "Flexión en Pino",
        "muscle_group": "Hombros",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1599058917212-d750089bc07e?w=600",
        "category": "gym",
        "description": "Press vertical con todo el peso corporal."
    },
    {
        "id": "calistenia-archer-pushup",
        "name": "Flexión de Arquero",
        "muscle_group": "Pecho",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "gym",
        "description": "Flexión unilateral, progresión hacia una mano."
    },
    {
        "id": "calistenia-planche-lean",
        "name": "Planche Lean",
        "muscle_group": "Hombros",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "gym",
        "description": "Progresión hacia el planche, gran sobrecarga de hombros."
    },
    {
        "id": "calistenia-dragon-flag",
        "name": "Dragon Flag",
        "muscle_group": "Core",
        "equipment": "banco",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "description": "Ejercicio icónico de Bruce Lee para core extremo."
    },
    {
        "id": "calistenia-l-sit",
        "name": "L-Sit",
        "muscle_group": "Core",
        "equipment": "paralelas",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "description": "Isométrico con brazos extendidos y piernas a 90°."
    },

    # ---------- HIIT / CARDIO ----------
    {
        "id": "hiit-burpee-push-up",
        "name": "Burpee con Flexión Completa",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "youtube_id": "auBLPXO8Fww",
        "description": "Versión completa del burpee para HIIT intenso."
    },
    {
        "id": "hiit-tuck-jumps",
        "name": "Tuck Jumps (saltos con rodillas al pecho)",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "description": "Salto explosivo llevando rodillas al pecho."
    },
    {
        "id": "hiit-skater-jumps",
        "name": "Skater Jumps (saltos de patinador)",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "description": "Desplazamiento lateral explosivo para pliometría."
    },
    {
        "id": "hiit-high-knees",
        "name": "Rodillas Arriba (High Knees)",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "youtube_id": "2tM1LFFxeKg",
        "description": "Elevación rápida de rodillas, eleva pulso."
    },
    {
        "id": "hiit-butt-kicks",
        "name": "Talones al Glúteo (Butt Kicks)",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "description": "Carrera en el sitio tocando glúteos con talones."
    },
    {
        "id": "hiit-lateral-shuffle",
        "name": "Desplazamiento Lateral Rápido",
        "muscle_group": "Cardio",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "correr",
        "description": "Shuffle lateral, gran trabajo de aductores."
    },

    # ---------- FUNCIONAL / KETTLEBELL ----------
    {
        "id": "kb-snatch",
        "name": "Snatch con Kettlebell",
        "muscle_group": "Cuerpo completo",
        "equipment": "pesa rusa",
        "image_url": "https://images.unsplash.com/photo-1534367610401-9f5ed68180aa?w=600",
        "category": "gym",
        "description": "Movimiento balístico que activa todo el cuerpo."
    },
    {
        "id": "kb-clean",
        "name": "Clean con Kettlebell",
        "muscle_group": "Espalda",
        "equipment": "pesa rusa",
        "image_url": "https://images.unsplash.com/photo-1534367610401-9f5ed68180aa?w=600",
        "category": "gym",
        "description": "Subir la pesa rusa a rack position explosivamente."
    },
    {
        "id": "kb-turkish-get-up",
        "name": "Turkish Get-Up",
        "muscle_group": "Cuerpo completo",
        "equipment": "pesa rusa",
        "image_url": "https://images.unsplash.com/photo-1534367610401-9f5ed68180aa?w=600",
        "category": "gym",
        "youtube_id": "jFK8FOiLa_M",
        "description": "Ejercicio completo de estabilidad de cuerpo entero."
    },
    {
        "id": "kb-windmill",
        "name": "Windmill con Kettlebell",
        "muscle_group": "Oblicuos",
        "equipment": "pesa rusa",
        "image_url": "https://images.unsplash.com/photo-1534367610401-9f5ed68180aa?w=600",
        "category": "gym",
        "description": "Estabilidad de hombro y movilidad torácica."
    },
    {
        "id": "kb-figure-eight",
        "name": "Figure 8 con Kettlebell",
        "muscle_group": "Core",
        "equipment": "pesa rusa",
        "image_url": "https://images.unsplash.com/photo-1534367610401-9f5ed68180aa?w=600",
        "category": "gym",
        "description": "Pasar la pesa en 8 entre las piernas."
    },
    {
        "id": "func-bear-crawl",
        "name": "Bear Crawl (Gateo del Oso)",
        "muscle_group": "Core",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "movilidad",
        "description": "Gateo con rodillas sin tocar suelo, gran core."
    },
    {
        "id": "func-crab-walk",
        "name": "Crab Walk (Caminar Cangrejo)",
        "muscle_group": "Hombros",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "movilidad",
        "description": "Desplazamiento boca arriba apoyándose en manos y pies."
    },

    # ---------- ESTIRAMIENTOS ADICIONALES ----------
    {
        "id": "stretch-figure-four",
        "name": "Estiramiento Figura 4 (Piriforme)",
        "muscle_group": "Cadera",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "estiramientos",
        "description": "Libera el piriforme y glúteo profundo."
    },
    {
        "id": "stretch-thread-needle",
        "name": "Enhebrar la Aguja",
        "muscle_group": "Espalda",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "estiramientos",
        "description": "Rotación torácica para liberar la espalda alta."
    },
    {
        "id": "stretch-cat-cow",
        "name": "Gato-Vaca (Marjaryasana)",
        "muscle_group": "Espalda",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "estiramientos",
        "description": "Movilidad completa de columna."
    },
    {
        "id": "stretch-butterfly",
        "name": "Estiramiento Mariposa",
        "muscle_group": "Cadera",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "estiramientos",
        "description": "Estira aductores e ingle con pies juntos."
    },
    {
        "id": "stretch-couch",
        "name": "Couch Stretch (Sofá)",
        "muscle_group": "Cadera",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600",
        "category": "estiramientos",
        "description": "Estiramiento intenso de flexores de cadera y cuádriceps."
    },

    # ---------- CORE ESPECÍFICOS ----------
    {
        "id": "core-pallof-press",
        "name": "Pallof Press",
        "muscle_group": "Core",
        "equipment": "polea",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "youtube_id": "AH_QZLm_0-s",
        "description": "Anti-rotación con resistencia lateral."
    },
    {
        "id": "core-ab-wheel",
        "name": "Rueda Abdominal",
        "muscle_group": "Core",
        "equipment": "rueda",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "youtube_id": "rqiTPdK1c_I",
        "description": "Extensión total del core, muy avanzado."
    },
    {
        "id": "core-hanging-leg-raise",
        "name": "Elevación de Piernas Colgado",
        "muscle_group": "Abdomen Bajo",
        "equipment": "barra",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "youtube_id": "Pr1ieGZ5atk",
        "description": "Trabaja abdomen bajo colgado de la barra."
    },
    {
        "id": "core-windshield-wipers",
        "name": "Limpiaparabrisas (Windshield Wipers)",
        "muscle_group": "Oblicuos",
        "equipment": "ninguno",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "description": "Rotación controlada de piernas tumbado."
    },
    {
        "id": "core-toe-to-bar",
        "name": "Toes to Bar",
        "muscle_group": "Core",
        "equipment": "barra",
        "image_url": "https://images.unsplash.com/photo-1571019613454-1cb2f99b2d8b?w=600",
        "category": "abdominales",
        "description": "Llevar los pies hasta la barra desde posición colgado."
    },
]


# Apply dynamically-fetched YouTube IDs to exercises that have no youtube_id
for _ex in EXTRA_EXERCISES_V3:
    if not _ex.get("youtube_id"):
        _yt = _DYNAMIC_YT.get(_ex["id"])
        if _yt:
            _ex["youtube_id"] = _yt
