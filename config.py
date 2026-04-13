"""
إعدادات التطبيق المركزية (مسارات، مفاتيح، قيم افتراضية).

- الإنتاج (مثل Render): المتغيرات من لوحة Environment فقط — لا يُفترض وجود ملف .env.
- محلياً: اختياريًا تحميل `.env` من جذر المشروع عند عدم تعيين RENDER.

ملاحظة: حزمة بيانات العلامة والفروع موجودة في المجلد site_config/ لتفادي التعارض مع اسم هذا الملف.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

# جذر المشروع (حيث يقع config.py)
BASE_DIR = Path(__file__).resolve().parent
_ENV_PATH = BASE_DIR / ".env"

if os.getenv("RENDER") is None:
    from dotenv import load_dotenv

    load_dotenv(_ENV_PATH)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip().replace(",", "."))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default

# ——— قاعدة البيانات ———
DATA_DIR = BASE_DIR / "data"
DATABASE_FILENAME = os.getenv("DATABASE_FILENAME", "family_system.db")
DATABASE_PATH = str(DATA_DIR / DATABASE_FILENAME)

# ——— Flask ———
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY must be set in the environment (e.g. .env or your host dashboard). "
        "Use a long random value; never commit real secrets."
    )
STATIC_FOLDER = "static"
UPLOAD_FOLDER_NAME = "uploads"
UPLOAD_FOLDER = str(BASE_DIR / STATIC_FOLDER / UPLOAD_FOLDER_NAME)

ALLOWED_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "webp", "webm", "wav", "mp3", "ogg", "m4a"}
)

# ——— تسجيل الدخول (لوحة الإدارة) ———
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "kazm")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError(
        "ADMIN_PASSWORD must be set in the environment. Refusing to start with a weak default."
    )

# ——— حساب المؤسس (لا يُخزَّن في قاعدة البيانات) ———
FOUNDER_USERNAME = os.getenv("FOUNDER_USERNAME", "kazm")
FOUNDER_PASSWORD = os.getenv("FOUNDER_PASSWORD")
if not FOUNDER_PASSWORD:
    raise RuntimeError(
        "FOUNDER_PASSWORD must be set in the environment. Refusing to start with a weak default."
    )

DEFAULT_BRANCH_PASSWORD = os.getenv("DEFAULT_BRANCH_PASSWORD")

# ——— معلومات المؤسس (تُعرض في المحادثة فقط عند السؤال الصريح عنها) ———
FOUNDER_PUBLIC_FULL_NAME = os.getenv("FOUNDER_PUBLIC_FULL_NAME", "كاظم نجيب المطحني")
FOUNDER_PUBLIC_PHONE = os.getenv("FOUNDER_PUBLIC_PHONE", "0538344673")

# ——— البريد ———
# المرسل الرسمي لـ SMTP (المستلمون: MAIN_RECEIVER_EMAIL / الفروع / الإدارة — لا تُستخدم كمرسل)
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_OFFICE_EMAIL = os.getenv("ADMIN_OFFICE_EMAIL", "management@family-mall.com")
# صندوق استقبال الشكاوى الرئيسي (يُرسل إليه كل بلاغ). يدعم الاسم الجديد أو القديم في .env
MAIN_RECEIVER_EMAIL = (
    os.getenv("MAIN_RECEIVER_EMAIL")
    or os.getenv("COMPLAINT_INBOX_EMAIL")
    or "almthnyalkazm@gmail.com"
)
DEFAULT_COMPLAINT_INBOX = MAIN_RECEIVER_EMAIL

# ——— تنبيهات تشخيص النظام (مسار /admin/diagnostics/full) ———
SYSTEM_ALERTS_EMAIL = (
    os.getenv("SYSTEM_ALERTS_EMAIL") or "theking73995@gmail.com"
).strip()
# تجنّب إعادة إرسال نفس التنبيهات خلال عدد الثواني التالية
DIAGNOSTICS_ALERT_COOLDOWN_SECONDS = int(
    os.getenv("DIAGNOSTICS_ALERT_COOLDOWN_SECONDS", "3600")
)

# ——— نماذج ذكاء اصطناعي (اختياري، يُقرأ من البيئة) ———
# Render: عيّن المفاتيح في Environment فقط — لا يُحمّل .env تلقائياً (انظر شرط RENDER أعلاه).
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if OPENAI_API_KEY is not None:
    OPENAI_API_KEY = OPENAI_API_KEY.strip() or None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# مرفقات الشات (Whisper + رؤية): يُعطّل بـ OPENAI_ATTACHMENTS=false
OPENAI_ATTACHMENTS_ENABLED = os.getenv(
    "OPENAI_ATTACHMENTS", "true"
).strip().lower() in ("1", "true", "yes")
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
# تشخيص منسّق الشات (طباعات/سجلات تفصيلية) — لا يُفعّل من مسار الطلب
OPENAI_ORCH_DEBUG = os.getenv("OPENAI_ORCH_DEBUG", "").strip().lower() in ("1", "true", "yes")
# تحليل استعلام البحث قبل SQLite (استخراج فقط) — يُعطّل بـ OPENAI_PRESEARCH=false
# تصنيف مسار unknown عبر OpenAI — يُعطّل بـ OPENAI_INTENT_CLASSIFIER=false
# منسّق الشات (قرار JSON: action + filters) — يُعطّل بـ OPENAI_CHAT_ORCHESTRATOR=false
# تشخيص stdout على Render: OPENAI_ORCH_DEBUG=true (يطبع OPENAI_API_KEY / OPENAI_CHAT_ORCHESTRATOR / MODEL / …)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# طبقة LLM كمحلل فقط (بدون ردود للمستخدم) داخل /chat_query
LLM_ENABLED = os.getenv("LLM_ENABLED", "false").lower() in ("1", "true", "yes")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL", OLLAMA_MODEL)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "90"))

# ——— عتبات القرار للشات (مركزية) ———
INTENT_SCORE_THRESHOLD_DIRECT = _env_float("CHAT_INTENT_SCORE_THRESHOLD", 60.0)
INTENT_SCORE_MARGIN_AMBIGUOUS = _env_float("CHAT_INTENT_SCORE_MARGIN", 12.0)
INTENT_SECOND_SCORE_AMBIGUOUS_FLOOR = _env_float(
    "CHAT_INTENT_SECOND_SCORE_FLOOR", 35.0
)
GLOBAL_RULE_THRESHOLD = _env_float("CHAT_GLOBAL_RULE_THRESHOLD", 50.0)

COMPLAINT_MIN_DETAIL = _env_int("CHAT_COMPLAINT_MIN_DETAIL", 22)
COMPLAINT_SCORE_THRESHOLD = _env_int("CHAT_COMPLAINT_SCORE_THRESHOLD", 2)
COMPLAINT_SCORE_PRIMARY_WEIGHT = _env_int("CHAT_COMPLAINT_SCORE_PRIMARY_WEIGHT", 2)
COMPLAINT_SCORE_NEGATIVE_WEIGHT = _env_int("CHAT_COMPLAINT_SCORE_NEGATIVE_WEIGHT", 1)
COMPLAINT_SCORE_BRANCH_WEIGHT = _env_int("CHAT_COMPLAINT_SCORE_BRANCH_WEIGHT", 1)

# ——— تشغيل السيرفر ———
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("PORT", os.getenv("FLASK_PORT", "5000")))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() in ("1", "true", "yes")

# ——— الحملات المجدولة (روابط الصور في البريد + خيط المجدول) ———
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
CAMPAIGN_SCHEDULER_ENABLED = os.getenv("CAMPAIGN_SCHEDULER_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
CAMPAIGN_SCHEDULER_INTERVAL_SEC = int(os.getenv("CAMPAIGN_SCHEDULER_INTERVAL_SEC", "60"))

# ——— جلسة Flask (لوحة التحكم) ———
PERMANENT_SESSION_LIFETIME = timedelta(days=30)


def ensure_upload_dir() -> None:
    """إنشاء مجلد الرفع إن لم يكن موجوداً."""
    Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)


def ensure_data_dir() -> None:
    """إنشاء مجلد البيانات إن لم يكن موجوداً."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def update_env_file(key: str, value: str) -> None:
    """
    يحدّث أو يضيف سطر KEY=value في ملف .env بجذر المشروع.
    يُستخدم لتحديث كلمة مرور المؤسس دون لمس قاعدة البيانات.
    """
    key = (key or "").strip()
    if not key or "=" in key or "\n" in key or "\r" in key:
        raise ValueError("مفتاح .env غير صالح")
    raw = str(value).replace("\n", "").replace("\r", "")
    if any(c in raw for c in ' #"\'\\'):
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        new_line = f'{key}="{escaped}"'
    else:
        new_line = f"{key}={raw}"

    path = _ENV_PATH
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                out.append(new_line)
                found = True
                continue
        out.append(line)
    if not found:
        out.append(new_line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def set_founder_password_runtime(new_password: str) -> None:
    """يحدّث كلمة مرور المؤسس في الذاكرة وفي os.environ بعد كتابة .env."""
    global FOUNDER_PASSWORD
    pw = str(new_password)
    os.environ["FOUNDER_PASSWORD"] = pw
    FOUNDER_PASSWORD = pw


def set_admin_password_runtime(new_password: str) -> None:
    """يحدّث كلمة مرور الإدارة في الذاكرة بعد كتابة .env."""
    global ADMIN_PASSWORD
    pw = str(new_password)
    os.environ["ADMIN_PASSWORD"] = pw
    ADMIN_PASSWORD = pw


def password_matches_stored(plain_password: str, stored: str | None) -> bool:
    """
    يطابق كلمة المرور المدخلة مع القيمة المحفوظة في .env.
    يدعم النص الصريح (القديم) وقيمة مُشفّرة بـ werkzeug (pbkdf2 / scrypt).
    """
    s = (stored or "").strip()
    if not s:
        return False
    plain = plain_password or ""
    if s.startswith("pbkdf2:") or s.startswith("scrypt:"):
        return check_password_hash(s, plain)
    return plain == s


def set_admin_username_runtime(new_username: str) -> None:
    global ADMIN_USERNAME
    u = (new_username or "").strip()
    if not u:
        raise ValueError("اسم مستخدم الإدارة فارغ")
    os.environ["ADMIN_USERNAME"] = u
    ADMIN_USERNAME = u


def set_founder_username_runtime(new_username: str) -> None:
    global FOUNDER_USERNAME
    u = (new_username or "").strip()
    if not u:
        raise ValueError("اسم مستخدم المؤسس فارغ")
    os.environ["FOUNDER_USERNAME"] = u
    FOUNDER_USERNAME = u


def persist_admin_password_hashed(new_plain: str) -> None:
    """تخزين كلمة مرور الإدارة في .env بصيغة hash وتحديث الذاكرة."""
    pw = (new_plain or "").strip()
    if not pw:
        raise ValueError("كلمة مرور الإدارة لا يمكن أن تكون فارغة")
    if len(pw) < 4:
        raise ValueError("كلمة مرور الإدارة قصيرة جداً (4 أحرف على الأقل)")
    hashed = generate_password_hash(pw)
    update_env_file("ADMIN_PASSWORD", hashed)
    set_admin_password_runtime(hashed)


def persist_founder_password_hashed(new_plain: str) -> None:
    pw = (new_plain or "").strip()
    if not pw:
        raise ValueError("كلمة مرور المؤسس لا يمكن أن تكون فارغة")
    if len(pw) < 4:
        raise ValueError("كلمة مرور المؤسس قصيرة جداً (4 أحرف على الأقل)")
    hashed = generate_password_hash(pw)
    update_env_file("FOUNDER_PASSWORD", hashed)
    set_founder_password_runtime(hashed)
