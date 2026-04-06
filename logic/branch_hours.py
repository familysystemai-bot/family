"""
تقدير بسيط لكون الفرع ضمن أوقات العمل المعلنة (توقيت السعودية).
لا يضمن الافتتاح الفعلي — للاسترشاد فقط.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

# Windows قد يفتقد tzdata — نستخدم UTC+3 كبديل لـ Asia/Riyadh
try:
    if not ZoneInfo:
        raise RuntimeError("no zoneinfo")
    SA_TZ = ZoneInfo("Asia/Riyadh")  # type: ignore[misc]
    _ = datetime.now(SA_TZ)
except Exception:
    SA_TZ = timezone(timedelta(hours=3))


def _t(h: int, m: int = 0) -> time:
    return time(h, m, 0)


def is_branch_likely_open_now(city_key: str, now: Optional[datetime] = None) -> Optional[bool]:
    """
    city_key: jeddah | makkah | madinah | khamis | qilwah
    يُرجع True/False أو None إن لم يُحسب (لا توقيت).
    """
    if now is None:
        now = datetime.now(SA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SA_TZ)
    else:
        now = now.astimezone(SA_TZ)

    wd = now.weekday()  # اثنين=0 … جمعة=4 … أحد=6
    friday = wd == 4
    t = now.time()
    end_day = _t(23, 59)

    # الجمعة للجميع: 4 عصر - 12 ليل
    if friday:
        return _t(16, 0) <= t <= end_day

    ck = (city_key or "").strip().lower()
    # جدة وقلوة: فترتان في الأيام الأخرى
    if ck in ("jeddah", "qilwah"):
        morning = _t(8, 30) <= t < _t(12, 0)
        evening = _t(16, 0) <= t <= end_day
        return morning or evening

    # مكة، المدينة، خميس: يوم واحد متصل
    if ck in ("makkah", "madinah", "khamis"):
        return _t(8, 30) <= t <= end_day

    return None


def _parse_hm(s: Optional[str]) -> Optional[time]:
    raw = (s or "").strip().replace(".", ":")
    if not raw:
        return None
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return time(h % 24, max(0, min(m, 59)), 0)
    except (ValueError, IndexError):
        return None


def _time_in_period(t: time, start: time, end: time) -> bool:
    """فترة ضمن يوم واحد أو عبر منتصف الليل؛ 00:00 كنهاية تُعامل كحد أقصى لنفس اليوم."""
    if start == end:
        return False
    eff_end = end
    if end == time(0, 0) and start != time(0, 0):
        eff_end = time(23, 59, 59)
    if eff_end > start:
        return start <= t <= eff_end
    return t >= start or t <= eff_end


def is_branch_open_now_from_db_rows(
    rows: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[bool]:
    """
    صفوف working_hours لفرع واحد (weekday + friday).
    مفتوح إذا الوقت الحالي داخل الفترة 1 أو 2.
    None: لا صف لليوم أو لا أوقات صالحة — يُستخدم الـ fallback الثابت.
    """
    if not rows:
        return None
    if now is None:
        now = datetime.now(SA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SA_TZ)
    else:
        now = now.astimezone(SA_TZ)
    friday = now.weekday() == 4
    key = "friday" if friday else "weekday"
    row = next((r for r in rows if (r.get("day_type") or "").strip() == key), None)
    if not row:
        return None
    s1 = _parse_hm(row.get("start_time_1") or row.get("open_time"))
    e1 = _parse_hm(row.get("end_time_1") or row.get("close_time"))
    s2 = _parse_hm(row.get("start_time_2"))
    e2 = _parse_hm(row.get("end_time_2"))
    t = now.time()
    if s1 and e1:
        if _time_in_period(t, s1, e1):
            return True
        if s2 and e2 and _time_in_period(t, s2, e2):
            return True
        return False
    return None


def _fmt_time_ar(t: time) -> str:
    """صيغة عربية مختصرة لوقت واحد (للردود الآلية)."""
    h, m = t.hour, t.minute
    if h == 0 and m == 0:
        return "12 منتصف الليل"
    if h == 12 and m == 0:
        return "12 ظهراً"
    h12 = h % 12
    if h12 == 0:
        h12 = 12
    num = f"{h12}:{m:02d}" if m else str(h12)
    if h < 12:
        suf = "صباحاً"
    elif h == 12:
        suf = "ظهراً"
    elif h < 17:
        suf = "عصراً"
    elif h < 21:
        suf = "مساءً"
    else:
        suf = "ليلاً"
    return f"{num} {suf}"


def _line_for_day_row(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not row:
        return None
    s1 = _parse_hm(row.get("start_time_1") or row.get("open_time"))
    e1 = _parse_hm(row.get("end_time_1") or row.get("close_time"))
    s2 = _parse_hm(row.get("start_time_2"))
    e2 = _parse_hm(row.get("end_time_2"))
    if not s1 or not e1:
        return None
    parts = [f"من {_fmt_time_ar(s1)} إلى {_fmt_time_ar(e1)}"]
    if s2 and e2:
        parts.append(f"ومن {_fmt_time_ar(s2)} إلى {_fmt_time_ar(e2)}")
    return " ".join(parts)


def format_working_hours_brief_ar(rows: List[Dict[str, Any]]) -> str:
    """
    سطر أو سطران من أوقات الدوام من صفوف قاعدة البيانات فقط (بدون اختراع).
    """
    if not rows:
        return ""
    wk = next((r for r in rows if (r.get("day_type") or "").strip() == "weekday"), None)
    fri = next((r for r in rows if (r.get("day_type") or "").strip() == "friday"), None)
    l1 = _line_for_day_row(wk)
    l2 = _line_for_day_row(fri)
    if l1 and l2 and l1 != l2:
        return f"دوامنا {l1}، ويوم الجمعة {l2}."
    if l1:
        return f"دوامنا {l1}."
    if l2:
        return f"دوامنا {l2}."
    return ""


def next_opening_datetime_after(
    rows: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """أقرب وقت افتتاح بعد `now` إذا كان الفرع مغلقاً؛ None إذا مفتوح أو لا بيانات."""
    if not rows:
        return None
    if now is None:
        now = datetime.now(SA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SA_TZ)
    else:
        now = now.astimezone(SA_TZ)
    if is_branch_open_now_from_db_rows(rows, now):
        return None
    candidates: List[datetime] = []
    for offset in range(0, 8):
        day = now + timedelta(days=offset)
        d = day.date()
        wd = day.weekday()
        friday = wd == 4
        key = "friday" if friday else "weekday"
        row = next((r for r in rows if (r.get("day_type") or "").strip() == key), None)
        if not row:
            continue
        s1 = _parse_hm(row.get("start_time_1") or row.get("open_time"))
        s2 = _parse_hm(row.get("start_time_2"))
        for st in (s1, s2):
            if not st:
                continue
            dt = datetime.combine(d, st, tzinfo=now.tzinfo)
            if dt > now:
                candidates.append(dt)
    if not candidates:
        return None
    return min(candidates)


def minutes_until_next_opening(
    rows: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[int]:
    nxt = next_opening_datetime_after(rows, now)
    if not nxt:
        return None
    if now is None:
        now = datetime.now(SA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SA_TZ)
    else:
        now = now.astimezone(SA_TZ)
    return max(0, int((nxt - now).total_seconds() // 60))


def format_next_opening_clock_ar(rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> Optional[str]:
    """نص الساعة لأقرب افتتاح (مثلاً لرد «نفتح الساعة …»)."""
    nxt = next_opening_datetime_after(rows, now)
    if not nxt:
        return None
    return _fmt_time_ar(nxt.time())


def format_minutes_until_open_ar(mins: int) -> str:
    """
    أقل من ساعتين: دقائق / نصف ساعة / ساعة تقريباً.
    أكثر من 3 ساعات: صيغة «بعد X ساعات».
    """
    if mins <= 1:
        return "نفتح بعد دقايق بس إن شاء الله"
    if mins < 120:
        if mins <= 35:
            return f"نفتح بعد حوالي {mins} دقيقة إن شاء الله"
        if mins < 50:
            return "نفتح بعد حوالي نصف ساعة إن شاء الله"
        if mins < 80:
            return "نفتح بعد حوالي ساعة إن شاء الله"
        return "نفتح بعد حوالي ساعة ونص إن شاء الله"
    if mins <= 180:
        h = mins / 60.0
        rounded = max(2, int(round(h)))
        if rounded == 2:
            return "نفتح بعد حوالي ساعتين إن شاء الله"
        return f"نفتح بعد حوالي {rounded} ساعات إن شاء الله"
    rounded = int(round(mins / 60.0))
    return f"نفتح بعد حوالي {rounded} ساعات إن شاء الله"


def format_current_time_clock_ar(now: Optional[datetime] = None) -> str:
    """الساعة الحالية بتوقيت السعودية (صيغة عربية مختصرة)."""
    if now is None:
        now = datetime.now(SA_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SA_TZ)
    else:
        now = now.astimezone(SA_TZ)
    return _fmt_time_ar(now.time())
