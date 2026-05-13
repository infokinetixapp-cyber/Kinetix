"""Matches our Spanish exercises against ExerciseDB (English) to assign GIFs.
Uses keyword translation + muscle/equipment filters + fuzzy name matching.
Outputs a mapping: our_id -> exerciseDB_id (for GIF URL construction).
"""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

# ============================================================================
# Spanish → English translation dictionaries
# ============================================================================
# Order matters — longest patterns first to avoid partial replacements
ES_EN_TERMS = [
    # Movement verbs / names (longest first)
    (r"\bpress de banca\b", "bench press"),
    (r"\bpress banca\b", "bench press"),
    (r"\bpress militar\b", "military press"),
    (r"\bpress mancuernas?\b", "dumbbell press"),
    (r"\bpress franc[eé]s\b", "french press skull crusher"),
    (r"\bpress arnold\b", "arnold press"),
    (r"\bpush[- ]press\b", "push press"),
    (r"\bpress\b", "press"),
    (r"\bsentadilla[s]? b[uú]lgara[s]?\b", "bulgarian split squat"),
    (r"\bsentadilla[s]? goblet\b", "goblet squat"),
    (r"\bsentadilla[s]? frontal\b", "front squat"),
    (r"\bsentadilla[s]?\b", "squat"),
    (r"\bpeso muerto rumano\b", "romanian deadlift"),
    (r"\bpeso muerto\b", "deadlift"),
    (r"\bdominada[s]? supina[s]?\b", "chin-up"),
    (r"\bdominada[s]?\b", "pull-up"),
    (r"\bremo con barra\b", "barbell row"),
    (r"\bremo mancuerna\b", "dumbbell row"),
    (r"\bremo polea\b", "cable row"),
    (r"\bremo\b", "row"),
    (r"\bcurl martillo\b", "hammer curl"),
    (r"\bcurl predicador\b", "preacher curl"),
    (r"\bcurl con[ ]*barra\b", "barbell curl"),
    (r"\bcurl mancuerna[s]?\b", "dumbbell curl"),
    (r"\bcurl concentrado\b", "concentration curl"),
    (r"\bcurl[ ]*(de)?[ ]*b[ií]ceps\b", "biceps curl"),
    (r"\bcurl\b", "curl"),
    (r"\bpatada[s]? de tr[ií]ceps\b", "triceps kickback"),
    (r"\bpatada[s]?\b", "kickback"),
    (r"\bextensi[oó]n de tr[ií]ceps\b", "triceps extension"),
    (r"\bextensi[oó]n de cu[aá]dric[eé]ps\b", "leg extension"),
    (r"\bextensi[oó]n en polea\b", "cable pushdown"),
    (r"\bextensi[oó]n\b", "extension"),
    (r"\bjal[oó]n al pecho\b", "lat pulldown"),
    (r"\bjal[oó]n\b", "pulldown"),
    (r"\belevaci[oó]n[ ]*(de)?[ ]*piernas?\b", "leg raise"),
    (r"\belevaci[oó]n[ ]*lateral\b", "lateral raise"),
    (r"\belevaci[oó]n[ ]*frontal\b", "front raise"),
    (r"\belevaci[oó]n[ ]*(de)?[ ]*gemelos?\b", "calf raise"),
    (r"\belevaci[oó]n[ ]*(de)?[ ]*talones?\b", "calf raise"),
    (r"\belevaci[oó]n\b", "raise"),
    (r"\bfondos? en paralelas?\b", "parallel bars dip"),
    (r"\bfondos? en banco\b", "bench dip"),
    (r"\bfondos?\b", "dip"),
    (r"\bflexiones? diamante\b", "diamond push-up"),
    (r"\bflexiones? declinadas?\b", "decline push-up"),
    (r"\bflexi[oó]n[ ]*(es)?\b", "push-up"),
    (r"\baperturas?\b", "fly"),
    (r"\bcruce[s]? de poleas?\b", "cable crossover"),
    (r"\bcruce[s]?\b", "crossover"),
    (r"\bencogimiento[s]?\b", "shrug"),
    (r"\bzancada[s]?\b", "lunge"),
    (r"\bpuente[s]?\b", "bridge"),
    (r"\bplancha lateral\b", "side plank"),
    (r"\bplancha\b", "plank"),
    (r"\bcrunch[es]?\b", "crunch"),
    (r"\brueda abdominal\b", "ab wheel rollout"),
    (r"\bgemelos?\b", "calf"),
    (r"\babductor\b", "hip abduction"),
    (r"\baductor\b", "hip adduction"),
    (r"\bhip[ -]thrust\b", "hip thrust"),
    (r"\bbird dog\b", "bird dog"),
    (r"\bdead bug\b", "dead bug"),
    (r"\bdeadbug\b", "dead bug"),
    (r"\bmountain climber[s]?\b", "mountain climber"),
    (r"\bburpee[s]?\b", "burpee"),
    (r"\brussian twist\b", "russian twist"),
    (r"\bpullover\b", "pullover"),
    (r"\bsalto[s]? (a la )?caja\b", "box jump"),
    (r"\bsalto[s]?\b", "jump"),
    (r"\bsuperman\b", "superman"),
    (r"\bbattle rope[s]?\b", "battle ropes"),
    (r"\bkettlebell swing\b", "kettlebell swing"),
    (r"\brope[ ]*skipping\b", "rope skipping"),
    (r"\bskipping\b", "high knees"),
    (r"\bpredicador\b", "preacher"),
    (r"\bspider\b", "spider"),
    (r"\bpatada de burro\b", "donkey kick"),
    (r"\bpatada\b", "kick"),
    (r"\bwall slide[s]?\b", "wall slide"),
    (r"\bface pull\b", "face pull"),
    (r"\bpa[jg][aá]ros\b", "reverse fly"),
    (r"\breverse fly\b", "reverse fly"),
    (r"\brompecr[aá]neos\b", "skull crusher"),
    (r"\bzottman\b", "zottman"),
    # Equipment
    (r"\bbarra z\b", "ez bar"),
    (r"\bbarra\b", "barbell"),
    (r"\bmancuerna[s]?\b", "dumbbell"),
    (r"\bkettlebell\b", "kettlebell"),
    (r"\bpesa rusa\b", "kettlebell"),
    (r"\bpolea[s]?\b", "cable"),
    (r"\bcable\b", "cable"),
    (r"\bm[aá]quina\b", "machine"),
    (r"\bbanda\b", "band"),
    (r"\bel[aá]stica\b", ""),
    (r"\bbanco\b", "bench"),
    (r"\bpelota suiza\b", "stability ball"),
    (r"\bpelota medicinal\b", "medicine ball"),
    (r"\bdisco[s]?\b", "weighted"),
    (r"\bpeso corporal\b", "body weight"),
    # Muscle groups
    (r"\bpecho superior\b", "upper chest"),
    (r"\bpecho inferior\b", "lower chest"),
    (r"\bpecho\b", "chest"),
    (r"\bespalda\b", "back"),
    (r"\bdorsal(es)?\b", "lat"),
    (r"\bhombro[s]?\b", "shoulder"),
    (r"\bb[ií]ceps\b", "biceps"),
    (r"\btr[ií]ceps\b", "triceps"),
    (r"\bpierna[s]?\b", "legs"),
    (r"\bcu[aá]dric[eé]ps\b", "quads"),
    (r"\bfemoral(es)?\b", "hamstring"),
    (r"\bgl[uú]teo[s]?\b", "glute"),
    (r"\babdomen\b", "abs"),
    (r"\boblicuo[s]?\b", "obliques"),
    (r"\bantebrazo[s]?\b", "forearm"),
    (r"\btrapecio[s]?\b", "trap"),
    # Directions
    (r"\bde pie\b", "standing"),
    (r"\bsentado\b", "seated"),
    (r"\btumbado\b", "lying"),
    (r"\binclinado\b", "incline"),
    (r"\bdeclinado\b", "decline"),
    (r"\bagarre cerrado\b", "close grip"),
    (r"\bagarre ancho\b", "wide grip"),
    (r"\bagarre neutro\b", "neutral grip"),
    (r"\bagarre supino\b", "supine"),
    (r"\bunilateral\b", "single arm"),
    (r"\b1 mano\b", "single arm"),
    (r"\b1 brazo\b", "single arm"),
    (r"\b1 pierna\b", "single leg"),
    (r"\bcon rotaci[oó]n\b", "with rotation"),
    (r"\bcon banda\b", "with band"),
    (r"\bmartillo\b", "hammer"),
    # Clean-up
    (r"\bcon\b", ""),
    (r"\bal\b", ""),
    (r"\ben\b", ""),
    (r"\bde la\b", ""),
    (r"\bde los\b", ""),
    (r"\bdel\b", ""),
    (r"\ben el\b", ""),
    (r"\bpara\b", ""),
    (r"\bla\b", ""),
    (r"\blos\b", ""),
    (r"\bel\b", ""),
    (r"\bun\b", ""),
    (r"\buna\b", ""),
    (r"\(tocar[^)]*\)", ""),
    (r"\([^)]*\)", ""),
    (r"  +", " "),
]


def translate(es_text: str) -> str:
    """Translate Spanish exercise description to English keyword bag."""
    t = es_text.lower()
    t = t.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    t = t.replace("ñ", "n")
    for pat, repl in ES_EN_TERMS:
        t = re.sub(pat, repl, t)
    t = re.sub(r"[^a-z0-9 -]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Body part mapping (our muscle_group → exerciseDB bodyPart)
BODYPART_MAP = {
    "pecho": "chest", "pecho superior": "chest", "pecho inferior": "chest",
    "pecho interno": "chest", "pecho/dorsal": "chest", "tríceps/pecho": "chest",
    "pecho/estabilidad": "chest", "pecho/hombro": "chest", "pecho/core": "chest",
    "espalda": "back", "dorsal": "back", "dorsal/bíceps": "back",
    "dorsal/braquial": "back", "dorsal bajo": "back", "dorsal/core": "back",
    "espalda alta/trapecio": "back", "espalda baja/glúteo": "back",
    "espalda/pierna": "back", "espalda/pierna/glúteo": "back",
    "espalda baja/femoral": "back", "trapecio": "back", "trapecio inferior": "back",
    "lumbar": "back",
    "hombro": "shoulders", "hombros": "shoulders",
    "hombro medio": "shoulders", "hombro frontal": "shoulders",
    "hombro posterior": "shoulders", "hombro posterior/trapecio": "shoulders",
    "hombro/trapecio": "shoulders", "hombro/core": "shoulders",
    "manguito rotador/hombro": "shoulders",
    "bíceps": "upper arms", "tríceps": "upper arms",
    "braquial/bíceps": "upper arms", "bíceps/antebrazo": "upper arms",
    "bíceps largo": "upper arms", "tríceps largo": "upper arms",
    "tríceps medial": "upper arms", "bíceps/dorsal": "upper arms",
    "antebrazo/bíceps": "upper arms", "antebrazo/braquial": "lower arms",
    "antebrazo": "lower arms", "antebrazo flexor": "lower arms",
    "antebrazo extensor": "lower arms", "antebrazo/agarre": "lower arms",
    "antebrazo/hombro": "lower arms", "antebrazo/espalda": "lower arms",
    "antebrazo/trapecio/core": "lower arms",
    "piernas": "upper legs", "cuádriceps": "upper legs", "femoral": "upper legs",
    "femoral/espalda baja": "upper legs", "femoral/lumbar": "upper legs",
    "cuádriceps/glúteo": "upper legs", "cuádriceps/core": "upper legs",
    "cuádriceps/core/hombro": "upper legs", "cuádriceps/glúteo/equilibrio": "upper legs",
    "cuádriceps externo": "upper legs", "pierna/glúteo": "upper legs",
    "pierna explosiva": "upper legs", "aductores/cuádriceps": "upper legs",
    "aductores/glúteo": "upper legs", "aductores": "upper legs",
    "glúteo": "upper legs", "glúteos": "upper legs", "glúteo medio": "upper legs",
    "glúteo/femoral": "upper legs", "glúteo/aductor": "upper legs",
    "glúteo/cuádriceps": "upper legs", "glúteo/femoral/equilibrio": "upper legs",
    "gemelos": "lower legs", "gemelos (sóleo)": "lower legs",
    "abdomen": "waist", "abdomen bajo": "waist", "core": "waist",
    "oblicuos": "waist", "core anti-rotación": "waist", "core/cardio": "waist",
    "pierna/cardio": "cardio", "cardio": "cardio",
}


# Equipment mapping
EQ_MAP = {
    "barra": ["barbell", "ez barbell", "olympic barbell"],
    "barra z": ["ez barbell"],
    "mancuerna": ["dumbbell"], "mancuernas": ["dumbbell"],
    "kettlebell": ["kettlebell"], "pesa rusa": ["kettlebell"],
    "polea": ["cable"], "poleas": ["cable"], "cable": ["cable"],
    "cables": ["cable"],
    "máquina": ["leverage machine", "smith machine", "sled machine"],
    "banda": ["band", "resistance band"],
    "banda elástica": ["band", "resistance band"],
    "banco": ["body weight", "bench"],
    "paralelas": ["body weight"], "paralela": ["body weight"],
    "anillas": ["body weight"], "peso corporal": ["body weight"],
    "pared": ["body weight"], "esterilla": ["body weight"],
    "pelota suiza": ["stability ball", "exercise ball"],
    "pelota medicinal": ["medicine ball"],
    "disco": ["weighted"], "discos": ["weighted"],
    "rueda": ["wheel roller"],
    "t-bar": ["barbell"], "trap bar": ["trap bar"],
    "landmine": ["barbell"], "barra landmine": ["barbell"],
    "cajón pliométrico": ["body weight"], "caja": ["body weight"],
    "banco ghd": ["body weight"], "banco scott": ["barbell"],
    "banco declinado": ["body weight"], "banco inclinado": ["body weight"],
    "banda/toalla": ["band"], "barra baja": ["body weight"],
    "barra/toalla": ["body weight"], "hand gripper": ["body weight"],
    "foam roller": ["roller"], "silla": ["body weight"],
    "cojín": ["body weight"], "piscina": ["body weight"],
    "cinturón con peso": ["weighted"], "máquina/cinturón": ["weighted"],
    "empeines": ["body weight"], "comba": ["body weight"],
    "cuesta": ["body weight"], "cuerda": ["rope"], "cuerdas": ["rope"],
    "foam": ["roller"], "compañero": ["body weight"],
    "cajón alto": ["body weight"], "mancuernas/discos": ["weighted"],
    "mancuernas/pesa": ["dumbbell"], "mancuernas/barra": ["dumbbell"],
    "banco/mancuernas": ["dumbbell"], "mancuerna/pesa rusa": ["dumbbell"],
    "barra/mancuerna": ["barbell"], "barra/z": ["ez barbell"],
    "banco/peso": ["weighted"], "poleas bajas": ["cable"],
    "barra paralela": ["body weight"], "barra fija": ["body weight"],
}


def best_match(es_ex: dict, exdb: list, exdb_by_bp: dict):
    """Find best matching ExerciseDB exercise for a given Spanish exercise."""
    target_bp = BODYPART_MAP.get(es_ex.get("muscle_group", "").lower(), None)
    pool = exdb_by_bp.get(target_bp, exdb) if target_bp else exdb

    en_name = translate(es_ex["name"])
    en_eq = es_ex.get("equipment", "").lower()

    # Equipment filter
    eq_keys = EQ_MAP.get(en_eq, [])
    eq_pool = [e for e in pool if any(k in e.get("equipment", "") for k in eq_keys)] if eq_keys else pool
    if not eq_pool or len(eq_pool) < 3:
        eq_pool = pool

    en_words = set(w for w in en_name.split() if len(w) > 2)

    best_score = 0.0
    best_ex = None
    for cand in eq_pool:
        cand_name = cand["name"].lower()
        cand_words = set(w for w in cand_name.split() if len(w) > 2)
        # Jaccard similarity
        if en_words and cand_words:
            jacc = len(en_words & cand_words) / len(en_words | cand_words)
        else:
            jacc = 0
        # Sequence similarity
        seq = SequenceMatcher(None, en_name, cand_name).ratio()
        # Weighted combination favoring keyword overlap
        score = 0.7 * jacc + 0.3 * seq
        # Small bonus for equipment exact match
        if eq_keys and any(k == cand.get("equipment", "") for k in eq_keys):
            score += 0.05
        if score > best_score:
            best_score = score
            best_ex = cand
    return best_ex, best_score


# Manual overrides for exercises that fuzzy matching struggles with.
# Our ID -> ExerciseDB ID (from /tmp/exdb_all.json)
MANUAL_OVERRIDES = {
    # Pecho
    "press-banca": "0025",            # barbell bench press
    "press-banca-inclinado": "0047",  # barbell incline bench press
    "press-banca-declinado": "0033",  # barbell decline bench press
    "press-mancuernas": "0289",       # dumbbell bench press
    "aperturas-mancuernas": "0308",   # dumbbell fly
    "fondos-paralelas": "0464",       # parallel bar dip (closest)
    "flexiones": "3284",              # push-up
    "press-suelo": "0306",
    "press-cerrado": "0028",          # barbell close-grip bench press
    # Espalda
    "dominadas": "0652",              # pull-up
    "dominadas-supinas": "0651",      # pull up (neutral grip) closest
    "remo-barra": "0027",             # barbell bent over row
    "remo-mancuerna": "0293",         # dumbbell bent over row
    "remo-polea": "0164",             # cable seated row (approx)
    "jalon-pecho": "0150",            # cable bar lateral pulldown
    "peso-muerto": "0032",            # barbell deadlift
    "encogimientos": "0329",          # dumbbell incline shrug
    "pullover": "0413",
    "hiperextensiones": "0467",       # hyperextension
    "face-pull": "0187",              # cable face pull (approx)
    # Hombros
    "press-militar": "0086",          # barbell seated behind head military press
    "press-arnold": "2137",           # dumbbell arnold press
    "elevaciones-laterales": "0334",  # dumbbell lateral raise (approx)
    "elevaciones-frontales": "0312",  # dumbbell front raise
    "pajaros": "0376",                # dumbbell rear raise (approx)
    "remo-menton": "0120",            # barbell upright row
    "push-press": "0182",
    "press-mancuernas-hombro": "0426",  # dumbbell standing overhead press
    # Bíceps
    "curl-barra": "0031",             # barbell curl
    "curl-mancuernas": "0290",        # dumbbell alternate biceps curl
    "curl-martillo": "0312",          # dumbbell hammer curl v. 2
    "curl-predicador": "0070",        # barbell preacher curl
    "curl-concentrado": "0297",       # dumbbell concentration curl
    "curl-21": "0031",
    # Tríceps
    "press-frances": "0060",          # barbell lying triceps extension skull crusher
    "extension-polea": "0201",        # cable pushdown
    "fondos-banco": "0129",           # bench dip
    "patada-triceps": "0333",         # dumbbell kickback
    "rompecraneos": "0060",
    # Piernas
    "sentadilla": "0043",             # barbell full squat (approx)
    "sentadilla-frontal": "0029",     # barbell clean-grip front squat
    "prensa": "0739",                 # sled 45° leg press
    "zancadas": "0054",               # barbell lunge
    "extensiones-cuadriceps": "0585", # lever leg extension
    "curl-femoral": "0586",           # lever lying leg curl
    "peso-muerto-rumano": "0085",     # barbell romanian deadlift
    "buenos-dias": "0046",            # barbell good morning (approx)
    "sentadilla-bulgara": "0358",     # dumbbell bulgarian split squat (approx)
    "hip-thrust": "3236",             # resistance band hip thrusts (best available)
    "gemelos-de-pie": "0108",         # barbell standing calf raise
    "gemelos-sentado": "0088",        # barbell seated calf raise
    "step-up": "0114",                # barbell step-up
    "sentadilla-goblet": "1760",      # dumbbell goblet squat
    # Core
    "plancha": "2135",                # weighted front plank → use body weight variant
    "plancha-lateral": "0664",
    "dead-bug": "0276",               # dead bug
    "bird-dog": "2432",               # bird dog (approx)
    "hollow-hold": "0471",            # hollow hold
    "crunch-abdominal": "0212",       # cable seated crunch (approx)
    "elevaciones-piernas": "0472",    # hanging leg raise
    "rueda-abdominal": "3220",
    "russian-twist": "0687",          # russian twist
    "pallof-press": "0979",           # band horizontal pallof press
    # Cardio
    "saltos-caja": "0498",            # box jump
    "burpees": "0501",                # jack burpee
    "mountain-climbers": "2466",
    "kettlebell-swing": "0549",       # kettlebell swing
    # Nuevos gym (de exercises_gym_full)
    "ch-press-banca-cerrado": "0028",
    "ch-press-mancuerna-inclinado": "0314",  # dumbbell incline bench press
    "ch-press-mancuerna-declinado": "0301",  # dumbbell decline bench press
    "ch-flexion-clasica": "3284",
    "ch-flexion-diamante": "3203",           # diamond push-up
    "ch-flexion-declinada": "0279",          # decline push-up
    "bk-dominada-prona": "0652",
    "bk-dominada-supina": "0651",
    "bk-remo-mancuerna": "0293",
    "bk-deadlift-convencional": "0032",
    "bk-deadlift-rumano": "0085",
    "bk-shrug-mancuerna": "0329",
    "sh-press-militar": "0086",
    "sh-arnold-press": "2137",
    "sh-elevacion-lateral-mancuerna": "0334",
    "sh-elevacion-frontal-mancuerna": "0312",
    "sh-push-press": "0182",
    "bi-curl-barra-recta": "0031",
    "bi-curl-mancuerna-alterno": "0290",
    "bi-curl-martillo-pie": "0312",
    "bi-curl-predicador": "0070",
    "bi-curl-concentrado": "0297",
    "tr-press-frances": "0060",
    "tr-pushdown-cuerda": "0200",
    "tr-pushdown-barra": "0201",
    "tr-kickback": "0333",
    "tr-fondos-banco-tri": "0129",
    "le-sentadilla-libre": "0043",
    "le-sentadilla-frontal": "0029",
    "le-sentadilla-bulgara-mancuerna": "0358",
    "le-prensa": "0739",
    "le-zancada-andando": "0054",
    "le-leg-extension": "0585",
    "le-leg-curl-tumbado": "0586",
    "le-stiff-leg-deadlift": "1010",
    "le-sentadilla-goblet": "1760",
    "le-elevacion-talones-pie": "0108",
    "le-elevacion-talones-sentado": "0088",
    "gl-hip-thrust-barra": "0087",
    "gl-hip-thrust-mancuerna": "3236",
    "gl-puente-gluteo": "0281",
    "gl-rdl-mancuerna": "1459",
    "co-cable-crunch": "0175",
    "co-russian-twist-medicina": "0014",
    "co-hanging-knee-raise": "0472",
    "co-ab-wheel": "3220",
    "co-bicycle-crunch": "0003",               # air bike (bicycle crunch)
    "co-mountain-climber": "2466",
    "co-flutter-kicks": "0476",
}


def apply_overrides(matched: dict, exdb: list):
    """Apply manual overrides to the matched dict."""
    exdb_ids = {e["id"] for e in exdb}
    exdb_by_id = {e["id"]: e for e in exdb}
    applied = 0
    for our_id, exdb_id in MANUAL_OVERRIDES.items():
        if exdb_id in exdb_ids:
            matched[our_id] = {
                "exdb_id": exdb_id,
                "exdb_name": exdb_by_id[exdb_id]["name"],
                "score": 1.0,
                "override": True,
            }
            applied += 1
    return applied


def main():
    import sys
    sys.path.insert(0, "/app/backend")
    from exercises_data import EXERCISES

    # Load ExerciseDB cache
    exdb = json.load(open("/tmp/exdb_all.json"))
    exdb_by_bp = {}
    for e in exdb:
        exdb_by_bp.setdefault(e.get("bodyPart", ""), []).append(e)

    matched = {}
    unmatched = []
    for ex in EXERCISES:
        best_ex, score = best_match(ex, exdb, exdb_by_bp)
        if best_ex and score >= 0.35:
            matched[ex["id"]] = {
                "exdb_id": best_ex["id"],
                "exdb_name": best_ex["name"],
                "score": round(score, 2),
            }
        else:
            unmatched.append((ex["id"], ex["name"], score, best_ex["name"] if best_ex else None))

    # Apply manual overrides (highest priority)
    overrides_applied = apply_overrides(matched, exdb)
    print(f"Manual overrides applied: {overrides_applied}")

    # Save the mapping
    json.dump(matched, open("/app/backend/exercise_gif_map.json", "w"), ensure_ascii=False, indent=2)
    print(f"Matched: {len(matched)}/{len(EXERCISES)} ({len(matched)*100//len(EXERCISES)}%)")
    print(f"Unmatched: {len(EXERCISES) - len(matched)}")
    # Sample final mapping quality
    print("\nSample matches (first 20):")
    for our_id in list(matched.keys())[:20]:
        m = matched[our_id]
        print(f"  {our_id:40} → {m['exdb_name'][:45]:45} (score={m['score']})")


if __name__ == "__main__":
    main()
