# -*- coding: utf-8 -*-
"""
محرك الذكاء المالي — يجلب بيانات واجهة المحاسبة (POS) مع عدم خلطها بقيم «وهمية»
عندما تكون مفاتيح الربط مفعّلة. عند تعذّر الوصول لـ POS تُعرض رسالة خطأ وحالة فارغة
مع مؤشرات حقيقية فقط من قاعدة البيانات المحلية (واتساب / استفسارات الفروع).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _safe_pct_change(current: float, previous: float) -> float:
    if previous in (0, 0.0):
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100.0, 2)


def _compute_kpis(today_sales: float, transactions: int, profit_margin_pct: float) -> Dict[str, Any]:
    OPEX_RATIO = 0.18
    margin = max(0.0, min(95.0, float(profit_margin_pct or 0))) / 100.0
    gross_profit = round(today_sales * margin, 2)
    opex = round(today_sales * OPEX_RATIO, 2)
    net_margin_value = round(gross_profit - opex, 2)
    net_margin_pct = round((net_margin_value / today_sales) * 100, 2) if today_sales else 0.0
    return {
        "gross_profit": gross_profit,
        "operating_expenses": opex,
        "net_margin_value": net_margin_value,
        "net_margin_pct": net_margin_pct,
        "avg_ticket": round(today_sales / transactions, 2) if transactions > 0 else 0.0,
    }


def _month_buckets(series_labels: List[str], series_values: List[float]) -> Dict[str, float]:
    buckets: Dict[str, float] = {}
    now_year = datetime.utcnow().year
    for lbl, val in zip(series_labels or [], series_values or []):
        s = str(lbl).strip()
        try:
            if len(s) >= 10 and s[4] == "-":
                key = s[:7]
            elif len(s) == 5 and s[2] == "-":
                key = f"{now_year}-{s[:2]}"
            else:
                continue
        except Exception:
            continue
        try:
            buckets[key] = buckets.get(key, 0.0) + float(val or 0)
        except (TypeError, ValueError):
            continue
    return buckets


def _rollup_last_months(month_to_value: Dict[str, float], n: int = 6) -> Tuple[List[str], List[float]]:
    if not month_to_value:
        return [], []
    keys = sorted(month_to_value.keys())[-n:]
    return keys, [round(month_to_value[k], 2) for k in keys]


def _month_ar_label(ym_key: str) -> str:
    """YYYY-MM -> نص مختصر شبيه بتسمية الشارت."""
    try:
        y, m = ym_key.split("-", 1)
        mi = int(m)
        return f"{mi:02d}/{y[-2:]}"
    except Exception:
        return ym_key


def _monthly_bundle_from_daily(
    labels: List[str],
    vals: List[Any],
    n_months: int = 6,
) -> Tuple[List[str], List[float]]:
    buckets = _month_buckets(labels or [], [float(x or 0) for x in (vals or [])])
    ym_keys, num = _rollup_last_months(buckets, n_months)
    short = [_month_ar_label(k) for k in ym_keys]
    return short, num


def _parse_msg_ts(ts_raw: Any) -> Optional[datetime]:
    s = str(ts_raw or "").strip().replace("Z", "").replace("T", " ")
    if not s:
        return None
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    part = (s.replace(" ", "T").split(".", 1)[0])[:19]
    try:
        if len(part) >= 19:
            return datetime.strptime(part[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace(" ", "T")[:19])
    except ValueError:
        return None


def _whatsapp_inbound_24h_pair(db: Any) -> Tuple[int, int]:
    conn = db._get_connection()
    ts_list: List[str] = []
    try:
        cur = conn.execute(
            """
            SELECT msg_timestamp FROM messages
            WHERE direction = 'inbound'
            ORDER BY id DESC LIMIT 15000
            """
        )
        for r in cur.fetchall():
            row = dict(r) if hasattr(r, "keys") else {"msg_timestamp": r[0]}
            ts_list.append(str(row.get("msg_timestamp") or "")[:26])
    except Exception as e:
        logger.debug("_whatsapp_inbound_24h_pair: %s", e)
        return 0, 0
    finally:
        conn.close()

    now = datetime.now()
    c1 = now - timedelta(days=1)
    c2 = now - timedelta(days=2)
    cur_w = prv = 0
    for ts in ts_list:
        d = _parse_msg_ts(ts)
        if not d:
            continue
        if d >= c1:
            cur_w += 1
        elif d >= c2:
            prv += 1
    return cur_w, prv


def _conversion_rate_pct(db: Any) -> float:
    try:
        rows = db.summarize_inquiries_by_branch() or []
    except Exception:
        return 0.0
    answered = sum(int(r.get("answered_cnt") or 0) for r in rows)
    total = sum(int(r.get("total_cnt") or 0) for r in rows)
    if not total:
        return 0.0
    return round((answered / total) * 100.0, 1)


def _db_live_dashboard_layer(db: Any) -> Dict[str, Any]:
    wa_now, wa_prev = _whatsapp_inbound_24h_pair(db)
    wa_trend = _safe_pct_change(float(wa_now), float(wa_prev))
    conversion = _conversion_rate_pct(db)
    try:
        ds = db.get_daily_chat_series(days=200)
    except Exception:
        ds = {"labels": [], "values": []}
    sm_lab, sm_inq = _monthly_bundle_from_daily(
        list(ds.get("labels") or []),
        list(ds.get("values") or []),
        6,
    )
    zeros = [0.0] * len(sm_lab)
    return {
        "conversion_rate_pct": conversion,
        "whatsapp_active_24h": int(wa_now),
        "whatsapp_prev_24h": int(wa_prev),
        "whatsapp_trend_vs_prev_pct": wa_trend,
        "six_month_labels_chart": sm_lab,
        "six_month_inquiries_series_chart": sm_inq,
        "six_month_sales_series_chart": zeros,
        "analytics_period_note": "(بيانات واتساب / الشات المحلّية)",
    }


def _sales_trend_vs_yesterday_pct(today: float, yesterday: float) -> Optional[float]:
    if today <= 0 and yesterday <= 0:
        return None
    return _safe_pct_change(float(today), float(yesterday))


def _extract_remote_sales_yesterday(data: Dict[str, Any]) -> Optional[float]:
    for k in ("yesterday_sales", "sales_yesterday", "total_sales_yesterday"):
        try:
            v = data.get(k)
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _month_rows_from_remote(data: Dict[str, Any]) -> Optional[Tuple[List[str], List[float], List[float]]]:
    mr = data.get("months") or data.get("monthly")
    if not isinstance(mr, list) or len(mr) < 2:
        return None
    labels: List[str] = []
    sales: List[float] = []
    inq: List[float] = []
    for raw in mr[-24:]:
        if not isinstance(raw, dict):
            continue
        lab = raw.get("label") or raw.get("month") or raw.get("key")
        if lab is None:
            continue
        labels.append(str(lab).strip())
        try:
            sales.append(float(raw.get("sales") or raw.get("sales_sar") or raw.get("revenue") or 0))
            inq.append(float(raw.get("inquiries") or raw.get("chats") or raw.get("wa_count") or 0))
        except (TypeError, ValueError):
            sales.append(0.0)
            inq.append(0.0)
    if len(labels) < 1:
        return None
    n = len(labels)
    return labels[max(0, n - 6) :], sales[max(0, n - 6) :], inq[max(0, n - 6) :]


def _build_mom_comparison(labels: List[str], values: List[float]) -> Dict[str, Any]:
    months = _month_buckets(labels, values)
    if not months:
        return {
            "current_month_label": "",
            "previous_month_label": "",
            "current_total": 0.0,
            "previous_total": 0.0,
            "pct_change": 0.0,
            "verdict": "",
        }
    sorted_keys = sorted(months.keys())
    cur_k = sorted_keys[-1]
    prev_k = sorted_keys[-2] if len(sorted_keys) > 1 else cur_k
    cur_v = round(months[cur_k], 2)
    prev_v = round(months[prev_k], 2) if prev_k != cur_k else 0.0
    pct = _safe_pct_change(cur_v, prev_v)
    if cur_k == prev_k:
        verdict = ""
    elif pct >= 10:
        verdict = f"نمو {pct:+.1f}% عن الشهر السابق."
    elif pct >= 0:
        verdict = f"استقرار نسبي {pct:+.1f}%."
    else:
        verdict = f"تراجع {pct:+.1f}% يستحق المتابعة."
    return {
        "current_month_label": cur_k,
        "previous_month_label": prev_k,
        "current_total": cur_v,
        "previous_total": prev_v,
        "pct_change": pct,
        "verdict": verdict,
    }


def _peak_hours_per_branch(db: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        branches = db.get_all_branches() or []
    except Exception:
        branches = []
    if not branches:
        return out

    conn = None
    raw_ts: List[Tuple[int, str]] = []
    try:
        conn = db._get_connection()
        cur = conn.execute(
            """
            SELECT branch_id, msg_timestamp
            FROM messages
            WHERE branch_id IS NOT NULL
              AND msg_timestamp IS NOT NULL
              AND direction = 'inbound'
            """
        )
        for r in cur.fetchall():
            try:
                row = dict(r) if hasattr(r, "keys") else {"branch_id": r[0], "msg_timestamp": r[1]}
                bid = int(row.get("branch_id") or 0)
                ts = str(row.get("msg_timestamp") or "")
            except (TypeError, ValueError):
                continue
            if ts:
                raw_ts.append((bid, ts))
    except Exception as e:
        logger.debug("peak_hours: %s", e)
        raw_ts = []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    by_branch: Dict[int, Dict[int, int]] = {}
    for bid, ts in raw_ts:
        try:
            t = ts.replace("T", " ")
            time_part = t.split(" ", 1)[1] if " " in t else t
            hh = int(time_part.split(":", 1)[0])
            if not (0 <= hh <= 23):
                continue
        except (ValueError, IndexError):
            continue
        by_branch.setdefault(bid, {}).setdefault(hh, 0)
        by_branch[bid][hh] = by_branch[bid][hh] + 1

    for b in branches:
        bid = int(b.get("id") or 0)
        name = (b.get("city_name") or b.get("branch_name") or str(bid)).strip()
        hour_map = by_branch.get(bid, {})
        hours = sorted(hour_map.items(), key=lambda x: -x[1])
        peak_hour = hours[0][0] if hours else None
        total = sum(h[1] for h in hours)
        if peak_hour is None:
            advice = "لا توجد بيانات كافية بعد."
        elif 7 <= peak_hour <= 11:
            advice = "ذروة صباحية — تأكد من الجاهزية المبكرة."
        elif 12 <= peak_hour <= 16:
            advice = "ذروة منتصف النهار."
        elif 17 <= peak_hour <= 21:
            advice = "ذروة مسائية."
        else:
            advice = "ذروة خارج أوقات الذروة المعتادة."
        out.append(
            {
                "branch_id": bid,
                "branch_name": name,
                "peak_hour": peak_hour,
                "peak_hour_label": f"{peak_hour:02d}:00" if peak_hour is not None else "—",
                "total_interactions": total,
                "advice": advice,
            }
        )
    out.sort(key=lambda r: -(r.get("total_interactions") or 0))
    return out


def _estimate_returns(db: Any, today_sales: float) -> Dict[str, Any]:
    refund_count = 0
    try:
        conn = db._get_connection()
        try:
            cur = conn.execute(
                """
                SELECT COUNT(*) AS c FROM complaints
                WHERE (complaint_type IN ('refund', 'return', 'exchange'))
                   OR (LOWER(COALESCE(message,'')) LIKE '%استرجاع%')
                   OR (LOWER(COALESCE(message,'')) LIKE '%مرتجع%')
                   OR (LOWER(COALESCE(message,'')) LIKE '%استبدال%')
                """
            )
            row = cur.fetchone()
            refund_count = int((row[0] if row else 0) or 0) if not isinstance(row, dict) else int(row.get("c") or 0)
        finally:
            conn.close()
    except Exception as e:
        logger.debug("returns estimate failed: %s", e)
        refund_count = 0

    avg_ticket = max(80.0, today_sales / 50 if today_sales else 80.0)
    est_value = round(refund_count * avg_ticket * 0.6, 2)
    ratio = round((est_value / today_sales) * 100, 2) if today_sales > 0 else 0.0
    if ratio == 0:
        msg = "لا توجد طلبات استرجاع نشطة ضمن هذا السياق."
    elif ratio < 3:
        msg = "نسبة الاسترجاع منخفضة ضمن هذا التقدير."
    elif ratio < 7:
        msg = "نسبة الاسترجاع متوسطة — راقب جودة التوريد والشحن."
    else:
        msg = "نسبة الاسترجاع مرتفعة — تحقق من سبب الزيادة."
    return {
        "return_requests": refund_count,
        "estimated_return_value": est_value,
        "return_ratio_pct": ratio,
        "verdict": msg,
    }


def _branch_recommendation(branches: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    if not branches:
        return out
    sorted_b = sorted(branches, key=lambda r: -(float(r.get("estimated_sales_month") or 0)))
    top = sorted_b[0]
    out.append(f"يقود فرع «{top.get('branch_name')}» أعلى ظهور في بيانات POS الحالية.")
    if len(sorted_b) >= 2:
        bottom = sorted_b[-1]
        out.append(f"راقب نشاط فرع «{bottom.get('branch_name')}» مقارنة بالبقية.")
    return out[:4]


def _fallback_from_db(db: Any) -> Dict[str, Any]:
    labels: List[str] = []
    inquiry_vals: List[int] = []
    try:
        series = db.get_daily_chat_series(days=60)
        labels[:] = series.get("labels") or []
        base = series.get("values") or []
        inquiry_vals = [int(max(0, int(v))) for v in base]
        sales_like = [float(max(0, int(v))) * 42.7 for v in base]
    except Exception:
        labels, sales_like, inquiry_vals = [], [], []

    branch_cards: List[Dict[str, Any]] = []
    try:
        inq_rep = db.summarize_inquiries_by_branch() or []
        branches = db.get_all_branches() or []
        by_city = {(r.get("branch_name") or "").strip(): r for r in inq_rep}
        for b in branches:
            city = (b.get("city_name") or "").strip() or str(b.get("id"))
            row = by_city.get(city) or {}
            pending = int(row.get("pending_cnt") or 0)
            tot = int(row.get("total_cnt") or pending)
            est_sales = round(8800 + pending * 120 + tot * 45, 2)
            branch_cards.append(
                {
                    "branch_id": int(b.get("id") or 0),
                    "branch_name": city,
                    "estimated_sales_month": est_sales,
                    "inquiry_total": tot,
                    "inquiries_pending": pending,
                }
            )
    except Exception:
        branch_cards = []

    live = _db_live_dashboard_layer(db)
    tx_count_last = int(inquiry_vals[-1]) if inquiry_vals else 0
    today_sale = round(sum(float(x) for x in sales_like[-1:]), 2) if sales_like else 0.0
    if today_sale <= 0 and tx_count_last > 0:
        today_sale = round(tx_count_last * 127.0, 2)
    tx_count = tx_count_last if tx_count_last > 0 else 1
    margin_pct = 22.8
    kpis = _compute_kpis(today_sale, tx_count, margin_pct)

    merged: Dict[str, Any] = {
        "mode": "internal_fallback",
        "pos_connected": False,
        "pos_error": "",
        "updated_at": int(time.time()),
        "today_sales": today_sale,
        "transaction_count": int(tx_count),
        "profit_margin_estimate_pct": margin_pct,
        "kpis": kpis,
        "timeseries_days": labels,
        "series_sales_estimate": [],
        "series_inquiries": inquiry_vals,
        "branches_breakdown": branch_cards,
        "inventory_signal": {},
        "mom_comparison": _build_mom_comparison(labels, inquiry_vals[:180]),
        "branch_peak_hours": _peak_hours_per_branch(db),
        "returns_signal": _estimate_returns(db, max(today_sale, 1.0)),
        "recommendations": _branch_recommendation(branch_cards),
        "six_month_labels_chart": live["six_month_labels_chart"],
        "six_month_inquiries_series_chart": live["six_month_inquiries_series_chart"],
        "six_month_sales_series_chart": live["six_month_sales_series_chart"],
        "live": live,
        "sales_vs_yesterday_pct": None,
        "recommended_branch_slug": "",
    }

    fb = merged
    if not fb["six_month_sales_series_chart"] or sum(fb["six_month_sales_series_chart"]) <= 0:
        fb["six_month_sales_series_chart"] = [0.0] * len(fb["six_month_labels_chart"])
    return fb


def _normalize_remote_payload(data: Dict[str, Any], db: Any, live_sup: Dict[str, Any]) -> Dict[str, Any]:
    for nest_key in ("dashboard", "result", "summary", "payload", "report"):
        inner = data.get(nest_key)
        if isinstance(inner, dict):
            merged_in = dict(data)
            merged_in.update(inner)
            data = merged_in
            break
    if isinstance(data.get("data"), dict):
        nested = data.get("data")
        merged_in = dict(data)
        merged_in.update({k: v for k, v in nested.items()})
        data = merged_in

    extracted = _month_rows_from_remote(data)
    if extracted:
        mlab, msal, miq = extracted
        lbl_ch, sal_ch, inq_ch = mlab[-6:], msal[-6:], miq[-6:]
    else:
        inquiry_vals_remote = data.get("inquiry_series") or []
        lbl_d = data.get("labels") or data.get("dates") or []
        ss = data.get("sales_series") or []
        if isinstance(ss, list) and isinstance(lbl_d, list) and len(ss) == len(lbl_d):
            if isinstance(inquiry_vals_remote, list) and len(inquiry_vals_remote) == len(lbl_d):
                iq_row = inquiry_vals_remote
            else:
                iq_row = [0.0] * len(lbl_d)
            smb = _month_buckets(lbl_d, [float(x or 0) for x in ss])
            inb = _month_buckets(lbl_d, [float(x or 0) for x in iq_row])
            all_months = sorted(set(smb.keys()) | set(inb.keys()))
            ym_tail = all_months[-6:]
            lbl_ch = [_month_ar_label(k) for k in ym_tail]
            sal_ch = [round(float(smb.get(k, 0) or 0), 2) for k in ym_tail]
            inq_ch = [round(float(inb.get(k, 0) or 0), 2) for k in ym_tail]
        else:
            lbl_ch = live_sup["six_month_labels_chart"]
            sal_ch = [0.0] * len(lbl_ch)
            inq_ch = live_sup["six_month_inquiries_series_chart"]

    branches_in = data.get("branches") if isinstance(data.get("branches"), list) else []
    branch_cards: List[Dict[str, Any]] = []
    inquiries_map: Dict[str, int] = {}
    try:
        for r in db.summarize_inquiries_by_branch() or []:
            inquiries_map[(r.get("branch_name") or "").strip()] = int(r.get("total_cnt") or 0)
    except Exception:
        pass

    today_sales_r = float(data.get("today_sales") or data.get("total_sales_day") or data.get("revenue_day") or 0)
    yest = _extract_remote_sales_yesterday(data)
    trend_vs_y = (
        _sales_trend_vs_yesterday_pct(today_sales_r, yest) if yest is not None else None
    )

    tx_count = int(data.get("transaction_count") or data.get("tx") or 0)
    margin_pct = float(data.get("profit_margin_pct") or data.get("margin_pct") or 0)

    ach_list = []
    mx = 0.0
    if branches_in:
        for item in branches_in[:48]:
            if not isinstance(item, dict):
                continue
            nm = str(item.get("name") or item.get("branch_name") or item.get("city") or "").strip()
            sal = float(item.get("sales") or item.get("amount") or item.get("revenue_month") or 0)
            tgt = float(item.get("target") or item.get("goal") or item.get("budget") or 0)
            ach = float(item.get("achievement_pct") or item.get("pct") or 0)
            if ach <= 0 and tgt > 0 and sal > 0:
                ach = round(min(999.0, sal / tgt * 100.0), 1)
            inq_b = inquiries_map.get(nm, int(item.get("inquiries") or 0))
            bid = int(item.get("id") or item.get("branch_id") or 0)
            mx = max(mx, sal or ach)
            branch_cards.append(
                {
                    "branch_id": bid,
                    "branch_name": nm or "?",
                    "estimated_sales_month": sal,
                    "inquiry_total": inq_b,
                    "inquiries_pending": int(item.get("pending") or 0),
                    "achievement_pct": ach,
                    "target_ref": tgt,
                }
            )
            ach_list.append(ach)

    merged: Dict[str, Any] = {
        "mode": "remote",
        "pos_connected": True,
        "pos_error": "",
        "updated_at": int(time.time()),
        "today_sales": today_sales_r,
        "transaction_count": tx_count,
        "profit_margin_estimate_pct": margin_pct,
        "timeseries_days": [],  # reserved
        "series_sales_estimate": [],
        "series_inquiries": [],
        "branches_breakdown": branch_cards,
        "inventory_signal": data.get("inventory") if isinstance(data.get("inventory"), dict) else {},
        "six_month_labels_chart": lbl_ch,
        "six_month_sales_series_chart": sal_ch,
        "six_month_inquiries_series_chart": inq_ch,
        "live": live_sup,
        "sales_vs_yesterday_pct": trend_vs_y,
        "recommended_branch_slug": "",
    }

    merged["kpis"] = _compute_kpis(
        merged["today_sales"], merged["transaction_count"], merged["profit_margin_estimate_pct"]
    )
    lbl_for_mom: List[str] = []
    val_for_mom: List[float] = []
    if isinstance(data.get("labels"), list) and isinstance(data.get("sales_series"), list):
        for l, z in zip(data["labels"], data["sales_series"]):
            lbl_for_mom.append(str(l))
            val_for_mom.append(float(z or 0))
    elif lbl_ch:
        lbl_for_mom = list(lbl_ch)
        val_for_mom = list(map(float, sal_ch))
    merged["mom_comparison"] = _build_mom_comparison(lbl_for_mom, val_for_mom)
    merged["branch_peak_hours"] = _peak_hours_per_branch(db)
    merged["returns_signal"] = _estimate_returns(db, max(merged["today_sales"], 0.01))

    rr = data.get("returns") or data.get("refunds")
    if isinstance(rr, dict):
        rr_m = merged["returns_signal"]
        for k in ("return_requests", "estimated_return_value", "return_ratio_pct"):
            if rr.get(k) is not None:
                rr_m[k] = rr.get(k)

    merged["recommendations"] = _branch_recommendation(branch_cards)

    mxv = mx if mx > 0 else 1.0
    for b in merged["branches_breakdown"]:
        sal = float(b.get("estimated_sales_month") or 0)
        b["relative_bar_pct"] = round(min(100.0, (sal / mxv) * 100.0), 1)
        if float(b.get("achievement_pct") or 0) <= 0:
            b["achievement_pct"] = round(b["relative_bar_pct"], 1)

    return merged


def _remote_error_dashboard(db: Any, err: str) -> Dict[str, Any]:
    live = _db_live_dashboard_layer(db)
    z = len(live["six_month_labels_chart"])
    return {
        "mode": "remote_error",
        "pos_connected": False,
        "pos_error": err,
        "updated_at": int(time.time()),
        "today_sales": 0.0,
        "transaction_count": 0,
        "profit_margin_estimate_pct": 0.0,
        "kpis": _compute_kpis(0.0, 1, 0.0),
        "timeseries_days": [],
        "series_sales_estimate": [],
        "series_inquiries": [],
        "branches_breakdown": [],
        "inventory_signal": {},
        "six_month_labels_chart": live["six_month_labels_chart"],
        "six_month_sales_series_chart": [0.0] * z,
        "six_month_inquiries_series_chart": live["six_month_inquiries_series_chart"],
        "mom_comparison": _build_mom_comparison([], []),
        "branch_peak_hours": _peak_hours_per_branch(db),
        "returns_signal": _estimate_returns(db, 1.0),
        "recommendations": [],
        "live": live,
        "sales_vs_yesterday_pct": None,
        "recommended_branch_slug": "",
    }


def fetch_financial_dashboard(
    *,
    db: Any,
    base_url: str,
    api_key: str,
    api_secret: str,
    timeout_seconds: float = 14.0,
    erp_kind: Optional[str] = None,
) -> Dict[str, Any]:
    bu = (base_url or "").strip().rstrip("/")
    ak = (api_key or "").strip()
    sec = (api_secret or "").strip()

    if not bu.startswith(("http://", "https://")) or not ak:
        logger.info("accounting fetch skipped — missing POS URL/API key → internal preview")
        out = _fallback_from_db(db)
        out.setdefault("credentials_missing", True)
        return out

    live_first = _db_live_dashboard_layer(db)

    candidate_paths: List[str] = []
    kind = (erp_kind or "").strip().lower()
    if kind == "onyx":
        candidate_paths += ["/api/v1/dashboard", "/onyx/dashboard", "/api/finance/summary"]
    elif kind == "amazon":
        candidate_paths += ["/v1/dashboard", "/dashboard/summary"]
    elif kind in ("microsoft", "dynamics", "ms"):
        candidate_paths += ["/api/data/v9.2/finance_dashboard", "/api/finance/summary"]

    candidate_paths += [
        "/v1/dashboard",
        "/api/dashboard",
        "/dashboard/summary",
        "/api/v1/finance/summary",
    ]

    seen = set()
    candidate_paths = [p for p in candidate_paths if not (p in seen or seen.add(p))]

    try:
        import requests as _rq
    except ImportError:
        return _remote_error_dashboard(db, "مكتبة requests غير مثبتة على الخادم.")

    headers = {
        "Accept": "application/json",
        "User-Agent": "AlManakhFinance/3.0",
        "X-API-Key": ak,
        "X-API-Secret": sec,
        "Authorization": f"Bearer {ak}",
    }

    last_status: Optional[int] = None
    for path in candidate_paths:
        u = bu + path
        try:
            r = _rq.get(u, headers=headers, timeout=timeout_seconds)
            last_status = r.status_code
            if r.status_code != 200:
                logger.debug("[POS] %s → HTTP %s", u, r.status_code)
                continue
            payload = r.json()
            if not isinstance(payload, dict):
                continue
            return _normalize_remote_payload(payload, db, live_first)
        except Exception as ex:
            logger.debug("[POS] %s failed: %s", u, ex)

    detail = ""
    try:
        if last_status:
            detail = f"آخر محاولة: HTTP {last_status}. "
    except Exception:
        pass

    msg = detail + (
        "تعذّر جلب JSON من نقطة نقطة البيع. تحقّق من الـ Base URL والمفتاح؛ "
        "وتأكّد أن الـ API تعيد حقلاً واحداً على الأقل مثل "
        "`today_sales` أو `months` ضمن الغلاف JSON."
    )
    return _remote_error_dashboard(db, msg)
