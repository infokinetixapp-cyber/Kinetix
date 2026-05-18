from dotenv import load_dotenv
from pathlib import Path
import os as _os
ROOT_DIR = Path(__file__).parent
# In production (Kubernetes/Emergent Deploy) the orchestrator injects critical env vars
# (MONGO_URL, DB_NAME, secrets, etc.) that MUST NOT be overridden by the local .env.
# In development we still need override=True so that .env beats stale shell env vars
# (e.g. STRIPE_API_KEY=sk_test_emergent leaked from the dev sandbox).
_IS_PROD = ("KUBERNETES_SERVICE_HOST" in _os.environ) or (_os.environ.get("EMERGENT_ENV") == "production")
load_dotenv(ROOT_DIR / '.env', override=not _IS_PROD)

import os
import uuid
import logging
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Literal, Union

from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from emergent_compat import LlmChat, UserMessage
from emergent_compat import StripeCheckout, CheckoutSessionRequest
import json as json_lib
import re
import stripe as stripe_sdk
from exercises_data import EXERCISES as FULL_EXERCISES, list_exercises, get_exercise, muscle_groups, categories

# DB
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

JWT_SECRET = os.environ['JWT_SECRET']
JWT_ALG = "HS256"
EMERGENT_LLM_KEY = (
    os.environ.get("EMERGENT_LLM_KEY")
    or os.environ.get("EMERGENT_LLM_KEY1")
    or os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY")
    or ""
)
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
if STRIPE_API_KEY:
    stripe_sdk.api_key = STRIPE_API_KEY
FREE_ROUTINE_LIMIT = 4
FREE_AI_LIMIT = 2

# ---------------- XP & LEVELS SYSTEM ----------------
import math as _math
XP_REWARDS = {
    "workout": 50,
    "cardio": 30,
    "pr": 100,
    "achievement": 150,
    "follow": 5,
    "publish_routine": 20,
    "body_entry": 10,
    "streak_bonus_7": 50,
}

def xp_to_level(xp: int) -> int:
    """level = floor(sqrt(xp/100)). Lvl 1=100xp, 2=400, 3=900, 4=1600, 5=2500..."""
    return int(_math.sqrt(max(0, xp) / 100))

def xp_for_level(level: int) -> int:
    """Total XP required to REACH this level."""
    return int(level * level * 100)

async def add_xp(user_id: str, amount: int, reason: str) -> dict:
    """Add XP, persist, return {added, total_xp, level, leveled_up, new_level}."""
    if amount <= 0 or not user_id:
        return {"added": 0}
    u = await db.users.find_one({"id": user_id}, {"xp": 1, "level": 1})
    if not u:
        return {"added": 0}
    old_xp = int(u.get("xp", 0) or 0)
    old_level = xp_to_level(old_xp)
    new_xp = old_xp + amount
    new_level = xp_to_level(new_xp)
    leveled_up = new_level > old_level
    await db.users.update_one(
        {"id": user_id},
        {
            "$set": {"xp": new_xp, "level": new_level},
            "$push": {
                "xp_history": {
                    "$each": [{
                        "amount": amount,
                        "reason": reason,
                        "date": datetime.now(timezone.utc).isoformat(),
                        "level_after": new_level,
                    }],
                    "$slice": -200,
                }
            },
        }
    )
    return {
        "added": amount,
        "total_xp": new_xp,
        "level": new_level,
        "leveled_up": leveled_up,
        "new_level": new_level if leveled_up else None,
        "reason": reason,
    }

# ---------------- Exercise GIF mapping ----------------
GIF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "gifs")
try:
    _gif_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exercise_gif_map.json")
    with open(_gif_map_path, "r", encoding="utf-8") as f:
        import json as _json
        EXERCISE_GIF_MAP = _json.load(f)
except Exception:
    EXERCISE_GIF_MAP = {}

# ---------------- YouTube overrides (replace broken IDs) ----------------
try:
    _yt_map_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_overrides.json")
    with open(_yt_map_path, "r", encoding="utf-8") as f:
        import json as __json
        YT_OVERRIDES = __json.load(f)
except Exception:
    YT_OVERRIDES = {}

# ---------------- Exercise name i18n translations ----------------
try:
    _i18n_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exercise_names_i18n.json")
    with open(_i18n_path, "r", encoding="utf-8") as f:
        import json as ___json
        EXERCISE_NAMES_I18N = ___json.load(f)
except Exception:
    EXERCISE_NAMES_I18N = {}


def _attach_gif_url(ex: dict) -> dict:
    """Attach gif_url + override youtube_id + i18n names."""
    if not ex or not isinstance(ex, dict):
        return ex
    eid = ex.get("id")
    name = ex.get("name") or ""
    out = dict(ex)
    if eid and eid in EXERCISE_GIF_MAP:
        out["gif_url"] = f"/api/exercises/{eid}/gif"
    if eid and eid in YT_OVERRIDES:
        out["youtube_id"] = YT_OVERRIDES[eid]["youtube_id"]
    tr = EXERCISE_NAMES_I18N.get(name)
    if tr and isinstance(tr, dict):
        out["name_i18n"] = {"es": name, **tr}
    return out

PREMIUM_PACKAGES = {
    "monthly":   {"amount": 6.99, "currency": "eur", "name": "Kinetix PRO Mensual",    "interval": "month",  "interval_count": 1},
    "quarterly": {"amount": 19.99, "currency": "eur", "name": "Kinetix PRO Trimestral", "interval": "month",  "interval_count": 3},
    "yearly":    {"amount": 59.99, "currency": "eur", "name": "Kinetix PRO Anual",      "interval": "year",   "interval_count": 1},
    # Legacy alias (one-time used in older code)
    "premium_monthly": {"amount": 6.99, "currency": "eur", "name": "Kinetix PRO Mensual"},
}

app = FastAPI()
api = APIRouter(prefix="/api")

# ============================================================================
# RATE LIMITING (slowapi) — In-memory + swallow_errors for resilience
# Note: per-worker counters (we have 2 workers), so effective limit = config × 2.
# We halve the configured limits to compensate, achieving target effective rate.
# ============================================================================
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],  # effective ~200/min across 2 workers
    swallow_errors=True,             # never 500 on any internal error
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
logging.info("Rate limiter: in-memory, swallow_errors enabled")

# ---------------- Helpers ----------------
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

async def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "No autenticado")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token inválido")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(401, "Usuario no encontrado")
    if user.get("is_banned"):
        raise HTTPException(403, "Tu cuenta ha sido suspendida. Contacta con soporte.")
    return user

# ---------------- Models ----------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str = Field(min_length=1, max_length=60)

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_premium: bool = False
    is_admin: bool = False
    onboarding_done: bool = False
    fitness_goal: Optional[str] = None
    fitness_level: Optional[str] = None
    days_per_week: Optional[int] = None
    avatar_url: Optional[str] = None
    created_at: str
    xp: int = 0
    level: int = 0

class AuthOut(BaseModel):
    user: UserOut
    token: str

class ExerciseIn(BaseModel):
    name: str
    sets: int = 3
    reps: int = 10
    weight: float = 0.0
    rest_seconds: int = 120
    image_url: Optional[str] = None
    muscle_group: Optional[str] = None
    notes: Optional[str] = None
    superset_group: Optional[str] = None  # ej: "A", "B" — ejercicios con misma letra forman superserie
    # Library enrichment (added so AI-generated routines show animated GIF + YouTube
    # tutorial just like predefined routines do).
    exercise_id: Optional[str] = None       # id of the exercise in the catalog (used to load gif on demand)
    equipment: Optional[str] = None
    gif_url: Optional[str] = None
    youtube_id: Optional[str] = None
    instructions: Optional[Union[str, List[str]]] = None
    tips: Optional[Union[str, List[str]]] = None
    # Cardio fields (optional; is_cardio=True means this block is a cardio activity)
    is_cardio: bool = False
    cardio_type: Optional[str] = None       # "correr" | "bici" | "elíptica" | "remo" | "escalera" | "natación" | "otro"
    location: Optional[str] = None          # "cinta" | "calle" | "pista" | "gimnasio"
    duration_min: Optional[int] = None      # minutos objetivo
    distance_km: Optional[float] = None     # km objetivo (opcional)
    target_pace: Optional[str] = None       # ej. "5:30/km" (opcional)

class Exercise(ExerciseIn):
    id: str

class RoutineIn(BaseModel):
    name: str
    description: Optional[str] = ""
    exercises: List[ExerciseIn] = []
    is_premium_routine: bool = False
    price_eur: float = 0.0
    cover_image_url: Optional[str] = None

class Routine(BaseModel):
    id: str
    user_id: Optional[str] = None  # None = predefined
    name: str
    description: str = ""
    exercises: List[Exercise] = []
    is_predefined: bool = False
    is_public: bool = False
    is_premium_routine: bool = False
    price_eur: float = 0.0
    cover_image_url: Optional[str] = None
    creator_name: Optional[str] = None
    original_id: Optional[str] = None
    saves_count: int = 0
    # Marketing fields (P1 — sells more premium routines)
    level: Optional[str] = None  # "principiante" | "intermedio" | "avanzado"
    duration_weeks: Optional[int] = None
    benefits: List[str] = []  # ["Más fuerza", "Pierde 5kg", ...]
    goal: Optional[str] = None  # "fuerza" | "hipertrofia" | "perdida-grasa" | "tonificar" | "resistencia"
    rating_avg: float = 0.0
    rating_count: int = 0
    created_at: str

class WorkoutSetLog(BaseModel):
    exercise_name: str
    sets_completed: int
    reps: int
    weight: float

class WorkoutSessionIn(BaseModel):
    routine_id: Optional[str] = None
    routine_name: str
    duration_seconds: int
    logs: List[WorkoutSetLog] = []

class WorkoutSession(WorkoutSessionIn):
    id: str
    user_id: str
    created_at: str
    total_volume: float = 0.0  # sum(sets*reps*weight)

class CardioSessionIn(BaseModel):
    activity: Literal["correr", "caminar", "ciclismo"]
    distance_km: float
    duration_seconds: int
    notes: Optional[str] = ""

class CardioSession(CardioSessionIn):
    id: str
    user_id: str
    created_at: str

class AIChatIn(BaseModel):
    message: str
    session_id: Optional[str] = None

# ---------------- Auth Routes ----------------
@api.post("/auth/register", response_model=AuthOut)
@limiter.limit("5/minute")
async def register(request: Request, body: RegisterIn, response: Response):
    email = body.email.lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(400, "El email ya está registrado")
    user_id = str(uuid.uuid4())
    doc = {
        "id": user_id,
        "email": email,
        "name": body.name,
        "password_hash": hash_password(body.password),
        "is_premium": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    token = create_token(user_id, email)
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=604800, path="/")
    return {"user": {**{k: doc[k] for k in ["id", "email", "name", "is_premium", "created_at"]}, "is_admin": email in ADMIN_EMAILS}, "token": token}

@api.post("/auth/login", response_model=AuthOut)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginIn, response: Response):
    email = body.email.lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Email o contraseña incorrectos")
    token = create_token(user["id"], email)
    response.set_cookie("access_token", token, httponly=True, samesite="lax", max_age=604800, path="/")
    return {
        "user": {**{k: user[k] for k in ["id", "email", "name", "is_premium", "created_at"]}, "is_admin": _is_admin(user)},
        "token": token,
    }

@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


# ---------------- Password Reset (Forgot Password) ----------------
import secrets as _secrets
import smtplib as _smtplib
from email.mime.text import MIMEText as _MIMEText
from email.mime.multipart import MIMEMultipart as _MIMEMultipart
import asyncio as _asyncio

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Kinetix")
RESET_CODE_TTL_MINUTES = 30


def _send_reset_email_sync(to_email: str, name: str, code: str) -> bool:
    """Send password-reset email via SMTP (Gmail). Returns True on success."""
    if not SMTP_USER or not SMTP_PASSWORD:
        return False
    try:
        msg = _MIMEMultipart("alternative")
        msg["Subject"] = "Recuperación de contraseña - Kinetix"
        msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
        msg["To"] = to_email
        text_body = (
            f"Hola {name},\n\n"
            f"Has solicitado restablecer tu contraseña en Kinetix.\n\n"
            f"Tu código de verificación es: {code}\n\n"
            f"Este código caduca en {RESET_CODE_TTL_MINUTES} minutos.\n"
            f"Si no has sido tú, ignora este correo.\n\n"
            f"-- Equipo Kinetix"
        )
        html_body = f"""
        <div style="font-family:-apple-system,Arial,sans-serif;background:#000;color:#fff;padding:32px;border-radius:12px;max-width:480px;margin:auto">
          <div style="text-align:center;margin-bottom:24px">
            <h1 style="color:#FF0000;font-size:36px;letter-spacing:4px;margin:0">KINETIX</h1>
          </div>
          <h2 style="color:#fff;font-size:18px">Hola {name},</h2>
          <p style="color:#bbb;line-height:1.6">Has solicitado restablecer tu contraseña.</p>
          <p style="color:#bbb;line-height:1.6">Introduce el siguiente código en la app:</p>
          <div style="background:#FF0000;color:#000;font-size:36px;font-weight:900;letter-spacing:8px;text-align:center;padding:24px;border-radius:8px;margin:24px 0">{code}</div>
          <p style="color:#888;font-size:13px">El código caduca en {RESET_CODE_TTL_MINUTES} minutos.<br>Si no has sido tú, ignora este correo.</p>
          <hr style="border:0;border-top:1px solid #222;margin:24px 0">
          <p style="color:#555;font-size:11px;text-align:center">Sé constante, sé imparable<br>— Kinetix</p>
        </div>
        """
        msg.attach(_MIMEText(text_body, "plain"))
        msg.attach(_MIMEText(html_body, "html"))
        with _smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.sendmail(SMTP_USER, [to_email], msg.as_string())
        return True
    except Exception as e:
        logging.exception(f"SMTP send failed: {e}")
        return False


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=6)


@api.post("/auth/forgot-password")
@limiter.limit("5/hour")
async def forgot_password(request: Request, body: ForgotIn):
    """Request a password-reset code. Always returns ok=True to avoid email enumeration."""
    email = body.email.lower().strip()
    user = await db.users.find_one({"email": email})
    response_payload = {"ok": True, "message": "Si el email existe, recibirás un código en tu correo."}
    if not user:
        # Don't leak whether the email exists
        return response_payload
    code = f"{_secrets.randbelow(1000000):06d}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=RESET_CODE_TTL_MINUTES)
    await db.password_resets.update_one(
        {"email": email},
        {"$set": {
            "email": email,
            "code": code,
            "expires_at": expires.isoformat(),
            "used": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    # Send email in background thread to not block
    name = user.get("name", "atleta")
    try:
        sent = await _asyncio.get_event_loop().run_in_executor(
            None, _send_reset_email_sync, email, name, code
        )
    except Exception:
        sent = False
    response_payload["email_sent"] = bool(sent)
    if not sent:
        # Fallback for development / when SMTP not configured: include code
        response_payload["dev_code"] = code
        response_payload["message"] = (
            "No se pudo enviar el email. Usa el código que aparece abajo (modo desarrollo)."
        )
    return response_payload


@api.post("/auth/reset-password")
async def reset_password(body: ResetIn):
    """Verify code and set new password."""
    email = body.email.lower().strip()
    rec = await db.password_resets.find_one({"email": email})
    if not rec:
        raise HTTPException(400, "Código inválido o expirado")
    if rec.get("used"):
        raise HTTPException(400, "Este código ya fue utilizado")
    try:
        expires_at = datetime.fromisoformat(rec["expires_at"])
    except Exception:
        raise HTTPException(400, "Código inválido")
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(400, "El código ha expirado. Solicita uno nuevo.")
    if body.code.strip() != rec.get("code"):
        raise HTTPException(400, "Código incorrecto")
    user = await db.users.find_one({"email": email})
    if not user:
        raise HTTPException(404, "Usuario no encontrado")
    new_hash = hash_password(body.new_password)
    await db.users.update_one({"id": user["id"]}, {"$set": {"password_hash": new_hash}})
    await db.password_resets.update_one({"email": email}, {"$set": {"used": True}})
    return {"ok": True, "message": "Contraseña actualizada. Ya puedes iniciar sesión."}


ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "info.kinetixapp@gmail.com,admin@fitness.com").split(",") if e.strip()}

def _is_admin(user) -> bool:
    return user.get("is_admin", False) or user.get("email", "").lower() in ADMIN_EMAILS

def require_admin(user=Depends(get_current_user)):
    if not _is_admin(user):
        raise HTTPException(403, "Acceso de admin requerido")
    return user

@api.get("/auth/me", response_model=UserOut)
async def me(user=Depends(get_current_user)):
    user["is_admin"] = _is_admin(user)
    return user

@api.post("/auth/upgrade", response_model=UserOut)
async def upgrade_premium(user=Depends(get_current_user)):
    """Mock premium upgrade toggle."""
    new_state = not user.get("is_premium", False)
    await db.users.update_one({"id": user["id"]}, {"$set": {"is_premium": new_state}})
    user["is_premium"] = new_state
    return user

# ---------------- Routines ----------------
@api.get("/routines/predefined", response_model=List[Routine])
async def get_predefined():
    items = await db.routines.find({"is_predefined": True}, {"_id": 0}).to_list(100)
    return items

@api.get("/routines/premium")
async def list_premium_routines(user=Depends(get_current_user)):
    """Routines flagged as premium. Subscription does NOT unlock these — must be purchased individually."""
    items = await db.routines.find({"is_premium_routine": True}, {"_id": 0}).sort("name", 1).to_list(100)
    me = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    purchased = (me or {}).get("purchased_routines", [])
    is_admin_user = _is_admin(me or user)
    for it in items:
        # Each premium routine is unlocked ONLY by individual purchase or admin.
        it["unlocked"] = is_admin_user or it["id"] in purchased
    return items

@api.get("/routines", response_model=List[Routine])
async def list_routines(user=Depends(get_current_user)):
    items = await db.routines.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items

@api.post("/routines", response_model=Routine)
async def create_routine(body: RoutineIn, user=Depends(get_current_user)):
    if not user.get("is_premium"):
        count = await db.routines.count_documents({"user_id": user["id"]})
        if count >= FREE_ROUTINE_LIMIT:
            raise HTTPException(402, f"Límite gratis alcanzado ({FREE_ROUTINE_LIMIT} rutinas). Actualiza a Premium para crear más.")
    rid = str(uuid.uuid4())
    exercises = [{**e.dict(), "id": str(uuid.uuid4())} for e in body.exercises]
    doc = {
        "id": rid,
        "user_id": user["id"],
        "name": body.name,
        "description": body.description or "",
        "exercises": exercises,
        "is_predefined": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.routines.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api.get("/routines/{rid}", response_model=Routine)
async def get_routine(rid: str, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid}, {"_id": 0})
    if not r:
        raise HTTPException(404, "Rutina no encontrada")
    # Owner / predefined access
    is_owner = r.get("user_id") == user["id"]
    if not r.get("is_predefined") and not is_owner and not r.get("is_public") and not r.get("is_premium_routine"):
        raise HTTPException(403, "No autorizado")
    # Premium routine lock — must purchase individually (subscription does NOT unlock)
    if r.get("is_premium_routine") and not is_owner and not _is_admin(user):
        purchased = (user.get("purchased_routines") or [])
        if r["id"] not in purchased:
            raise HTTPException(status_code=402, detail={
                "code": "premium_routine_locked",
                "routine_id": r["id"],
                "routine_name": r.get("name"),
                "price_eur": r.get("price_eur", 3.99),
                "message": "Rutina premium bloqueada. Compra individual requerida.",
            })
    return r

@api.put("/routines/{rid}", response_model=Routine)
async def update_routine(rid: str, body: RoutineIn, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid})
    if not r:
        raise HTTPException(404, "No encontrada")
    if r.get("user_id") != user["id"]:
        raise HTTPException(403, "No autorizado")
    exercises = [{**e.dict(), "id": str(uuid.uuid4())} for e in body.exercises]
    await db.routines.update_one(
        {"id": rid},
        {"$set": {"name": body.name, "description": body.description or "", "exercises": exercises}},
    )
    r2 = await db.routines.find_one({"id": rid}, {"_id": 0})
    return r2

@api.delete("/routines/{rid}")
async def delete_routine(rid: str, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid})
    if not r or r.get("user_id") != user["id"]:
        raise HTTPException(404, "No encontrada")
    await db.routines.delete_one({"id": rid})
    return {"ok": True}

# ---------------- Social: publish, save, feed, follow ----------------
@api.post("/routines/{rid}/toggle-public")
async def toggle_public(rid: str, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid})
    if not r or r.get("user_id") != user["id"]:
        raise HTTPException(404, "No encontrada")
    new_state = not r.get("is_public", False)
    await db.routines.update_one(
        {"id": rid},
        {"$set": {"is_public": new_state, "creator_name": user["name"]}},
    )
    return {"is_public": new_state}

@api.post("/routines/{rid}/save")
async def save_routine_copy(rid: str, user=Depends(get_current_user)):
    """Copy a public routine to my routines."""
    src = await db.routines.find_one({"id": rid}, {"_id": 0})
    if not src:
        raise HTTPException(404, "No encontrada")
    if not src.get("is_public") and not src.get("is_predefined"):
        raise HTTPException(403, "Esta rutina no es pública")
    if not user.get("is_premium"):
        count = await db.routines.count_documents({"user_id": user["id"]})
        if count >= FREE_ROUTINE_LIMIT:
            raise HTTPException(403, f"Límite gratis alcanzado ({FREE_ROUTINE_LIMIT})")
    new_id = str(uuid.uuid4())
    exercises = [{**e, "id": str(uuid.uuid4())} for e in src.get("exercises", [])]
    doc = {
        "id": new_id,
        "user_id": user["id"],
        "name": src["name"],
        "description": src.get("description", ""),
        "exercises": exercises,
        "is_predefined": False,
        "is_public": False,
        "original_id": src["id"],
        "creator_name": src.get("creator_name"),
        "saves_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.routines.insert_one(doc)
    await db.routines.update_one({"id": rid}, {"$inc": {"saves_count": 1}})
    doc.pop("_id", None)
    return doc

@api.get("/feed")
async def routine_feed(user=Depends(get_current_user), sort: str = "trending"):
    """Public routines feed. sort: trending (saves), recent, following."""
    me = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    following = me.get("following", []) if me else []
    base_filter = {"is_public": True, "user_id": {"$ne": user["id"]}}
    if sort == "following":
        if not following:
            return []
        base_filter["user_id"] = {"$in": following}
        cursor = db.routines.find(base_filter, {"_id": 0}).sort("created_at", -1).limit(100)
    elif sort == "recent":
        cursor = db.routines.find(base_filter, {"_id": 0}).sort("created_at", -1).limit(100)
    else:  # trending
        cursor = db.routines.find(base_filter, {"_id": 0}).sort("saves_count", -1).limit(100)
    items = await cursor.to_list(100)
    # Enrich with creator avatar
    creator_ids = list({it.get("user_id") for it in items if it.get("user_id")})
    creators = await db.users.find({"id": {"$in": creator_ids}}, {"_id": 0, "id": 1, "avatar_url": 1}).to_list(len(creator_ids)) if creator_ids else []
    avatar_by_id = {c["id"]: c.get("avatar_url") for c in creators}
    for it in items:
        it["from_following"] = it.get("user_id") in following
        it["creator_avatar"] = avatar_by_id.get(it.get("user_id"))
    if sort == "trending":
        # boost followed in trending
        items.sort(key=lambda x: (not x.get("from_following"), -x.get("saves_count", 0)))
    return items

@api.get("/users/search")
async def search_users(q: str = "", limit: int = 20, user=Depends(get_current_user)):
    """Search creators by name or email (partial match)."""
    if not q or len(q) < 2:
        # Suggested: top creators by saves; fallback to most recent users
        pipeline = [
            {"$match": {"is_public": True}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "saves": {"$sum": "$saves_count"}}},
            {"$sort": {"saves": -1, "count": -1}},
            {"$limit": limit},
        ]
        agg = await db.routines.aggregate(pipeline).to_list(limit)
        ids = [a["_id"] for a in agg if a["_id"]]
        users = await db.users.find({"id": {"$in": ids}}, {"_id": 0, "password_hash": 0, "email": 0}).to_list(limit) if ids else []
        # Fill with most recent users if no top creators yet
        if len(users) < limit:
            existing_ids = {u["id"] for u in users}
            extra = await db.users.find({"id": {"$nin": list(existing_ids)}}, {"_id": 0, "password_hash": 0}).sort("created_at", -1).limit(limit - len(users)).to_list(limit)
            users.extend(extra)
    else:
        regex = {"$regex": re.escape(q), "$options": "i"}
        users = await db.users.find(
            {"$or": [{"name": regex}, {"email": regex}]},
            {"_id": 0, "password_hash": 0}
        ).limit(limit).to_list(limit)
    me = await db.users.find_one({"id": user["id"]}, {"following": 1})
    following = set((me or {}).get("following", []))
    out = []
    for u in users:
        if u["id"] == user["id"]:
            continue
        rt_count = await db.routines.count_documents({"user_id": u["id"], "is_public": True})
        # followers count
        followers = await db.users.count_documents({"following": u["id"]})
        out.append({
            "id": u["id"],
            "name": u.get("name"),
            "avatar_url": u.get("avatar_url"),
            "bio": u.get("bio") or "",
            "is_premium": u.get("is_premium", False),
            "public_routines_count": rt_count,
            "followers_count": followers,
            "is_following": u["id"] in following,
        })
    return out

@api.post("/users/{uid}/follow")
async def follow_user(uid: str, user=Depends(get_current_user)):
    if uid == user["id"]:
        raise HTTPException(400, "No puedes seguirte a ti mismo")
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    me = await db.users.find_one({"id": user["id"]}, {"following": 1})
    already_following = uid in (me.get("following", []) if me else [])
    await db.users.update_one({"id": user["id"]}, {"$addToSet": {"following": uid}})
    # ── XP (solo si es nuevo follow) ──
    if not already_following:
        await add_xp(user["id"], XP_REWARDS["follow"], f"follow:{uid}")
    return {"following": True}

@api.delete("/users/{uid}/follow")
async def unfollow_user(uid: str, user=Depends(get_current_user)):
    await db.users.update_one({"id": user["id"]}, {"$pull": {"following": uid}})
    return {"following": False}

@api.get("/users/{uid}")
async def get_user_profile(uid: str, user=Depends(get_current_user)):
    target = await db.users.find_one({"id": uid}, {"_id": 0, "password_hash": 0, "email": 0})
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    routines = await db.routines.find({"user_id": uid, "is_public": True}, {"_id": 0}).sort("saves_count", -1).to_list(100)
    me = await db.users.find_one({"id": user["id"]}, {"_id": 0})
    is_following = uid in (me.get("following", []) if me else [])
    followers_count = await db.users.count_documents({"following": uid})
    following_count = len(target.get("following") or [])
    # Total saves on their public routines (popularity)
    total_saves = sum(r.get("saves_count", 0) for r in routines)
    return {
        "user": {
            "id": target["id"],
            "name": target["name"],
            "avatar_url": target.get("avatar_url"),
            "bio": target.get("bio") or "",
            "is_premium": target.get("is_premium", False),
            "fitness_goal": target.get("fitness_goal"),
            "fitness_level": target.get("fitness_level"),
        },
        "public_routines": routines,
        "is_following": is_following,
        "is_self": uid == user["id"],
        "followers_count": followers_count,
        "following_count": following_count,
        "total_saves": total_saves,
    }

class ProfileUpdateIn(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None

@api.put("/auth/profile")
async def update_profile(body: ProfileUpdateIn, user=Depends(get_current_user)):
    upd = {}
    if body.name is not None and body.name.strip():
        upd["name"] = body.name.strip()[:50]
    if body.bio is not None:
        upd["bio"] = body.bio.strip()[:200]
    if upd:
        await db.users.update_one({"id": user["id"]}, {"$set": upd})
    return {"ok": True, **upd}

# ---------------- Workout Sessions ----------------
@api.post("/workouts", response_model=WorkoutSession)
async def log_workout(body: WorkoutSessionIn, user=Depends(get_current_user)):
    sid = str(uuid.uuid4())
    total = sum(l.sets_completed * l.reps * l.weight for l in body.logs)
    doc = {
        "id": sid,
        "user_id": user["id"],
        "routine_id": body.routine_id,
        "routine_name": body.routine_name,
        "duration_seconds": body.duration_seconds,
        "logs": [l.dict() for l in body.logs],
        "total_volume": total,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.workouts.insert_one(doc)
    doc.pop("_id", None)
    # ── XP ──
    xp_result = await add_xp(user["id"], XP_REWARDS["workout"], "workout")
    doc["xp_gained"] = xp_result
    return doc

@api.get("/workouts", response_model=List[WorkoutSession])
async def list_workouts(user=Depends(get_current_user)):
    items = await db.workouts.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items

# ---------------- Cardio ----------------
@api.post("/cardio", response_model=CardioSession)
async def log_cardio(body: CardioSessionIn, user=Depends(get_current_user)):
    sid = str(uuid.uuid4())
    doc = {
        "id": sid,
        "user_id": user["id"],
        **body.dict(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.cardio.insert_one(doc)
    doc.pop("_id", None)
    # ── XP ──
    xp_result = await add_xp(user["id"], XP_REWARDS["cardio"], "cardio")
    doc["xp_gained"] = xp_result
    return doc

@api.get("/cardio", response_model=List[CardioSession])
async def list_cardio(user=Depends(get_current_user)):
    items = await db.cardio.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items

# ---------------- Stats ----------------
@api.get("/stats")
async def stats(user=Depends(get_current_user)):
    workouts = await db.workouts.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    cardios = await db.cardio.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    total_workouts = len(workouts)
    total_volume = sum(w.get("total_volume", 0) for w in workouts)
    total_minutes = sum(w.get("duration_seconds", 0) for w in workouts) // 60
    total_distance = sum(c.get("distance_km", 0) for c in cardios)
    last7 = workouts[-7:]
    weekly_volume = [{"label": w["created_at"][5:10], "value": w.get("total_volume", 0)} for w in last7]
    last7c = cardios[-7:]
    weekly_distance = [{"label": c["created_at"][5:10], "value": c.get("distance_km", 0)} for c in last7c]

    # Insights: this week vs last week, top muscle group, streak
    now = datetime.now(timezone.utc)
    one_w = now - timedelta(days=7)
    two_w = now - timedelta(days=14)
    this_week = [w for w in workouts if datetime.fromisoformat(w["created_at"]) >= one_w]
    prev_week = [w for w in workouts if two_w <= datetime.fromisoformat(w["created_at"]) < one_w]
    tw_vol = sum(w.get("total_volume", 0) for w in this_week)
    pw_vol = sum(w.get("total_volume", 0) for w in prev_week)
    delta_pct = None
    if pw_vol > 0:
        delta_pct = round((tw_vol - pw_vol) / pw_vol * 100, 1)
    elif tw_vol > 0:
        delta_pct = 100.0
    # Top muscle group (last 30 days)
    from exercises_data import EXERCISES as EX
    name_to_muscle = {e["name"].lower(): e["muscle_group"] for e in EX}
    one_m = now - timedelta(days=30)
    muscle_vol = {}
    for w in workouts:
        if datetime.fromisoformat(w["created_at"]) < one_m: continue
        for log in w.get("logs", []):
            mg = name_to_muscle.get(log["exercise_name"].lower())
            if not mg: continue
            v = log.get("sets_completed", 0) * log.get("reps", 0) * log.get("weight", 0)
            muscle_vol[mg] = muscle_vol.get(mg, 0) + v
    top_muscle = max(muscle_vol.items(), key=lambda x: x[1])[0] if muscle_vol else None
    # Streak (consecutive days)
    workout_dates = sorted({w["created_at"][:10] for w in workouts}, reverse=True)
    streak = 0
    today = now.date()
    for i, d in enumerate(workout_dates):
        dd = datetime.fromisoformat(d).date()
        expect = today - timedelta(days=i)
        if dd == expect or (i == 0 and dd == today - timedelta(days=1)):
            streak += 1
        else:
            break

    return {
        "total_workouts": total_workouts,
        "total_volume": total_volume,
        "total_minutes": total_minutes,
        "total_cardio_sessions": len(cardios),
        "total_distance_km": round(total_distance, 2),
        "weekly_volume": weekly_volume,
        "weekly_distance": weekly_distance,
        "this_week_volume": round(tw_vol, 1),
        "prev_week_volume": round(pw_vol, 1),
        "delta_pct": delta_pct,
        "top_muscle_group": top_muscle,
        "streak_days": streak,
        "this_week_count": len(this_week),
    }

# ---------------- Exercise Library ----------------
EXERCISE_LIBRARY = [
    {"id": "press-banca", "name": "Press de Banca", "muscle_group": "Pecho", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg"},
    {"id": "sentadilla", "name": "Sentadilla", "muscle_group": "Piernas", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
    {"id": "peso-muerto", "name": "Peso Muerto", "muscle_group": "Espalda", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg"},
    {"id": "curl-biceps", "name": "Curl de Bíceps", "muscle_group": "Bíceps", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
    {"id": "press-militar", "name": "Press Militar", "muscle_group": "Hombros", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg"},
    {"id": "dominadas", "name": "Dominadas", "muscle_group": "Espalda", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
    {"id": "fondos", "name": "Fondos", "muscle_group": "Tríceps", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg"},
    {"id": "remo", "name": "Remo con Barra", "muscle_group": "Espalda", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
    {"id": "zancadas", "name": "Zancadas", "muscle_group": "Piernas", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
    {"id": "abdominales", "name": "Abdominales", "muscle_group": "Core", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd"},
]

# ---------------- Static-data caches (in-memory, TTL-based) ----------------
import time as _time
_CACHE_STORE: dict = {}  # key -> (expires_at, value)
_CACHE_TTL = 300  # 5 minutes

def _cache_get(key: str):
    rec = _CACHE_STORE.get(key)
    if rec and rec[0] > _time.time():
        return rec[1]
    return None

def _cache_set(key: str, value, ttl: int = _CACHE_TTL):
    _CACHE_STORE[key] = (_time.time() + ttl, value)

def _cache_invalidate_prefix(prefix: str):
    for k in list(_CACHE_STORE.keys()):
        if k.startswith(prefix):
            _CACHE_STORE.pop(k, None)


@api.get("/exercises/library")
async def exercise_library(q: str = "", muscle: str = "", category: str = ""):
    # No in-process cache: static data is already in-memory (list_exercises),
    # and custom_exercises query is indexed + small. Multi-worker-safe.
    items = list(list_exercises(q, muscle, category))
    # Merge in custom admin-added exercises
    custom = await db.custom_exercises.find({}, {"_id": 0}).to_list(500)
    for c in custom:
        if category and c.get("category", "").lower() != category.lower(): continue
        if muscle and c.get("muscle_group", "").lower() != muscle.lower(): continue
        if q and q.lower() not in c.get("name", "").lower() and q.lower() not in c.get("muscle_group", "").lower(): continue
        items.append(c)
    items = [_attach_gif_url(e) for e in items]
    return items

@api.get("/exercises/groups")
async def exercise_groups():
    return muscle_groups()

@api.get("/exercises/categories")
async def exercise_categories():
    return categories()

@api.get("/exercises/{eid}/gif")
async def exercise_gif(eid: str):
    """Lazy-cache proxy: serves the GIF locally if cached, else fetches from ExerciseDB
    (using RAPIDAPI_KEY), caches to disk, and returns the bytes."""
    from fastapi.responses import FileResponse, Response
    exdb_id = EXERCISE_GIF_MAP.get(eid, {}).get("exdb_id")
    if not exdb_id:
        raise HTTPException(404, "Sin GIF disponible para este ejercicio")
    path = os.path.join(GIF_DIR, f"{exdb_id}.gif")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/gif", headers={"Cache-Control": "public, max-age=604800"})
    # Lazy fetch from ExerciseDB
    api_key = os.getenv("RAPIDAPI_KEY", "")
    if not api_key:
        raise HTTPException(502, "RAPIDAPI_KEY not configured")
    try:
        import requests as _req
        url = f"https://exercisedb.p.rapidapi.com/image?exerciseId={exdb_id}&resolution=360&rapidapi-key={api_key}"
        r = _req.get(url, timeout=10)
        if r.status_code == 200 and r.content[:3] == b"GIF":
            os.makedirs(GIF_DIR, exist_ok=True)
            with open(path, "wb") as f:
                f.write(r.content)
            return Response(content=r.content, media_type="image/gif",
                            headers={"Cache-Control": "public, max-age=604800"})
        raise HTTPException(502, f"GIF fetch failed: HTTP {r.status_code}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"GIF fetch error: {e}")

@api.get("/exercises/{eid}")
async def exercise_detail(eid: str):
    e = get_exercise(eid)
    if not e:
        raise HTTPException(404, "Ejercicio no encontrado")
    return _attach_gif_url(dict(e))

# ---------------- AI Chat (Gemini) ----------------
# Max AI chat messages granted for unlocking all achievements (non-premium reward)
ACHIEVEMENT_AI_MAX_MESSAGES = 2


async def _achievement_ai_used(user_id: str) -> int:
    """Count AI messages consumed while using the achievement-unlock grant
    (only relevant for non-premium users)."""
    try:
        return await db.ai_messages.count_documents({
            "user_id": user_id,
            "via_achievements": True,
        })
    except Exception:
        return 0


@api.post("/ai/chat")
async def ai_chat(body: AIChatIn, user=Depends(get_current_user)):
    # AI Coach access logic: Premium → unlimited. Non-premium w/ all achievements → max 2 messages.
    is_prem = bool(user.get("is_premium"))
    has_all_ach = _has_all_achievements(user)
    via_ach_used = 0
    if not is_prem:
        if not has_all_ach:
            raise HTTPException(402, "El Coach IA es exclusivo Premium. Hazte Premium o desbloquea todos los logros para hablar con tu entrenador personal.")
        # non-premium but has all achievements → enforce 2-message cap
        via_ach_used = await _achievement_ai_used(user["id"])
        if via_ach_used >= ACHIEVEMENT_AI_MAX_MESSAGES:
            raise HTTPException(
                402,
                f"Has usado tus {ACHIEVEMENT_AI_MAX_MESSAGES} consultas gratis del Coach IA por logros. Hazte Premium para consultas ilimitadas.",
            )
    if not EMERGENT_LLM_KEY:
        raise HTTPException(500, "AI no configurado")
    session_id = body.session_id or f"user-{user['id']}"
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=(
            "Eres un entrenador personal experto en fitness y nutrición. "
            "Respondes SIEMPRE en español, de forma clara, motivadora y breve (máximo 200 palabras). "
            "Das consejos personalizados sobre rutinas, ejercicios, descanso, alimentación y técnica. "
            "Si te piden una rutina, devuelve una lista de ejercicios con series y repeticiones."
        ),
    ).with_model("gemini", "gemini-2.5-flash")
    try:
        response = await chat.send_message(UserMessage(text=body.message))
    except Exception as e:
        logging.exception("AI error")
        raise HTTPException(500, f"Error en IA: {e}")
    await db.ai_messages.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "session_id": session_id,
        "user_message": body.message,
        "ai_response": response,
        "via_achievements": (not is_prem) and has_all_ach,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    remaining_ach = None
    if not is_prem and has_all_ach:
        remaining_ach = max(0, ACHIEVEMENT_AI_MAX_MESSAGES - (via_ach_used + 1))
    return {
        "response": response,
        "session_id": session_id,
        "remaining_free": None,
        "remaining_achievement_grant": remaining_ach,
    }

@api.get("/ai/history")
async def ai_history(user=Depends(get_current_user)):
    if not user.get("is_premium"):
        return []
    items = await db.ai_messages.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(200)
    return items

# ---------------- Body weight tracking ----------------
class BodyEntryIn(BaseModel):
    weight_kg: float
    notes: Optional[str] = ""

@api.post("/body-entries")
async def add_body_entry(body: BodyEntryIn, user=Depends(get_current_user)):
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "weight_kg": body.weight_kg,
        "notes": body.notes or "",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.body_entries.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api.get("/body-entries")
async def list_body_entries(user=Depends(get_current_user)):
    items = await db.body_entries.find({"user_id": user["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    return items

@api.delete("/body-entries/{eid}")
async def del_body_entry(eid: str, user=Depends(get_current_user)):
    r = await db.body_entries.find_one({"id": eid})
    if not r or r.get("user_id") != user["id"]:
        raise HTTPException(404, "No encontrada")
    await db.body_entries.delete_one({"id": eid})
    return {"ok": True}

# ---------------- Personal Records (PRs) ----------------
@api.get("/stats/prs")
async def get_prs(user=Depends(get_current_user)):
    workouts = await db.workouts.find({"user_id": user["id"]}, {"_id": 0}).to_list(2000)
    prs: dict = {}
    for w in workouts:
        for log in w.get("logs", []):
            name = log["exercise_name"]
            weight = log.get("weight", 0)
            reps = log.get("reps", 0)
            if weight <= 0 or reps <= 0:
                continue
            # Brzycki 1RM formula
            one_rm = weight * 36 / max(1, 37 - reps) if reps < 37 else weight
            cur = prs.get(name)
            if cur is None or weight > cur["max_weight"] or one_rm > cur["est_1rm"]:
                if cur is None:
                    prs[name] = {"exercise": name, "max_weight": weight, "max_reps_at_max": reps, "est_1rm": round(one_rm, 1), "date": w["created_at"][:10]}
                else:
                    if weight > cur["max_weight"]:
                        cur["max_weight"] = weight
                        cur["max_reps_at_max"] = reps
                        cur["date"] = w["created_at"][:10]
                    if one_rm > cur["est_1rm"]:
                        cur["est_1rm"] = round(one_rm, 1)
    return sorted(prs.values(), key=lambda x: -x["est_1rm"])

# ---------------- AI Routine Generator ----------------
class AIGenIn(BaseModel):
    prompt: str

@api.post("/ai/generate-routine", response_model=Routine)
async def ai_generate_routine(body: AIGenIn, user=Depends(get_current_user)):
    # Premium → unlimited. Non-premium w/ all achievements → counts against the 2-msg cap.
    is_prem = bool(user.get("is_premium"))
    has_all_ach = _has_all_achievements(user)
    if not is_prem:
        if not has_all_ach:
            raise HTTPException(402, "El generador de rutinas IA es exclusivo Premium. Hazte Premium o desbloquea todos los logros para usarlo.")
        used = await _achievement_ai_used(user["id"])
        if used >= ACHIEVEMENT_AI_MAX_MESSAGES:
            raise HTTPException(
                402,
                f"Has usado tus {ACHIEVEMENT_AI_MAX_MESSAGES} consultas gratis del Coach IA por logros. Hazte Premium para consultas ilimitadas.",
            )
    if not EMERGENT_LLM_KEY:
        raise HTTPException(500, "AI no configurado")

    # Build a compact catalog of exercises the AI can choose from.
    # We focus on gym exercises (what most people ask for). Include a short tag per muscle group.
    gym_exercises = [e for e in FULL_EXERCISES if e.get("category") == "gym"]
    # Compact lines: "NAME | MUSCLE | EQUIP"
    catalog_lines = [
        f"- {e['name']} | {e.get('muscle_group','?')} | {e.get('equipment','?')}"
        for e in gym_exercises
    ]
    catalog_text = "\n".join(catalog_lines)

    system_message = (
        "Eres un entrenador personal experto. Genera UNA rutina de gimnasio basada en la petición del usuario. "
        "⚠️ REGLA CRÍTICA: DEBES elegir los ejercicios EXCLUSIVAMENTE del siguiente catálogo. "
        "COPIA el nombre de cada ejercicio EXACTAMENTE como aparece (mismas mayúsculas, acentos y puntuación). "
        "Está prohibido inventar ejercicios o modificar sus nombres.\n\n"
        f"CATÁLOGO ({len(gym_exercises)} ejercicios disponibles):\n{catalog_text}\n\n"
        "Responde EXCLUSIVAMENTE en formato JSON válido (sin texto adicional, sin markdown, sin ```), con esta estructura:\n"
        '{"name": "nombre de la rutina", "description": "descripción breve", "exercises": '
        '[{"name": "NOMBRE EXACTO DEL CATÁLOGO", "sets": 4, "reps": 10, "weight": 20, "rest_seconds": 90}]}\n\n'
        "Incluye 4-7 ejercicios. weight en kg (0 si es peso corporal). rest_seconds entre 30 y 180."
    )
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=f"gen-{user['id']}-{uuid.uuid4()}",
        system_message=system_message,
    ).with_model("gemini", "gemini-2.5-flash")
    try:
        raw = await chat.send_message(UserMessage(text=body.prompt))
    except Exception as e:
        raise HTTPException(500, f"Error IA: {e}")

    # Extract JSON
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise HTTPException(500, "La IA no devolvió JSON válido")
    try:
        data = json_lib.loads(match.group(0))
    except Exception:
        raise HTTPException(500, "Error parseando JSON de la IA")

    # Build a lookup: normalize name -> library entry
    import unicodedata as _ud
    def _norm(s: str) -> str:
        s = _ud.normalize("NFD", s or "")
        s = "".join(c for c in s if not _ud.combining(c))
        s = s.lower().strip()
        # Collapse whitespace / minor punctuation
        s = re.sub(r"[^a-z0-9 ]", " ", s)
        s = re.sub(r"\s+", " ", s)
        return s

    library_by_norm = {_norm(e["name"]): e for e in FULL_EXERCISES}
    library_names_norm = list(library_by_norm.keys())

    from difflib import get_close_matches as _gcm

    rid = str(uuid.uuid4())
    exercises = []
    skipped = []
    for e in data.get("exercises", [])[:15]:
        raw_name = (e.get("name") or "").strip()
        if not raw_name:
            continue
        key = _norm(raw_name)
        lib = library_by_norm.get(key)
        if not lib:
            # Fuzzy match with reasonably tight threshold to avoid nonsense mapping
            candidates = _gcm(key, library_names_norm, n=1, cutoff=0.65)
            if candidates:
                lib = library_by_norm[candidates[0]]
        if not lib:
            skipped.append(raw_name)
            continue  # Drop unknown exercises — don't pollute routine

        # Enrich with gif_url + youtube_id + i18n the same way the library endpoint does
        # so the routine viewer can show the animated GIF + tutorial like predefined routines.
        try:
            lib_full = _attach_gif_url(dict(lib))
        except Exception:
            lib_full = lib

        exercises.append({
            "id": str(uuid.uuid4()),
            "exercise_id": lib_full.get("id") or lib.get("id"),
            "name": lib_full.get("name") or lib["name"],
            "sets": int(e.get("sets", 3)),
            "reps": int(e.get("reps", 10)),
            "weight": float(e.get("weight", 0)),
            "rest_seconds": int(e.get("rest_seconds", 90)),
            "muscle_group": lib_full.get("muscle_group", ""),
            "equipment": lib_full.get("equipment", ""),
            "image_url": lib_full.get("image_url") or "https://images.unsplash.com/photo-1672344048213-76b6e77304bd",
            "gif_url": lib_full.get("gif_url"),
            "youtube_id": lib_full.get("youtube_id"),
            "instructions": lib_full.get("instructions", ""),
            "tips": lib_full.get("tips", ""),
            "notes": "",
        })
    if not exercises:
        # As a last resort, reject rather than save a broken routine
        raise HTTPException(500, f"La IA no pudo mapear ejercicios al catálogo (skipped: {skipped[:5]}). Vuelve a intentarlo reformulando.")
    if len(exercises) < 3:
        logging.warning(f"AI routine only {len(exercises)} valid exercises, skipped: {skipped}")

    doc = {
        "id": rid,
        "user_id": user["id"],
        "name": data.get("name", "Rutina IA")[:80],
        "description": (data.get("description", "Generada por IA"))[:200],
        "exercises": exercises,
        "is_predefined": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.routines.insert_one(doc)
    doc.pop("_id", None)
    # Count this generation against the achievement-grant cap if applicable
    try:
        await db.ai_messages.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "session_id": f"gen-{rid}",
            "user_message": body.prompt[:500],
            "ai_response": f"[routine:{rid}]",
            "via_achievements": (not is_prem) and has_all_ach,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass
    return doc

# ---------------- Stripe Payments ----------------
class CheckoutIn(BaseModel):
    package_id: str
    origin_url: str

class RoutineCheckoutIn(BaseModel):
    routine_id: str
    origin_url: str

@api.post("/payments/checkout-routine")
async def create_routine_checkout(body: RoutineCheckoutIn, request: Request, user=Depends(get_current_user)):
    """Per-routine purchase. Creates a Stripe checkout session for ONE specific premium routine."""
    if not STRIPE_API_KEY:
        raise HTTPException(500, "Stripe no configurado")
    r = await db.routines.find_one({"id": body.routine_id})
    if not r:
        raise HTTPException(404, "Rutina no encontrada")
    if not r.get("is_premium_routine"):
        raise HTTPException(400, "Esta rutina no es premium")
    purchased = (user.get("purchased_routines") or [])
    if r["id"] in purchased or _is_admin(user):
        return {"already_owned": True, "url": None, "session_id": None}
    price = float(r.get("price_eur") or 3.99)
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/payment-success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/premium"
    package_id = f"routine:{r['id']}"
    req = CheckoutSessionRequest(
        amount=price,
        currency="eur",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user["id"], "package_id": package_id, "routine_id": r["id"], "user_email": user["email"]},
    )
    session = await stripe.create_checkout_session(req)
    await db.payment_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "user_id": user["id"],
        "user_email": user["email"],
        "package_id": package_id,
        "routine_id": r["id"],
        "amount": price,
        "currency": "eur",
        "payment_status": "initiated",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"url": session.url, "session_id": session.session_id, "amount": price, "routine_name": r.get("name")}

@api.post("/payments/checkout")
async def create_checkout(body: CheckoutIn, request: Request, user=Depends(get_current_user)):
    if body.package_id not in PREMIUM_PACKAGES:
        raise HTTPException(400, "Paquete inválido")
    if not STRIPE_API_KEY:
        raise HTTPException(500, "Stripe no configurado")
    pkg = PREMIUM_PACKAGES[body.package_id]
    host_url = str(request.base_url).rstrip("/")
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe = StripeCheckout(api_key=STRIPE_API_KEY, webhook_url=webhook_url)
    origin = body.origin_url.rstrip("/")
    success_url = f"{origin}/payment-success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/premium"
    req = CheckoutSessionRequest(
        amount=float(pkg["amount"]),
        currency=pkg["currency"],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user["id"], "package_id": body.package_id, "user_email": user["email"]},
    )
    session = await stripe.create_checkout_session(req)
    await db.payment_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "session_id": session.session_id,
        "user_id": user["id"],
        "user_email": user["email"],
        "package_id": body.package_id,
        "amount": pkg["amount"],
        "currency": pkg["currency"],
        "payment_status": "initiated",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"url": session.url, "session_id": session.session_id}

# ---------------- RECURRING SUBSCRIPTION (auto-renewal) ----------------
class SubscribeIn(BaseModel):
    plan_id: str  # "monthly" | "quarterly" | "yearly"
    origin_url: str

# Cache of Stripe Price IDs per plan (lazy-created on first subscribe)
_PRICE_CACHE = {}

async def _get_or_create_price(plan_id: str) -> str:
    """Lazy-create a recurring Stripe Price for the plan. Cached in DB."""
    pkg = PREMIUM_PACKAGES[plan_id]
    if plan_id in _PRICE_CACHE:
        return _PRICE_CACHE[plan_id]
    cached = await db.app_config.find_one({"key": f"stripe_price_{plan_id}"})
    if cached and cached.get("price_id"):
        _PRICE_CACHE[plan_id] = cached["price_id"]
        return cached["price_id"]
    # Create product
    product = stripe_sdk.Product.create(name=pkg["name"], description=f"Suscripción {pkg['name']}")
    # Create recurring price
    price = stripe_sdk.Price.create(
        product=product.id,
        unit_amount=int(pkg["amount"] * 100),
        currency=pkg["currency"],
        recurring={"interval": pkg["interval"], "interval_count": pkg["interval_count"]},
    )
    await db.app_config.update_one(
        {"key": f"stripe_price_{plan_id}"},
        {"$set": {"price_id": price.id, "product_id": product.id, "created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    _PRICE_CACHE[plan_id] = price.id
    return price.id

@api.post("/payments/subscribe")
async def create_subscription(body: SubscribeIn, request: Request, user=Depends(get_current_user)):
    """Creates a Stripe Checkout Session in subscription mode (auto-renewing)."""
    if body.plan_id not in PREMIUM_PACKAGES or "interval" not in PREMIUM_PACKAGES[body.plan_id]:
        raise HTTPException(400, "Plan inválido. Usa 'monthly', 'quarterly' o 'yearly'.")
    if not STRIPE_API_KEY:
        raise HTTPException(500, "Stripe no configurado")
    # Already premium? Don't allow double-subscribing
    me = await db.users.find_one({"id": user["id"]})
    if me and me.get("stripe_subscription_id") and me.get("subscription_status") in ("active", "trialing"):
        raise HTTPException(400, "Ya tienes una suscripción activa. Cancélala primero si quieres cambiar de plan.")
    price_id = await _get_or_create_price(body.plan_id)
    # Create or reuse Stripe customer (validate it still exists in the LIVE account
    # -- defensive fix in case key was rotated or a stale sandbox id persists)
    customer_id = (me or {}).get("stripe_customer_id")
    if customer_id:
        try:
            stripe_sdk.Customer.retrieve(customer_id)
        except Exception:
            customer_id = None
            await db.users.update_one({"id": user["id"]}, {"$unset": {"stripe_customer_id": "", "stripe_subscription_id": ""}})
    if not customer_id:
        cust = stripe_sdk.Customer.create(email=user["email"], name=user.get("name"), metadata={"user_id": user["id"]})
        customer_id = cust.id
        await db.users.update_one({"id": user["id"]}, {"$set": {"stripe_customer_id": customer_id}})
    origin = body.origin_url.rstrip("/")
    session = stripe_sdk.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{origin}/payment-success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{origin}/premium",
        metadata={"user_id": user["id"], "plan_id": body.plan_id, "kind": "subscription"},
        subscription_data={"metadata": {"user_id": user["id"], "plan_id": body.plan_id}},
    )
    await db.payment_transactions.insert_one({
        "id": str(uuid.uuid4()),
        "session_id": session.id,
        "user_id": user["id"],
        "user_email": user["email"],
        "package_id": body.plan_id,
        "kind": "subscription",
        "amount": PREMIUM_PACKAGES[body.plan_id]["amount"],
        "currency": "eur",
        "payment_status": "initiated",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"url": session.url, "session_id": session.id, "plan_id": body.plan_id}

@api.get("/payments/subscription")
async def get_subscription_status(user=Depends(get_current_user)):
    """Returns current user's active subscription info (plan, next renewal, etc)."""
    me = await db.users.find_one({"id": user["id"]})
    sub_id = (me or {}).get("stripe_subscription_id")
    if not sub_id or not STRIPE_API_KEY:
        return {"active": False, "plan_id": None}
    try:
        sub = stripe_sdk.Subscription.retrieve(sub_id)
    except Exception:
        return {"active": False, "plan_id": None}
    interval = sub["items"]["data"][0]["price"]["recurring"]["interval"]
    interval_count = sub["items"]["data"][0]["price"]["recurring"]["interval_count"]
    plan_label = {"month-1": "Mensual", "month-3": "Trimestral", "year-1": "Anual"}.get(f"{interval}-{interval_count}", "Premium")
    return {
        "active": sub.status in ("active", "trialing"),
        "status": sub.status,
        "plan_id": (me or {}).get("subscription_plan_id"),
        "plan_label": plan_label,
        "current_period_start": sub.current_period_start,
        "current_period_end": sub.current_period_end,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "amount": sub["items"]["data"][0]["price"]["unit_amount"] / 100,
        "currency": sub["items"]["data"][0]["price"]["currency"],
        "interval": interval,
        "interval_count": interval_count,
    }

@api.post("/payments/cancel-subscription")
async def cancel_subscription(user=Depends(get_current_user)):
    """Cancels at period end — user keeps premium until current period finishes."""
    me = await db.users.find_one({"id": user["id"]})
    sub_id = (me or {}).get("stripe_subscription_id")
    if not sub_id:
        raise HTTPException(400, "No tienes suscripción activa")
    sub = stripe_sdk.Subscription.modify(sub_id, cancel_at_period_end=True)
    await db.users.update_one({"id": user["id"]}, {"$set": {"subscription_cancel_at_period_end": True}})
    return {"ok": True, "ends_at": sub.current_period_end}

@api.post("/payments/resume-subscription")
async def resume_subscription(user=Depends(get_current_user)):
    """Undoes a scheduled cancellation."""
    me = await db.users.find_one({"id": user["id"]})
    sub_id = (me or {}).get("stripe_subscription_id")
    if not sub_id:
        raise HTTPException(400, "No tienes suscripción")
    stripe_sdk.Subscription.modify(sub_id, cancel_at_period_end=False)
    await db.users.update_one({"id": user["id"]}, {"$set": {"subscription_cancel_at_period_end": False}})
    return {"ok": True}

@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, user=Depends(get_current_user)):
    if not STRIPE_API_KEY:
        raise HTTPException(500, "Stripe no configurado")
    tx = await db.payment_transactions.find_one({"session_id": session_id, "user_id": user["id"]})
    if not tx:
        raise HTTPException(404, "Sesión no encontrada")
    if tx.get("payment_status") == "paid":
        return {"payment_status": "paid", "status": "complete", "already_processed": True}
    # Use stripe SDK directly to avoid metadata-validation bug in emergentintegrations
    try:
        import stripe as stripe_sdk
        stripe_sdk.api_key = STRIPE_API_KEY
        sess = stripe_sdk.checkout.Session.retrieve(session_id)
        payment_status_val = sess.get("payment_status", "unpaid")
        status_val = sess.get("status", "open")
        amount_total = sess.get("amount_total", 0)
        currency = sess.get("currency", "eur")
    except Exception as e:
        logging.exception("Stripe status fetch failed")
        # Fall back to persisted state
        return {"payment_status": tx.get("payment_status", "pending"), "status": tx.get("status", "pending"), "fallback": True}
    upd = {"status": status_val, "payment_status": payment_status_val, "updated_at": datetime.now(timezone.utc).isoformat()}
    await db.payment_transactions.update_one({"session_id": session_id}, {"$set": upd})
    if payment_status_val == "paid":
        pkg_id = tx.get("package_id", "")
        if pkg_id.startswith("routine:"):
            rid = tx.get("routine_id") or pkg_id.split(":", 1)[1]
            await db.users.update_one({"id": user["id"]}, {"$addToSet": {"purchased_routines": rid}})
        else:
            await db.users.update_one({"id": user["id"]}, {"$set": {"is_premium": True}})
    return {"payment_status": payment_status_val, "status": status_val, "amount_total": amount_total, "currency": currency, "package_id": tx.get("package_id"), "routine_id": tx.get("routine_id")}

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    if not STRIPE_API_KEY:
        return {"ok": False}
    body_bytes = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    # Verify signature when possible
    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe_sdk.Webhook.construct_event(body_bytes, sig, STRIPE_WEBHOOK_SECRET)
        else:
            # Fallback (no signature verification, dev only)
            event = json_lib.loads(body_bytes)
    except Exception as e:
        logging.exception("Webhook signature error")
        return {"ok": False, "error": str(e)}

    etype = event["type"] if isinstance(event, dict) else event.type
    data = event["data"]["object"] if isinstance(event, dict) else event.data.object

    try:
        # ─── One-time checkout completed (rutina premium o pago suelto)
        if etype == "checkout.session.completed":
            session_id = data.get("id") if isinstance(data, dict) else data.id
            mode = data.get("mode") if isinstance(data, dict) else data.mode
            metadata = data.get("metadata", {}) if isinstance(data, dict) else (data.metadata or {})
            uid = metadata.get("user_id")
            if not uid:
                return {"ok": True}
            tx = await db.payment_transactions.find_one({"session_id": session_id})
            if mode == "subscription":
                # Subscription: persist subscription_id and mark premium
                sub_id = data.get("subscription") if isinstance(data, dict) else data.subscription
                plan_id = metadata.get("plan_id")
                await db.users.update_one({"id": uid}, {"$set": {
                    "is_premium": True,
                    "stripe_subscription_id": sub_id,
                    "subscription_plan_id": plan_id,
                    "subscription_started_at": datetime.now(timezone.utc).isoformat(),
                    "subscription_cancel_at_period_end": False,
                    "subscription_status": "active",
                }})
                if tx:
                    await db.payment_transactions.update_one({"session_id": session_id},
                        {"$set": {"payment_status": "paid", "status": "complete", "subscription_id": sub_id,
                                  "updated_at": datetime.now(timezone.utc).isoformat()}})
            else:
                # One-time payment (routine purchase)
                if tx:
                    await db.payment_transactions.update_one({"session_id": session_id},
                        {"$set": {"payment_status": "paid", "status": "complete", "updated_at": datetime.now(timezone.utc).isoformat()}})
                    pkg_id = tx.get("package_id", "")
                    if pkg_id.startswith("routine:"):
                        rid = tx.get("routine_id") or pkg_id.split(":", 1)[1]
                        await db.users.update_one({"id": uid}, {"$addToSet": {"purchased_routines": rid}})

        # ─── Subscription successfully renewed (recurring payment)
        elif etype == "invoice.paid":
            sub_id = data.get("subscription") if isinstance(data, dict) else data.subscription
            if sub_id:
                u = await db.users.find_one({"stripe_subscription_id": sub_id})
                if u:
                    await db.users.update_one({"id": u["id"]}, {"$set": {
                        "is_premium": True, "subscription_status": "active",
                        "last_renewal_at": datetime.now(timezone.utc).isoformat(),
                    }})

        # ─── Payment failed (card declined, etc.) — keep premium until end of period
        elif etype == "invoice.payment_failed":
            sub_id = data.get("subscription") if isinstance(data, dict) else data.subscription
            if sub_id:
                u = await db.users.find_one({"stripe_subscription_id": sub_id})
                if u:
                    await db.users.update_one({"id": u["id"]}, {"$set": {"subscription_status": "past_due"}})

        # ─── Subscription updated (cancel scheduled, etc.)
        elif etype == "customer.subscription.updated":
            sub_id = data.get("id") if isinstance(data, dict) else data.id
            cancel_at_period_end = data.get("cancel_at_period_end") if isinstance(data, dict) else data.cancel_at_period_end
            status_val = data.get("status") if isinstance(data, dict) else data.status
            await db.users.update_one({"stripe_subscription_id": sub_id},
                {"$set": {"subscription_cancel_at_period_end": bool(cancel_at_period_end),
                          "subscription_status": status_val}})

        # ─── Subscription cancelled (period ended)
        elif etype == "customer.subscription.deleted":
            sub_id = data.get("id") if isinstance(data, dict) else data.id
            u = await db.users.find_one({"stripe_subscription_id": sub_id})
            if u:
                await db.users.update_one({"id": u["id"]}, {"$set": {
                    "is_premium": False,
                    "subscription_status": "canceled",
                    "stripe_subscription_id": None,
                    "subscription_plan_id": None,
                    "subscription_canceled_at": datetime.now(timezone.utc).isoformat(),
                }})
    except Exception as e:
        logging.exception(f"Webhook handler error for {etype}: {e}")
    return {"ok": True}

# ---------------- Health ----------------
# Achievements definitions
ACHIEVEMENTS = [
    # --- Primeros pasos ---
    {"id": "first_workout", "name": "Primera Sesión", "desc": "Completa tu primer entrenamiento", "icon": "trophy", "color": "#00FF88"},
    {"id": "first_routine", "name": "Constructor", "desc": "Crea tu primera rutina", "icon": "construct", "color": "#00A3FF"},
    {"id": "first_cardio", "name": "Calienta motores", "desc": "Registra tu primera sesión de cardio", "icon": "heart", "color": "#FF3B30"},
    {"id": "first_pr", "name": "Récord Personal", "desc": "Registra tu primer PR", "icon": "barbell", "color": "#00A3FF"},
    {"id": "first_weight", "name": "Métrica", "desc": "Registra tu primer peso corporal", "icon": "body", "color": "#8B5CF6"},
    {"id": "first_public", "name": "Compartir es vivir", "desc": "Publica tu primera rutina", "icon": "globe", "color": "#8B5CF6"},
    {"id": "first_ai", "name": "Habla con la IA", "desc": "Haz tu primera consulta al Coach IA", "icon": "sparkles", "color": "#8B5CF6"},

    # --- Rachas (días seguidos entrenando) ---
    {"id": "streak_3", "name": "En marcha", "desc": "Mantén una racha de 3 días", "icon": "flame", "color": "#FFB800"},
    {"id": "streak_7", "name": "Semana completa", "desc": "Mantén una racha de 7 días", "icon": "flame", "color": "#FF8800"},
    {"id": "streak_30", "name": "Imparable", "desc": "Mantén una racha de 30 días", "icon": "flame", "color": "#FF3B30"},

    # --- Volumen de entrenamientos ---
    {"id": "ten_workouts", "name": "Constancia", "desc": "Completa 10 entrenamientos", "icon": "medal", "color": "#FFB800"},
    {"id": "workouts_25", "name": "Con fuerza", "desc": "Completa 25 entrenamientos", "icon": "trophy", "color": "#FF8800"},
    {"id": "fifty_workouts", "name": "Guerrero", "desc": "Completa 50 entrenamientos", "icon": "rocket", "color": "#FF3B30"},
    {"id": "workouts_100", "name": "Centenario", "desc": "Completa 100 entrenamientos", "icon": "ribbon", "color": "#8B5CF6"},

    # --- Volumen total (kg levantados) ---
    {"id": "vol_1000", "name": "Mil kilos", "desc": "Acumula 1.000 kg de volumen total", "icon": "fitness", "color": "#00FF88"},
    {"id": "vol_10000", "name": "Diez mil kilos", "desc": "Acumula 10.000 kg de volumen total", "icon": "trophy", "color": "#FFB800"},
    {"id": "vol_50000", "name": "Coloso", "desc": "Acumula 50.000 kg de volumen total", "icon": "trophy", "color": "#FF8800"},
    {"id": "vol_100000", "name": "Titán", "desc": "Acumula 100.000 kg de volumen total", "icon": "trophy", "color": "#FF3B30"},

    # --- Cardio ---
    {"id": "ten_cardio", "name": "Maratoniano", "desc": "Completa 10 sesiones de cardio", "icon": "walk", "color": "#FFB800"},
    {"id": "cardio_25km", "name": "Explorador", "desc": "Acumula 25 km de cardio", "icon": "trail-sign", "color": "#00A3FF"},
    {"id": "cardio_100km", "name": "Ultradistancia", "desc": "Acumula 100 km de cardio", "icon": "bicycle", "color": "#8B5CF6"},

    # --- Rutinas ---
    {"id": "routines_5", "name": "Planificador", "desc": "Crea 5 rutinas diferentes", "icon": "list", "color": "#00A3FF"},
    {"id": "routines_10", "name": "Arquitecto", "desc": "Crea 10 rutinas diferentes", "icon": "library", "color": "#8B5CF6"},

    # --- Seguimiento de peso ---
    {"id": "weight_10", "name": "Dedicado", "desc": "Registra 10 pesajes corporales", "icon": "analytics", "color": "#00FF88"},

    # --- Social ---
    {"id": "follow_3", "name": "Socialité", "desc": "Sigue a 3 usuarios", "icon": "people", "color": "#00A3FF"},

    # --- Diversidad de ejercicios ---
    {"id": "exercises_20", "name": "Variedad", "desc": "Entrena 20 ejercicios distintos", "icon": "shuffle", "color": "#FFB800"},
    {"id": "exercises_50", "name": "Todoterreno", "desc": "Entrena 50 ejercicios distintos", "icon": "apps", "color": "#FF8800"},

    # --- Estilos de entrenamiento ---
    {"id": "cardio_and_gym", "name": "Completo", "desc": "Haz gym y cardio en la misma semana", "icon": "sparkles", "color": "#00FF88"},

    # --- Premium ---
    {"id": "premium", "name": "PRO", "desc": "Hazte Premium", "icon": "star", "color": "#8B5CF6"},
]

async def check_achievements(user_id: str) -> List[str]:
    """Recompute achievements for a user. Returns list of newly unlocked ones."""
    user = await db.users.find_one({"id": user_id})
    if not user:
        return []
    existing = set(user.get("achievements", []))
    workouts = await db.workouts.count_documents({"user_id": user_id})
    routines = await db.routines.count_documents({"user_id": user_id})
    public_routines = await db.routines.count_documents({"user_id": user_id, "is_public": True})
    cardio = await db.cardio.count_documents({"user_id": user_id})
    body = await db.body_entries.count_documents({"user_id": user_id})
    ai = await db.ai_messages.count_documents({"user_id": user_id})
    workout_docs = await db.workouts.find({"user_id": user_id}, {"total_volume": 1, "date": 1, "exercises": 1}).to_list(5000)
    total_vol = sum(w.get("total_volume", 0) for w in workout_docs)
    has_pr = workouts > 0  # any workout with weight > 0 implicitly

    # Unique exercises trained
    unique_exercises = set()
    for w in workout_docs:
        for ex in w.get("exercises", []) or []:
            name = (ex.get("name") or "").strip().lower()
            if name:
                unique_exercises.add(name)

    # Cardio total distance in km
    cardio_docs = await db.cardio.find({"user_id": user_id}, {"distance_km": 1, "date": 1}).to_list(5000)
    cardio_total_km = sum(c.get("distance_km", 0) or 0 for c in cardio_docs)

    # Streak (current)
    streak_current = 0
    try:
        dates = {w.get("date", "")[:10] for w in workout_docs if w.get("date")}
        dates.update({c.get("date", "")[:10] for c in cardio_docs if c.get("date")})
        dates = {d for d in dates if d}
        if dates:
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            for i in range(0, 400):
                d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
                if d in dates:
                    streak_current += 1
                else:
                    if i == 0:
                        # allow today to not count if no workout yet
                        continue
                    break
    except Exception:
        pass

    # Followers/follows
    follows_count = 0
    try:
        follows_count = await db.follows.count_documents({"follower_id": user_id})
    except Exception:
        pass

    # Cardio + gym in same week (ISO week)
    cardio_and_gym_same_week = False
    try:
        from datetime import datetime
        weeks_gym = set()
        weeks_cardio = set()
        for w in workout_docs:
            d = w.get("date", "")
            try:
                dt = datetime.fromisoformat(d[:19])
                weeks_gym.add((dt.isocalendar().year, dt.isocalendar().week))
            except Exception:
                pass
        for c in cardio_docs:
            d = c.get("date", "")
            try:
                dt = datetime.fromisoformat(d[:19])
                weeks_cardio.add((dt.isocalendar().year, dt.isocalendar().week))
            except Exception:
                pass
        if weeks_gym & weeks_cardio:
            cardio_and_gym_same_week = True
    except Exception:
        pass

    rules = {
        # first steps
        "first_workout": workouts >= 1,
        "first_routine": routines >= 1,
        "first_cardio": cardio >= 1,
        "first_pr": has_pr,
        "first_weight": body >= 1,
        "first_public": public_routines >= 1,
        "first_ai": ai >= 1,
        # streaks
        "streak_3": streak_current >= 3,
        "streak_7": streak_current >= 7,
        "streak_30": streak_current >= 30,
        # workouts volume
        "ten_workouts": workouts >= 10,
        "workouts_25": workouts >= 25,
        "fifty_workouts": workouts >= 50,
        "workouts_100": workouts >= 100,
        # total kg volume
        "vol_1000": total_vol >= 1000,
        "vol_10000": total_vol >= 10000,
        "vol_50000": total_vol >= 50000,
        "vol_100000": total_vol >= 100000,
        # cardio
        "ten_cardio": cardio >= 10,
        "cardio_25km": cardio_total_km >= 25,
        "cardio_100km": cardio_total_km >= 100,
        # routines
        "routines_5": routines >= 5,
        "routines_10": routines >= 10,
        # weight
        "weight_10": body >= 10,
        # social
        "follow_3": follows_count >= 3,
        # variety
        "exercises_20": len(unique_exercises) >= 20,
        "exercises_50": len(unique_exercises) >= 50,
        # style mix
        "cardio_and_gym": cardio_and_gym_same_week,
        # premium
        "premium": user.get("is_premium", False),
    }
    unlocked_now = [k for k, v in rules.items() if v]
    # Achievements are permanent: union with existing instead of overwriting.
    # This also honours admin-granted achievements and tests that seed the array.
    union = set(unlocked_now) | existing
    new = [k for k in unlocked_now if k not in existing]
    if union != existing:
        await db.users.update_one({"id": user_id}, {"$set": {"achievements": sorted(union)}})
    # ── XP por cada logro NUEVO ──
    if new:
        try:
            await add_xp(user_id, XP_REWARDS["achievement"] * len(new), f"achievements:{','.join(new)}")
        except Exception:
            pass
    return new


def _has_all_achievements(user: dict) -> bool:
    """Returns True if user has unlocked ALL achievements (grants AI access)."""
    try:
        u_ach = set(user.get("achievements", []))
        total_ids = {a["id"] for a in ACHIEVEMENTS}
        return u_ach >= total_ids
    except Exception:
        return False


@api.get("/achievements")
async def get_achievements(user=Depends(get_current_user)):
    await check_achievements(user["id"])
    fresh = await db.users.find_one({"id": user["id"]}, {"_id": 0, "achievements": 1, "is_premium": 1})
    unlocked = set((fresh or {}).get("achievements", []))
    items = [{**a, "unlocked": a["id"] in unlocked} for a in ACHIEVEMENTS]
    all_unlocked = len(unlocked) >= len(ACHIEVEMENTS)
    ai_grant_used = await _achievement_ai_used(user["id"])
    ai_grant_remaining = max(0, ACHIEVEMENT_AI_MAX_MESSAGES - ai_grant_used)
    return {
        "items": items,
        "unlocked_count": len(unlocked),
        "total": len(ACHIEVEMENTS),
        "all_unlocked": all_unlocked,
        "ai_unlocked_by_achievements": all_unlocked,
        "ai_grant_max": ACHIEVEMENT_AI_MAX_MESSAGES,
        "ai_grant_used": ai_grant_used,
        "ai_grant_remaining": ai_grant_remaining,
    }

# ---------------- Weekly Schedule ----------------
class ScheduleIn(BaseModel):
    day: int = Field(ge=0, le=6)  # 0=Mon, 6=Sun
    routine_id: str

@api.get("/schedule")
async def get_schedule(user=Depends(get_current_user)):
    items = await db.schedule.find({"user_id": user["id"]}, {"_id": 0}).to_list(20)
    by_day = {it["day"]: it for it in items}
    out = []
    for d in range(7):
        s = by_day.get(d)
        if s and s.get("routine_id"):
            r = await db.routines.find_one({"id": s["routine_id"]}, {"_id": 0, "name": 1, "id": 1, "exercises": 1})
            if r:
                out.append({"day": d, "routine_id": r["id"], "routine_name": r["name"], "exercise_count": len(r.get("exercises", []))})
            else:
                out.append({"day": d, "routine_id": None, "routine_name": None, "exercise_count": 0})
        else:
            out.append({"day": d, "routine_id": None, "routine_name": None, "exercise_count": 0})
    return out

@api.post("/schedule")
async def set_schedule(body: ScheduleIn, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": body.routine_id})
    if not r or (r.get("user_id") not in (user["id"], None) and not r.get("is_predefined")):
        raise HTTPException(404, "Rutina no encontrada")
    await db.schedule.update_one(
        {"user_id": user["id"], "day": body.day},
        {"$set": {"user_id": user["id"], "day": body.day, "routine_id": body.routine_id}},
        upsert=True,
    )
    return {"ok": True}

@api.delete("/schedule/{day}")
async def clear_schedule(day: int, user=Depends(get_current_user)):
    await db.schedule.delete_one({"user_id": user["id"], "day": day})
    return {"ok": True}

# ---------------- Health endpoint ----------------
@api.get("/")
async def root():
    return {"message": "Fitness API OK"}

# ---------------- Seed predefined routines ----------------
async def seed_predefined():
    existing = await db.routines.count_documents({"is_predefined": True})
    if existing > 0:
        return
    pred = [
        {
            "id": str(uuid.uuid4()),
            "user_id": None,
            "name": "Full Body Principiante",
            "description": "Rutina completa básica para empezar",
            "is_predefined": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "exercises": [
                {"id": str(uuid.uuid4()), "name": "Sentadilla", "sets": 3, "reps": 12, "weight": 0, "rest_seconds": 90, "muscle_group": "Piernas", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd", "notes": ""},
                {"id": str(uuid.uuid4()), "name": "Press de Banca", "sets": 3, "reps": 10, "weight": 20, "rest_seconds": 120, "muscle_group": "Pecho", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg", "notes": ""},
                {"id": str(uuid.uuid4()), "name": "Remo con Barra", "sets": 3, "reps": 10, "weight": 20, "rest_seconds": 90, "muscle_group": "Espalda", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd", "notes": ""},
                {"id": str(uuid.uuid4()), "name": "Abdominales", "sets": 3, "reps": 15, "weight": 0, "rest_seconds": 60, "muscle_group": "Core", "image_url": "https://images.unsplash.com/photo-1672344048213-76b6e77304bd", "notes": ""},
            ],
        },
        {
            "id": str(uuid.uuid4()),
            "user_id": None,
            "name": "Empuje (Push)",
            "description": "Pecho, hombros y tríceps",
            "is_predefined": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "exercises": [
                {"id": str(uuid.uuid4()), "name": "Press de Banca", "sets": 4, "reps": 8, "weight": 30, "rest_seconds": 120, "muscle_group": "Pecho", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg", "notes": ""},
                {"id": str(uuid.uuid4()), "name": "Press Militar", "sets": 3, "reps": 10, "weight": 15, "rest_seconds": 90, "muscle_group": "Hombros", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg", "notes": ""},
                {"id": str(uuid.uuid4()), "name": "Fondos", "sets": 3, "reps": 10, "weight": 0, "rest_seconds": 90, "muscle_group": "Tríceps", "image_url": "https://images.pexels.com/photos/11433060/pexels-photo-11433060.jpeg", "notes": ""},
            ],
        },
    ]
    await db.routines.insert_many(pred)

async def seed_admin():
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@fitness.com").lower()
    admin_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": admin_email,
            "name": "Admin",
            "password_hash": hash_password(admin_pw),
            "is_premium": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    elif not verify_password(admin_pw, existing["password_hash"]):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": hash_password(admin_pw)}})

# Mount

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- Admin Endpoints ----------------

class AdminExerciseIn(BaseModel):
    name: str
    muscle_group: str = "Otro"
    category: str = "gym"
    equipment: str = "—"
    instructions: List[str] = []
    tips: List[str] = []
    image_url: Optional[str] = ""
    youtube_id: Optional[str] = ""

@api.get("/admin/exercises")
async def admin_list_exercises(user=Depends(require_admin)):
    custom = await db.custom_exercises.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return custom

@api.post("/admin/exercises")
async def admin_add_exercise(data: AdminExerciseIn, user=Depends(require_admin)):
    eid = str(uuid.uuid4())[:8]
    doc = {
        "id": f"custom-{eid}",
        "name": data.name,
        "muscle_group": data.muscle_group,
        "category": data.category,
        "equipment": data.equipment,
        "instructions": data.instructions,
        "tips": data.tips,
        "image_url": data.image_url or "",
        "youtube_id": data.youtube_id or "",
        "is_custom": True,
        "created_at": datetime.utcnow().isoformat(),
    }
    await db.custom_exercises.insert_one(dict(doc))
    _cache_invalidate_prefix("lib:")
    return doc

@api.put("/admin/exercises/{eid}")
async def admin_update_exercise(eid: str, data: AdminExerciseIn, user=Depends(require_admin)):
    update = data.model_dump()
    res = await db.custom_exercises.update_one({"id": eid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "No encontrado")
    _cache_invalidate_prefix("lib:")
    return await db.custom_exercises.find_one({"id": eid}, {"_id": 0})

@api.delete("/admin/exercises/{eid}")
async def admin_delete_exercise(eid: str, user=Depends(require_admin)):
    res = await db.custom_exercises.delete_one({"id": eid})
    _cache_invalidate_prefix("lib:")
    return {"ok": res.deleted_count > 0}

@api.get("/admin/routines")
async def admin_list_routines(user=Depends(require_admin)):
    items = await db.routines.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items

class AdminPremiumPatch(BaseModel):
    is_premium_routine: bool
    price_eur: float = 3.99
    cover_image_url: Optional[str] = None
    is_predefined: Optional[bool] = None
    level: Optional[str] = None  # "principiante" | "intermedio" | "avanzado"
    duration_weeks: Optional[int] = None
    benefits: Optional[List[str]] = None
    goal: Optional[str] = None

@api.put("/admin/routines/{rid}/premium")
async def admin_toggle_premium(rid: str, data: AdminPremiumPatch, user=Depends(require_admin)):
    # No limit on premium routines — admin can create as many as desired (revenue model).
    update = {"is_premium_routine": data.is_premium_routine, "price_eur": data.price_eur}
    if data.cover_image_url is not None:
        update["cover_image_url"] = data.cover_image_url
    if data.is_predefined is not None:
        update["is_predefined"] = data.is_predefined
        if data.is_predefined:
            update["user_id"] = None
    if data.level is not None: update["level"] = data.level
    if data.duration_weeks is not None: update["duration_weeks"] = data.duration_weeks
    if data.benefits is not None: update["benefits"] = data.benefits
    if data.goal is not None: update["goal"] = data.goal
    res = await db.routines.update_one({"id": rid}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(404, "Rutina no encontrada")
    return await db.routines.find_one({"id": rid}, {"_id": 0})

# ---------------- Routine ratings (P1: social proof) ----------------
class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = ""

@api.post("/routines/{rid}/rate")
async def rate_routine(rid: str, body: RatingIn, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid})
    if not r:
        raise HTTPException(404, "Rutina no encontrada")
    # Only buyers / owners / public-saved users can rate
    is_buyer = rid in (user.get("purchased_routines") or [])
    is_owner = r.get("user_id") == user["id"]
    if r.get("is_premium_routine") and not is_buyer and not is_owner and not _is_admin(user):
        raise HTTPException(403, "Solo quienes han comprado pueden valorar")
    await db.routine_ratings.update_one(
        {"routine_id": rid, "user_id": user["id"]},
        {"$set": {"rating": body.rating, "comment": body.comment or "",
                  "user_name": user.get("name"), "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    # Recalc avg
    cursor = db.routine_ratings.find({"routine_id": rid})
    total, n = 0, 0
    async for doc in cursor:
        total += doc["rating"]; n += 1
    avg = round(total / n, 2) if n else 0.0
    await db.routines.update_one({"id": rid}, {"$set": {"rating_avg": avg, "rating_count": n}})
    return {"rating_avg": avg, "rating_count": n}

@api.get("/routines/{rid}/ratings")
async def list_ratings(rid: str, user=Depends(get_current_user)):
    items = await db.routine_ratings.find({"routine_id": rid}, {"_id": 0}).sort("updated_at", -1).to_list(50)
    return items

# ---------------- Premium routine PREVIEW (P1: free first exercise → conversion boost) ----------------
@api.get("/routines/{rid}/preview")
async def preview_routine(rid: str, user=Depends(get_current_user)):
    r = await db.routines.find_one({"id": rid}, {"_id": 0})
    if not r:
        raise HTTPException(404, "Rutina no encontrada")
    if not r.get("is_premium_routine"):
        return r
    # Strip exercises to first 1 only, mark rest as locked
    full = r.get("exercises", [])
    preview_exercises = full[:1]
    return {
        **r,
        "exercises": preview_exercises,
        "preview_mode": True,
        "total_exercises": len(full),
        "locked_count": max(0, len(full) - 1),
    }

# ---------------- Onboarding (P0: first-time user wizard) ----------------
class OnboardingIn(BaseModel):
    goal: Optional[str] = None  # "perder-grasa" | "ganar-musculo" | "tonificar" | "resistencia"
    level: Optional[str] = None  # "principiante" | "intermedio" | "avanzado"
    days_per_week: Optional[int] = None
    equipment: Optional[List[str]] = None  # ["gym", "casa", "aire-libre"]

@api.post("/auth/onboarding")
async def save_onboarding(body: OnboardingIn, user=Depends(get_current_user)):
    update = {"onboarding_done": True, "onboarding_at": datetime.now(timezone.utc).isoformat()}
    if body.goal: update["fitness_goal"] = body.goal
    if body.level: update["fitness_level"] = body.level
    if body.days_per_week: update["days_per_week"] = body.days_per_week
    if body.equipment is not None: update["equipment"] = body.equipment
    await db.users.update_one({"id": user["id"]}, {"$set": update})
    return {"ok": True}

# ---------------- Avatar (P0: foto de perfil) ----------------
class AvatarIn(BaseModel):
    avatar_url: str  # base64 data URI o URL http

@api.put("/auth/avatar")
async def update_avatar(body: AvatarIn, user=Depends(get_current_user)):
    if not body.avatar_url:
        await db.users.update_one({"id": user["id"]}, {"$unset": {"avatar_url": ""}})
        return {"ok": True, "avatar_url": None}
    # Limit base64 size to ~500KB to avoid huge docs
    if len(body.avatar_url) > 700_000:
        raise HTTPException(400, "Imagen demasiado grande. Usa una más pequeña.")
    await db.users.update_one({"id": user["id"]}, {"$set": {"avatar_url": body.avatar_url}})
    return {"ok": True, "avatar_url": body.avatar_url}

# ---------------- Achievements / Badges (P1: gamification) ----------------
@api.get("/stats/achievements")
async def get_stats_achievements(user=Depends(get_current_user)):
    workouts = await db.workouts.count_documents({"user_id": user["id"]})
    cardio = await db.cardio.count_documents({"user_id": user["id"]})
    routines_owned = await db.routines.count_documents({"user_id": user["id"]})
    streak = await get_streak(user)
    purchased = len(user.get("purchased_routines") or [])
    total_volume = 0
    cursor = db.workouts.find({"user_id": user["id"]}, {"total_volume": 1, "logs": 1})
    async for w in cursor:
        if w.get("total_volume"):
            total_volume += w["total_volume"]
        else:
            for l in (w.get("logs") or []):
                total_volume += (l.get("weight") or 0) * (l.get("reps") or 0) * (l.get("sets_completed") or 0)
    achievements = [
        {"id": "first-workout", "icon": "🥇", "title": "Primera vez", "desc": "Completa tu primera sesión", "unlocked": workouts >= 1, "progress": min(workouts, 1), "goal": 1},
        {"id": "10-workouts", "icon": "💪", "title": "Diez al saco", "desc": "10 entrenamientos", "unlocked": workouts >= 10, "progress": min(workouts, 10), "goal": 10},
        {"id": "50-workouts", "icon": "🔥", "title": "Imparable", "desc": "50 entrenamientos", "unlocked": workouts >= 50, "progress": min(workouts, 50), "goal": 50},
        {"id": "100-workouts", "icon": "🏆", "title": "Centenario", "desc": "100 entrenamientos", "unlocked": workouts >= 100, "progress": min(workouts, 100), "goal": 100},
        {"id": "first-routine", "icon": "📋", "title": "Arquitecto", "desc": "Crea tu primera rutina", "unlocked": routines_owned >= 1, "progress": min(routines_owned, 1), "goal": 1},
        {"id": "first-cardio", "icon": "🏃", "title": "En marcha", "desc": "Primera sesión de cardio", "unlocked": cardio >= 1, "progress": min(cardio, 1), "goal": 1},
        {"id": "streak-3", "icon": "🔥", "title": "Tres días", "desc": "3 días seguidos", "unlocked": streak["best_streak"] >= 3, "progress": min(streak["best_streak"], 3), "goal": 3},
        {"id": "streak-7", "icon": "📅", "title": "Una semana", "desc": "7 días seguidos", "unlocked": streak["best_streak"] >= 7, "progress": min(streak["best_streak"], 7), "goal": 7},
        {"id": "streak-30", "icon": "🚀", "title": "Mes de hierro", "desc": "30 días seguidos", "unlocked": streak["best_streak"] >= 30, "progress": min(streak["best_streak"], 30), "goal": 30},
        {"id": "first-purchase", "icon": "💎", "title": "Premium", "desc": "Compra tu primera rutina premium", "unlocked": purchased >= 1, "progress": min(purchased, 1), "goal": 1},
        {"id": "volume-10k", "icon": "🏋️", "title": "10 toneladas", "desc": "10.000kg movidos", "unlocked": total_volume >= 10000, "progress": min(int(total_volume), 10000), "goal": 10000},
        {"id": "volume-100k", "icon": "⚡", "title": "100 toneladas", "desc": "100.000kg movidos", "unlocked": total_volume >= 100000, "progress": min(int(total_volume), 100000), "goal": 100000},
    ]
    unlocked_count = sum(1 for a in achievements if a["unlocked"])
    return {"items": achievements, "unlocked": unlocked_count, "total": len(achievements), "total_volume_kg": int(total_volume)}

# ---------------- Streak (P0: gamified retention) ----------------
@api.get("/stats/streak")
async def get_streak(user=Depends(get_current_user)):
    """Calculate current daily workout streak (consecutive days with at least 1 workout/cardio)."""
    sessions = await db.workouts.find({"user_id": user["id"]}, {"created_at": 1, "_id": 0}).to_list(1000)
    cardio = await db.cardio.find({"user_id": user["id"]}, {"created_at": 1, "_id": 0}).to_list(1000)
    all_dates = set()
    for s in sessions + cardio:
        try:
            all_dates.add(s["created_at"][:10])  # YYYY-MM-DD
        except Exception:
            pass
    if not all_dates:
        return {"current_streak": 0, "best_streak": 0, "total_days": 0, "trained_today": False}
    from datetime import date, timedelta
    today = date.today()
    yesterday = today - timedelta(days=1)
    sorted_dates = sorted(all_dates, reverse=True)
    # Current streak: consecutive days ending today or yesterday
    current = 0
    cursor_date = today if today.isoformat() in all_dates else (yesterday if yesterday.isoformat() in all_dates else None)
    while cursor_date and cursor_date.isoformat() in all_dates:
        current += 1
        cursor_date = cursor_date - timedelta(days=1)
    # Best streak ever
    sorted_asc = sorted(all_dates)
    best = 1
    run = 1
    for i in range(1, len(sorted_asc)):
        prev = date.fromisoformat(sorted_asc[i-1])
        cur = date.fromisoformat(sorted_asc[i])
        if (cur - prev).days == 1:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return {
        "current_streak": current,
        "best_streak": best,
        "total_days": len(all_dates),
        "trained_today": today.isoformat() in all_dates,
    }


@api.delete("/admin/routines/{rid}")
async def admin_delete_routine(rid: str, user=Depends(require_admin)):
    res = await db.routines.delete_one({"id": rid})
    return {"ok": res.deleted_count > 0}


# ==================== ADMIN USER MANAGEMENT ====================

class AdminUserPatch(BaseModel):
    is_premium: Optional[bool] = None
    is_admin: Optional[bool] = None
    is_banned: Optional[bool] = None


@api.get("/admin/users")
async def admin_list_users(
    user=Depends(require_admin),
    q: Optional[str] = None,
    filter: Optional[str] = None,
    limit: int = 50,
    skip: int = 0,
):
    """List users with optional search and filter.
    q: search by email or name (case-insensitive)
    filter: 'premium' | 'admin' | 'banned' | 'free'
    """
    query: dict = {}
    if q:
        query["$or"] = [
            {"email": {"$regex": q, "$options": "i"}},
            {"name": {"$regex": q, "$options": "i"}},
        ]
    if filter == "premium":
        query["is_premium"] = True
    elif filter == "admin":
        query["is_admin"] = True
    elif filter == "banned":
        query["is_banned"] = True
    elif filter == "free":
        query["is_premium"] = {"$ne": True}

    total = await db.users.count_documents(query)
    cursor = db.users.find(query, {
        "_id": 0,
        "id": 1, "email": 1, "name": 1,
        "is_premium": 1, "is_admin": 1, "is_banned": 1,
        "created_at": 1, "stripe_subscription_id": 1,
        "subscription_status": 1, "avatar_url": 1,
    }).sort("created_at", -1).skip(skip).limit(min(limit, 200))
    users = await cursor.to_list(limit)
    return {"total": total, "skip": skip, "limit": limit, "users": users}


@api.get("/admin/users/{uid}")
async def admin_user_detail(uid: str, user=Depends(require_admin)):
    u = await db.users.find_one({"id": uid}, {"_id": 0, "password_hash": 0})
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    # aggregate stats
    workouts = await db.workouts.count_documents({"user_id": uid})
    routines = await db.routines.count_documents({"user_id": uid})
    cardio = await db.cardio.count_documents({"user_id": uid})
    purchased = u.get("purchased_routines", []) or []
    u["_stats"] = {
        "workouts": workouts,
        "routines": routines,
        "cardio_sessions": cardio,
        "purchased_routines": len(purchased),
    }
    return u


@api.patch("/admin/users/{uid}")
async def admin_patch_user(uid: str, body: AdminUserPatch, user=Depends(require_admin)):
    """Toggle is_premium, is_admin, is_banned flags."""
    if uid == user["id"]:
        raise HTTPException(400, "No puedes modificarte a ti mismo.")
    updates: dict = {}
    if body.is_premium is not None:
        updates["is_premium"] = bool(body.is_premium)
    if body.is_admin is not None:
        updates["is_admin"] = bool(body.is_admin)
    if body.is_banned is not None:
        updates["is_banned"] = bool(body.is_banned)
    if not updates:
        raise HTTPException(400, "Nada para actualizar.")
    res = await db.users.update_one({"id": uid}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(404, "Usuario no encontrado")
    return {"ok": True, "updated": updates}


@api.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, user=Depends(require_admin)):
    """Delete user and ABSOLUTELY ALL their data across the whole system.

    This is a nuclear delete: in addition to obvious user-owned collections we also:
      - Cancel any active Stripe subscription and delete the Stripe customer.
      - Remove routine_ratings that belong to routines this user created (orphan cleanup).
      - Remove the user's routine IDs from other users' purchased_routines arrays.
      - Pull the user id from every other user's followers/following arrays.
      - Wipe any lingering docs that reference the user via secondary fields (follower_id, target_user_id, etc.)
    """
    if uid == user["id"]:
        raise HTTPException(400, "No puedes eliminarte a ti mismo.")
    target = await db.users.find_one({"id": uid})
    if not target:
        raise HTTPException(404, "Usuario no encontrado")
    if target.get("is_admin"):
        raise HTTPException(400, "No puedes eliminar a otro administrador. Quítale el rol primero.")

    deleted: dict = {}

    # 1) Cancel Stripe subscription + delete customer (so Stripe dashboard is clean too)
    stripe_result = {"subscription_canceled": False, "customer_deleted": False, "error": None}
    if STRIPE_API_KEY:
        try:
            sub_id = target.get("stripe_subscription_id")
            if sub_id:
                try:
                    stripe_sdk.Subscription.cancel(sub_id)
                    stripe_result["subscription_canceled"] = True
                except Exception as se:
                    stripe_result["error"] = f"sub: {se}"
            cust_id = target.get("stripe_customer_id")
            if cust_id:
                try:
                    stripe_sdk.Customer.delete(cust_id)
                    stripe_result["customer_deleted"] = True
                except Exception as ce:
                    stripe_result["error"] = (stripe_result.get("error") or "") + f" cust: {ce}"
        except Exception as e:
            stripe_result["error"] = str(e)
    deleted["_stripe"] = stripe_result

    # 2) Collect routine IDs owned by this user BEFORE deleting them
    owned_routine_ids = [r["id"] async for r in db.routines.find({"user_id": uid}, {"id": 1, "_id": 0})]

    # 3) Orphan cleanup: ratings that OTHER users left on routines this user created
    if owned_routine_ids:
        try:
            r_orphan = await db.routine_ratings.delete_many({"routine_id": {"$in": owned_routine_ids}})
            deleted["routine_ratings_orphans"] = r_orphan.deleted_count
        except Exception:
            deleted["routine_ratings_orphans"] = 0

    # 4) Remove those routine IDs from every user's purchased_routines
    if owned_routine_ids:
        try:
            pr = await db.users.update_many(
                {"purchased_routines": {"$in": owned_routine_ids}},
                {"$pull": {"purchased_routines": {"$in": owned_routine_ids}}},
            )
            deleted["purchased_routines_cleaned_from"] = pr.modified_count
        except Exception:
            deleted["purchased_routines_cleaned_from"] = 0

    # 5) Cascade delete user-owned data
    for coll in [
        "routines", "workouts", "cardio", "body_entries", "ai_messages",
        "password_resets", "schedule", "routine_ratings", "custom_exercises",
        "payment_transactions",
    ]:
        try:
            r = await getattr(db, coll).delete_many({"user_id": uid})
            deleted[coll] = r.deleted_count
        except Exception:
            deleted[coll] = 0

    # 6) Follows collection (legacy / if used) — try several field shapes
    follows_deleted = 0
    for field in ["user_id", "follower_id", "following_id", "target_user_id", "followee_id"]:
        try:
            r = await db.follows.delete_many({field: uid})
            follows_deleted += r.deleted_count
        except Exception:
            pass
    deleted["follows"] = follows_deleted

    # 7) Pull user id from every other user's following / followers arrays
    try:
        r1 = await db.users.update_many({"following": uid}, {"$pull": {"following": uid}})
        r2 = await db.users.update_many({"followers": uid}, {"$pull": {"followers": uid}})
        deleted["unlinked_from_following_arrays"] = r1.modified_count
        deleted["unlinked_from_followers_arrays"] = r2.modified_count
    except Exception:
        deleted["unlinked_from_following_arrays"] = 0
        deleted["unlinked_from_followers_arrays"] = 0

    # 8) Finally delete the user record itself
    res = await db.users.delete_one({"id": uid})
    deleted["user_record"] = res.deleted_count

    # Log for audit (visible in backend logs)
    logging.warning(f"[ADMIN DELETE] admin={user['email']} removed user={target.get('email')} (id={uid}) report={deleted}")

    return {"ok": True, "email": target.get("email"), "deleted": deleted}


@api.get("/admin/stats")
async def admin_stats(user=Depends(require_admin)):
    """Global stats for admin dashboard."""
    total_users = await db.users.count_documents({})
    premium_users = await db.users.count_documents({"is_premium": True})
    banned_users = await db.users.count_documents({"is_banned": True})
    total_routines = await db.routines.count_documents({})
    premium_routines = await db.routines.count_documents({"is_premium_routine": True})
    total_workouts = await db.workouts.count_documents({})
    # Paid transactions
    total_paid = await db.payment_transactions.count_documents({"payment_status": "paid"})
    pipeline = [
        {"$match": {"payment_status": "paid"}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    revenue_agg = await db.payment_transactions.aggregate(pipeline).to_list(1)
    revenue = revenue_agg[0]["total"] if revenue_agg else 0
    return {
        "users": {
            "total": total_users,
            "premium": premium_users,
            "banned": banned_users,
            "free": total_users - premium_users,
        },
        "content": {
            "routines": total_routines,
            "premium_routines": premium_routines,
            "workouts_completed": total_workouts,
        },
        "payments": {
            "successful_transactions": total_paid,
            "total_revenue_eur": round(revenue, 2),
        },
    }


# Temporary endpoint to download the static web build (so the user can drag-drop into Netlify)
# Safe to keep: it is publicly listed but only serves a static zip with already-public frontend code.
@api.get("/download/web-build")
async def download_web_build():
    from fastapi.responses import FileResponse
    zip_path = "/tmp/kinetix-web.zip"
    if not os.path.exists(zip_path):
        raise HTTPException(404, "Build no disponible. Pide al agente que regenere el build.")
    return FileResponse(zip_path, media_type="application/zip", filename="kinetix-web.zip")


# ============================================================
# ────────── XP & LEVELS API ──────────
# ============================================================
@api.get("/me/xp")
async def get_my_xp(user=Depends(get_current_user)):
    """Returns current XP, level, progress to next level, and recent history."""
    u = await db.users.find_one({"id": user["id"]}, {"_id": 0, "xp": 1, "level": 1, "xp_history": 1})
    xp = int((u or {}).get("xp", 0) or 0)
    level = xp_to_level(xp)
    current_level_xp = xp_for_level(level)
    next_level_xp = xp_for_level(level + 1)
    xp_in_level = xp - current_level_xp
    xp_needed_for_next = next_level_xp - current_level_xp
    progress_pct = (xp_in_level / xp_needed_for_next * 100) if xp_needed_for_next > 0 else 100
    history = list(reversed((u or {}).get("xp_history", []) or []))[:30]
    return {
        "xp": xp,
        "level": level,
        "current_level_xp": current_level_xp,
        "next_level_xp": next_level_xp,
        "xp_in_level": xp_in_level,
        "xp_to_next_level": max(0, next_level_xp - xp),
        "progress_pct": round(progress_pct, 1),
        "rewards": XP_REWARDS,
        "recent_history": history,
    }


@api.get("/leaderboard/xp")
async def leaderboard_xp(user=Depends(get_current_user), limit: int = 100):
    """Top users globally by XP."""
    limit = max(1, min(200, limit))
    cursor = db.users.find(
        {"is_banned": {"$ne": True}, "xp": {"$gt": 0}},
        {"_id": 0, "id": 1, "name": 1, "avatar_url": 1, "xp": 1, "level": 1, "is_premium": 1}
    ).sort("xp", -1).limit(limit)
    items = await cursor.to_list(limit)
    # Find my position even if I'm beyond the limit
    me = await db.users.find_one({"id": user["id"]}, {"_id": 0, "xp": 1, "level": 1, "name": 1, "avatar_url": 1})
    my_xp = int((me or {}).get("xp", 0) or 0)
    my_rank = None
    for idx, it in enumerate(items, start=1):
        if it.get("id") == user["id"]:
            my_rank = idx
            break
    if my_rank is None and my_xp > 0:
        # Count how many users have MORE xp than me
        higher = await db.users.count_documents({"xp": {"$gt": my_xp}, "is_banned": {"$ne": True}})
        my_rank = higher + 1
    return {
        "items": [{**it, "rank": idx} for idx, it in enumerate(items, start=1)],
        "my_rank": my_rank,
        "my_xp": my_xp,
        "my_level": xp_to_level(my_xp),
        "total": len(items),
    }


# ---- Admin: seed premium routines (idempotente) ----
@api.post("/admin/seed-premium-routines")
async def admin_seed_premium_routines(user=Depends(get_current_user)):
    """Crea las 3 rutinas premium de ejemplo si no existen. Solo admin."""
    if not _is_admin(user):
        raise HTTPException(403, "Solo admin")

    PREMIUM_ROUTINES = [
        {
            "name": "💪 Full Body Hipertrofia 3x/sem",
            "description": "Plan completo 3 días por semana para ganar músculo. Programación de expertos con ejercicios compuestos y aislados.",
            "price_eur": 3.99,
            "exercises": [
                {"name": "Sentadilla con Barra", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Cuádriceps"},
                {"name": "Press Banca", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Pecho"},
                {"name": "Peso Muerto Rumano", "sets": 4, "reps": 10, "weight": 0, "rest_seconds": 120, "muscle_group": "Isquios"},
                {"name": "Remo con Barra", "sets": 4, "reps": 10, "weight": 0, "rest_seconds": 90, "muscle_group": "Espalda"},
                {"name": "Press Militar con Barra", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 90, "muscle_group": "Hombros"},
                {"name": "Dominadas", "sets": 4, "reps": 8, "weight": 0, "rest_seconds": 120, "muscle_group": "Dorsal"},
                {"name": "Curl con Barra", "sets": 3, "reps": 12, "weight": 0, "rest_seconds": 60, "muscle_group": "Bíceps"},
                {"name": "Tríceps en Polea", "sets": 3, "reps": 12, "weight": 0, "rest_seconds": 60, "muscle_group": "Tríceps"},
                {"name": "Plancha", "sets": 3, "reps": 60, "weight": 0, "rest_seconds": 60, "muscle_group": "Core"},
            ],
        },
        {
            "name": "🔥 Quema Grasa HIIT 20min",
            "description": "Sesiones intensas de 20 minutos tipo HIIT. Quema calorías como una caldera y mejora tu capacidad cardiovascular.",
            "price_eur": 3.99,
            "exercises": [
                {"name": "Burpee", "sets": 5, "reps": 12, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
                {"name": "Mountain Climbers", "sets": 5, "reps": 30, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
                {"name": "Jumping Jacks", "sets": 5, "reps": 40, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
                {"name": "Rodillas Arriba (High Knees)", "sets": 5, "reps": 30, "weight": 0, "rest_seconds": 30, "muscle_group": "Cardio"},
                {"name": "Sentadilla con salto", "sets": 4, "reps": 15, "weight": 0, "rest_seconds": 45, "muscle_group": "Piernas"},
                {"name": "Flexión de pecho", "sets": 4, "reps": 12, "weight": 0, "rest_seconds": 45, "muscle_group": "Pecho"},
                {"name": "Plancha", "sets": 3, "reps": 45, "weight": 0, "rest_seconds": 30, "muscle_group": "Core"},
            ],
        },
        {
            "name": "🏋️ Fuerza Pura 5x5",
            "description": "Método 5x5 clásico para ganar fuerza bruta. Pecho, espalda, piernas en 3 días. Progresión lineal.",
            "price_eur": 3.99,
            "exercises": [
                {"name": "Sentadilla con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Cuádriceps"},
                {"name": "Press Banca", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Pecho"},
                {"name": "Peso Muerto", "sets": 3, "reps": 5, "weight": 0, "rest_seconds": 240, "muscle_group": "Espalda"},
                {"name": "Press Militar con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 180, "muscle_group": "Hombros"},
                {"name": "Remo con Barra", "sets": 5, "reps": 5, "weight": 0, "rest_seconds": 150, "muscle_group": "Espalda"},
            ],
        },
    ]

    admin = await db.users.find_one({"email": "admin@fitness.com"})
    if not admin:
        raise HTTPException(500, "Admin user no encontrado")

    created = []
    skipped = []
    for r in PREMIUM_ROUTINES:
        existing = await db.routines.find_one({"name": r["name"], "is_premium_routine": True})
        if existing:
            skipped.append(r["name"])
            continue
        # Inyectar IDs únicos a cada ejercicio
        exercises_with_ids = [{**ex, "id": str(uuid.uuid4())} for ex in r["exercises"]]
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": admin["id"],
            "name": r["name"],
            "description": r["description"],
            "exercises": exercises_with_ids,
            "is_public": True,
            "is_premium_routine": True,
            "price_eur": r["price_eur"],
            "is_predefined": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.routines.insert_one(doc)
        created.append(r["name"])

    return {"created": created, "skipped": skipped, "total_created": len(created)}



@app.on_event("startup")
async def on_startup():
    # ========== INDEXES (performance-critical) ==========
    # users
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.users.create_index("stripe_customer_id", sparse=True)
    await db.users.create_index("username", sparse=True)
    # routines
    await db.routines.create_index([("user_id", 1), ("created_at", -1)])
    await db.routines.create_index("id", unique=True)
    await db.routines.create_index([("is_public", 1), ("rating_avg", -1)])
    await db.routines.create_index([("is_premium_routine", 1), ("is_public", 1)])
    # workouts
    await db.workouts.create_index([("user_id", 1), ("completed_at", -1)])
    await db.workouts.create_index("id", unique=True)
    # cardio
    await db.cardio.create_index([("user_id", 1), ("completed_at", -1)])
    await db.cardio.create_index("id", unique=True)
    # password_resets (email lookups + TTL-like cleanup via expires_at)
    await db.password_resets.create_index("email", unique=True)
    # schedule / calendar
    await db.schedule.create_index([("user_id", 1), ("date", -1)])
    # ratings
    await db.routine_ratings.create_index([("routine_id", 1), ("user_id", 1)], unique=True)
    # custom exercises
    await db.custom_exercises.create_index("id", unique=True)
    await db.custom_exercises.create_index("category")
    # body_entries
    await db.body_entries.create_index([("user_id", 1), ("date", -1)])
    # ai_messages
    await db.ai_messages.create_index([("user_id", 1), ("created_at", -1)])
    # payments
    await db.payment_transactions.create_index("user_id")
    await db.payment_transactions.create_index("session_id", unique=True, sparse=True)
    # follows / social
    await db.users.create_index("following", sparse=True)
    await db.users.create_index("followers", sparse=True)
    # Seed baseline data
    await seed_admin()
    await seed_predefined()
    logger.info("Startup complete — indexes + seeds OK")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

# Mount router LAST so all @api decorators above are picked up
app.include_router(api)
