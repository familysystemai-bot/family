# -*- coding: utf-8 -*-
"""
توجيه طلبات الشات — ترتيب المعالجة ومسارات النية دون تغيير السلوك.
يستورد دوال الجلسة وقاعدة البيانات من chat_service.
"""
from __future__ import annotations

import copy
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Optional

from flask import Response, current_app, jsonify, request, session

from logic import keywords as kw
from logic.branch_service import _branch_location_json, branch_phone_payload
from logic.category_service import (
    _looks_like_section_stock_question,
    _section_chat_response,
    _try_resolve_pending_section_choice,
)
from logic.chat_handlers.complaint_handler import random_opening_apology
from logic.complaint_service import (
    _handle_complaint_policy_precheck_turn,
    _try_chat_active_complaint_turn,
    _try_complaint_rule_flow,
    _try_complaint_wizard,
    maybe_clear_complaint_session_before_router,
    try_complaint_ticket_status_lookup,
)
from logic import ai_fallback as ai_fb
from logic.dialect_detector import detect_dialect
from logic.dialect_responses import dialect_message
from logic.intent_handler import (
    decision_meets_global_rule_threshold,
    get_intent_routing_decision,
    pre_route_intent_snapshot,
    user_wants_open_now,
)
from logic.product_query_parse import normalize_for_product_search
from logic.product_service import (
    _build_products_response,
    _try_last_section_product_followup,
    _try_next_remaining_product_response,
    _try_pending_product_intent_confirmation,
    _try_product_detail_reply,
)
from site_config.company_policies import build_return_policy_chat_message
from site_config.founder_attribution import founder_attribution_payload_if_asked

import logic.chat_service as cs
from logic import attachment_openai
from logic import chat_context as chat_ctx
from logic import customer_chat as cust_ch
from logic.chat_handlers.time_handler import enhanced_location_reply_kind
from logic.chat_rules import (
    SALAM_REPLY_FIRST,
    SALAM_REPLY_SECOND,
    is_acceptable_display_name,
    looks_like_direct_request,
)

logger = logging.getLogger(__name__)

# يميّز «لم يُمرَّر precalc» عن «النتيجة None بعد تشغيل pending مرة واحدة» (تجنّب استدعاء مزدوج).
_PENDING_PRECALC_MISSING = object()


def _return_policy_reply_for_chat(display_name: str, message: str) -> str:
    """سياسات من company_info كما هي؛ وإلا حزمة الحقول؛ وإلا site_config."""
    db = cs.get_db()
    ans = db.get_policy_answer_exact(message)
    if ans is not None:
        return ans
    bundle = db.get_return_policy_bundle_text()
    if bundle.strip():
        return bundle.strip()
    return build_return_policy_chat_message(display_name, message)


def _collapse_consecutive_duplicate_lines(text: str) -> str:
    """يزيل أسطراً متتطابقة داخل الفقرة (نفس الجملة مكررة على سطرين)."""
    if not (text or "").strip():
        return text or ""
    out_chunks: list[str] = []
    for para in text.split("\n\n"):
        lines = para.split("\n")
        cleaned: list[str] = []
        prev_norm: Optional[str] = None
        for line in lines:
            n = line.strip()
            if n and n == prev_norm:
                continue
            cleaned.append(line)
            prev_norm = n if n else prev_norm
        out_chunks.append("\n".join(cleaned))
    return "\n\n".join(out_chunks)


def _dedupe_repeated_blocks_in_message(msg: str) -> str:
    """
    يزيل فقرات مكررة أو فقرة تُسبقها فقرة أطول بنفس البداية (دمج قواعد + AI).
    """
    raw = (msg or "").strip()
    if not raw:
        return ""
    raw = _collapse_consecutive_duplicate_lines(raw)
    parts = [p.strip() for p in raw.split("\n\n") if p.strip()]
    if len(parts) < 2:
        return raw
    n = len(parts)
    keep = [True] * n
    for i in range(n):
        if not keep[i]:
            continue
        pi = parts[i]
        for j in range(n):
            if i == j:
                continue
            pj = parts[j]
            if pi == pj and i < j:
                keep[j] = False
                continue
            # فقرة أطول تبدأ بنفس فقارة قصيرة ثم سطر جديد — نحذف الأقصر فقط (لا نخلط كلمة بجملة)
            if len(pj) > len(pi) and pj.startswith(pi):
                suf = pj[len(pi) :]
                if suf.startswith("\n"):
                    keep[i] = False
                    break
    out = [p for i, p in enumerate(parts) if keep[i]]
    deduped: list[str] = []
    for p in out:
        if deduped and p == deduped[-1]:
            continue
        deduped.append(p)
    return "\n\n".join(deduped)


def _merge_text_avoid_redundant_overlap(base_msg: str, ai_msg: str) -> str:
    """يدمج نص القواعد مع AI دون تكرار جملة/فقرة موجودة بالكامل في الأساس."""
    b = (base_msg or "").strip()
    a = (ai_msg or "").strip()
    if not a:
        return b
    if not b:
        return a
    if a.startswith(b) or (len(b) >= 8 and b in a):
        return a
    if b.startswith(a) or (len(a) >= 8 and a in b):
        return b
    b_norm = " ".join(b.split())
    a_norm = " ".join(a.split())
    if a_norm.startswith(b_norm) or (len(b_norm) >= 8 and b_norm in a_norm):
        return a
    bp = [p.strip() for p in b.split("\n\n") if p.strip()]
    ap = [p.strip() for p in a.split("\n\n") if p.strip()]
    novel: list[str] = []
    for p in ap:
        if p in bp:
            continue
        if any(p.startswith(q) and len(p) > len(q) for q in bp):
            novel.append(p)
            continue
        if any(q.startswith(p) and len(q) > len(p) for q in bp):
            continue
        novel.append(p)
    if not novel:
        return b
    return b + "\n\n" + "\n\n".join(novel)


def _scrub_disallowed_bot_phrases(text: str) -> str:
    """
    يزيل عبارات عديمة الفائدة (غالباً من نموذج) دون المساس بباقي النص.
    إن أصبح النص فارغاً بعد الإزالة يُعاد سلسلة فارغة → مسار صامت لاحقاً.
    """
    t = (text or "").strip()
    if not t:
        return ""
    # عبارات ممنوعة كاملة أو مكررة بلا قيمة مضافة
    banned = (
        "ما حصلت نفس الطلب",
        "ما حصلت نفس الطلب.",
    )
    for b in banned:
        if b in t:
            t = t.replace(b, " ")
    t = " ".join(t.split()).strip()
    return t


def _maybe_scrub_json_response(resp: Any) -> Any:
    """يطبّق _scrub_disallowed_bot_phrases على حقل message في استجابات JSON."""
    from flask import Response

    if isinstance(resp, tuple) and resp:
        inner = _maybe_scrub_json_response(resp[0])
        return (inner,) + resp[1:] if len(resp) > 1 else (inner,)

    if not isinstance(resp, Response):
        return resp
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return resp
    msg = (data.get("message") or "").strip()
    scrubbed = _scrub_disallowed_bot_phrases(msg)
    if scrubbed == msg:
        return resp
    out = dict(data)
    out["message"] = scrubbed
    prods = out.get("products") or []
    if not scrubbed and (not isinstance(prods, list) or len(prods) == 0):
        intent = str(out.get("intent") or "")
        if intent not in (
            "complaint_rule",
            "complaint_wizard",
            "complaint",
            "return_policy",
            "attachment",
            "collect_name",
            "error",
            "account_session_sync",
        ):
            out["intent"] = "silent"
    return jsonify(out)


def _deduplicate_bot_outgoing(resp: Any) -> Any:
    """يمنع إرسال نفس النص حرفياً مرتين متتاليتين قدر الإمكان."""
    from flask import Response

    if not isinstance(resp, Response):
        return resp
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return resp
    raw_msg = (data.get("message") or "").strip()
    if not raw_msg:
        return resp
    msg = _dedupe_repeated_blocks_in_message(raw_msg)
    raw_fol = (data.get("followup_message") or "").strip()
    fol = _dedupe_repeated_blocks_in_message(raw_fol) if raw_fol else ""
    if msg != raw_msg or (raw_fol and fol != raw_fol):
        data = dict(data)
        data["message"] = msg
        if raw_fol:
            data["followup_message"] = fol
        resp = jsonify(data)
    last_msg = (session.get("last_bot_message") or "").strip()
    if last_msg and msg == last_msg:
        d = session.get("chat_dialect") or "default"
        intent = str(data.get("intent") or "").strip()
        if intent in ("complaint", "complaint_rule", "complaint_wizard", "complaint_policy_precheck"):
            varied = "تم، أكمل بالخطوة التالية أو اكتب إضافتك باختصار."
        else:
            varied = dialect_message(d, "unknown_fallback", name=cs._display_name()).strip()
        if not varied or varied == msg:
            if intent in ("product", "recommendation", "section"):
                varied = "إذا حاب أكمل معك بدقة أكثر، عطِني اسم المنتج أو المقاس أو اللون."
            elif intent in ("location", "branch_phone"):
                varied = "إذا تبغى نفس الخدمة لكن لفرع مختلف، اكتب اسم المدينة أو الفرع."
            elif intent in ("complaint", "complaint_rule", "complaint_wizard", "complaint_policy_precheck"):
                varied = "تم، أكمل بالخطوة التالية أو اكتب إضافتك باختصار."
            else:
                varied = "وضح لي طلبك أكثر شوي عشان ما أكرر عليك نفس الرد."
        out = dict(data)
        out["message"] = varied
        session["last_bot_message"] = varied[:4000]
        return jsonify(out)
    session["last_bot_message"] = msg[:4000]
    return resp


def _apply_response_shaping_to_response(raw_resp: Any, user_message: str) -> Any:
    """
    يقصّر الرد ويزيل ذكر الفروع/أصناف بعيدة عند الحاجة — بعد التسويق وقبل منع التكرار.
    """
    if isinstance(raw_resp, tuple) and len(raw_resp) > 0:
        inner = _apply_response_shaping_to_response(raw_resp[0], user_message)
        return (inner,) + raw_resp[1:] if len(raw_resp) > 1 else (inner,)
    if not isinstance(raw_resp, Response):
        return raw_resp
    data = raw_resp.get_json(silent=True)
    if not isinstance(data, dict):
        return raw_resp
    intent = str(data.get("intent") or "").strip()
    if intent in ai_fb.BOT_RESPONSE_SHAPING_SKIP_INTENTS:
        return raw_resp
    new_data = dict(data)
    for key in ("message", "followup_message"):
        val = new_data.get(key)
        if isinstance(val, str) and val.strip():
            new_data[key] = ai_fb.apply_bot_response_shaping(
                val, user_message=user_message, intent=intent
            )
    return jsonify(new_data)


def _finalize_chat_outputs(raw_resp: Any, message: str) -> Any:
    """تسجيل آخر رسالة مستخدم، إثراء الاسم، تنظيف عبارات عديمة الفائدة، تسويق، منع تكرار رد البوت."""
    if message:
        session["chat_last_incoming_message"] = message
    step1 = _maybe_enrich_json_response(raw_resp)
    step2 = _maybe_scrub_json_response(step1)
    step3 = cust_ch.attach_marketing_followup_if_needed(step2)
    step4 = _apply_response_shaping_to_response(step3, message)
    return _deduplicate_bot_outgoing(step4)


def _trend_feature_value(branch_id: Optional[int], entity_name: str) -> str:
    """قيمة فريدة لـ trend_data: فرع + اسم الكيان (جدول feature_type / feature_value)."""
    b = 0 if branch_id is None else int(branch_id)
    n = (entity_name or "").strip().replace("\x1f", " ")[:600]
    if not n:
        n = "_"
    return f"{b}\x1f{n}"


def _resolve_branch_id_for_trends(db: Any) -> Optional[int]:
    """فرع من سياق الشات أو ملف العميل عند الحاجة."""
    for key in ("chat_last_branch", "chat_selected_branch"):
        bn = session.get(key)
        if isinstance(bn, str) and bn.strip():
            bid = db.get_branch_id_by_city_name(bn.strip())
            if bid is not None:
                return int(bid)
    cid = session.get("customer_id")
    if cid is not None:
        try:
            row = db.get_customer_by_id(int(cid))
            if row and row.get("branch_id") is not None:
                return int(row["branch_id"])
        except (TypeError, ValueError):
            pass
    return None


def _record_chat_trend_analytics(db: Any, response: Any) -> None:
    """
    يحدّث trend_data بعد رد ناجح: نوع intent ونوع product (feature_type).
    feature_value يضم branch_id وentity_name للتمييز الدقيق.
    """
    try:
        status = 200
        resp = response
        if isinstance(response, tuple) and len(response) >= 1:
            resp = response[0]
            if len(response) > 1 and isinstance(response[1], int):
                status = int(response[1])
        if status >= 400:
            return
        if not hasattr(resp, "get_json"):
            return
        data = resp.get_json(silent=True)
        if not isinstance(data, dict):
            return
        intent = str(data.get("intent") or "").strip() or "unknown"
        if intent == "account_session_sync":
            return

        branch_ctx = _resolve_branch_id_for_trends(db)
        db.upsert_trend("intent", _trend_feature_value(branch_ctx, intent))
        db.increment_daily_chat_count()

        br_h = branch_ctx if branch_ctx is not None else 0
        db.upsert_trend("hour", _trend_feature_value(br_h, f"{datetime.now().hour:02d}"))

        products = data.get("products")
        if not isinstance(products, list) or not products:
            return
        seen: set[tuple[Optional[int], str]] = set()
        for p in products:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            pname = (p.get("name") or p.get("product_name") or "").strip()
            if not pname and pid is not None:
                pname = f"product:{pid}"
            branch_id: Optional[int] = None
            if pid is not None:
                try:
                    detail = db.get_product_detail(int(pid))
                    if detail and detail.get("branch_id") is not None:
                        branch_id = int(detail["branch_id"])
                except (TypeError, ValueError):
                    pass
            if branch_id is None:
                branch_id = branch_ctx
            key = (branch_id, pname)
            if key in seen:
                continue
            seen.add(key)
            db.upsert_trend("product", _trend_feature_value(branch_id, pname))
    except Exception:
        logger.debug("chat trend analytics failed", exc_info=True)


def _finalize_chat_outputs_with_trends(db: Any, raw_resp: Any, message: str) -> Any:
    out = _finalize_chat_outputs(raw_resp, message)
    _record_chat_trend_analytics(db, out)
    return out


def _response_to_dict(resp: Any) -> Optional[dict[str, Any]]:
    """يستخرج dict من jsonify أو (Response, status)."""
    if resp is None:
        return None
    if isinstance(resp, tuple) and len(resp) > 0:
        return _response_to_dict(resp[0])
    if isinstance(resp, Response):
        d = resp.get_json(silent=True)
        return d if isinstance(d, dict) else None
    return None


def _merge_ai_over_rule(ai_d: Optional[dict], rule_d: Optional[dict]) -> dict[str, Any]:
    """
    هجين: نص القواعد + إثراء المنسّق عند وجودهما؛ المنتجات تُفضَّل من القواعد عند وجودها (مطابقة DB).
    """
    base = copy.deepcopy(rule_d) if isinstance(rule_d, dict) else {}
    ai = ai_d if isinstance(ai_d, dict) else {}
    out: dict[str, Any] = dict(base) if base else {}
    ai_msg = (ai.get("message") or "").strip()
    base_msg = (out.get("message") or "").strip()
    if ai_msg and base_msg:
        out["message"] = _merge_text_avoid_redundant_overlap(base_msg, ai_msg)
    elif ai_msg:
        out["message"] = ai["message"]
    elif base_msg:
        out["message"] = out.get("message", "")
    else:
        out["message"] = ""

    rule_prods = base.get("products") if isinstance(base.get("products"), list) else []
    ai_prods = ai.get("products") if isinstance(ai.get("products"), list) else []
    if rule_prods and len(rule_prods) > 0:
        out["products"] = rule_prods
    elif ai_prods and len(ai_prods) > 0:
        out["products"] = ai_prods
    else:
        out["products"] = []

    ri = str(base.get("intent") or "").strip()
    ai_intent = (ai.get("intent") or "").strip()
    if ri in (
        "complaint_rule",
        "complaint_wizard",
        "location",
        "branch_phone",
        "return_policy",
        "attachment",
        "collect_name",
        "product",
        "section",
        "recommendation",
        "greeting",
        "thanks",
        "goodbye",
    ):
        out["intent"] = ri or "general"
    elif ai_intent and ai_intent != "silent":
        out["intent"] = ai["intent"]
    elif "intent" not in out or not out.get("intent"):
        out["intent"] = "general"

    fol = (ai.get("followup_message") or "").strip()
    base_fol = (base.get("followup_message") or "").strip()
    if fol and base_fol:
        out["followup_message"] = f"{base_fol}\n\n{fol}"
    elif fol:
        out["followup_message"] = ai["followup_message"]
    for k in (
        "branches",
        "sections",
        "complaint_id",
        "email_sent",
        "user_name",
        "complaint_target",
        "complaint_type",
        "complaint_type_label",
    ):
        if k in base and k not in out:
            out[k] = base[k]
    return out


def _merge_complaint_ai_append(
    rule_d: dict[str, Any], ai_d: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """
    شكوى: الإبقاء على نص القواعد (اعتذار وهيكل) وإلحاق نص المنسّق دون استبدال المعنى.
    لا يُستبدل نص الشكوى الأساسي برد AI.
    """
    out = copy.deepcopy(rule_d) if isinstance(rule_d, dict) else {}
    if not isinstance(ai_d, dict):
        return out
    base_msg = (out.get("message") or "").strip()
    ai_msg = (ai_d.get("message") or "").strip()
    if ai_msg and base_msg:
        out["message"] = _merge_text_avoid_redundant_overlap(base_msg, ai_msg)
    out["intent"] = out.get("intent") or "complaint_rule"
    return out


def _is_weak_location_rule_payload(d: dict) -> bool:
    """طلب اختيار فرع / غير محدد → ضعيف ويستحق دعم المنسّق."""
    msg = (d.get("message") or "")
    intent = str(d.get("intent") or "")
    if intent == "branch_phone" and "أي فرع" in msg:
        return True
    if intent != "location":
        return False
    if "maps.google" in msg or "goo.gl" in msg or "http" in msg:
        return False
    if "ما التقطنا الفرع" in msg or "أي فرع" in msg or "أي مدينة" in msg:
        return True
    if msg.startswith("حاضر يا") and "؟" in msg:
        return True
    return False


def _map_rule_intent_to_scored_intent(rule_d: Optional[dict]) -> Optional[str]:
    if not isinstance(rule_d, dict):
        return None
    intent = str(rule_d.get("intent") or "").strip()
    if intent in ("product", "recommendation", "section"):
        return "product"
    if intent in ("location", "branch_phone", "location_pick"):
        return "branch"
    if intent in ("complaint", "complaint_rule", "complaint_wizard", "return_policy"):
        return "complaint"
    return None
def _intent_snapshot_unclear(decision: dict) -> bool:
    r = decision.get("route") or ""
    if r in ("needs_openai", "complex", "ambiguous", "weak"):
        return True
    return False


def _rule_payload_needs_orchestrator(
    rule_d: Optional[dict], decision: dict, message: str
) -> bool:
    """متى نرسل للمنسّق بعد القواعد: لا رد، ثقة دون العتبة، أو ناتج ناقص/غامض."""
    if not rule_d:
        return True
    intent = str(rule_d.get("intent") or "")
    if intent == "silent":
        return True
    if decision.get("route") == "score_direct":
        return False
    if not decision_meets_global_rule_threshold(
        decision, _map_rule_intent_to_scored_intent(rule_d)
    ):
        return True
    prods = rule_d.get("products") or []
    si = decision.get("score_intent") or ""
    if (intent in ("product", "section", "recommendation") or si == "product") and (
        not isinstance(prods, list) or len(prods) == 0
    ):
        return True
    if intent == "location" and _is_weak_location_rule_payload(rule_d):
        return True
    if _intent_snapshot_unclear(decision):
        return True
    return False


def _build_rule_findings_context(rule_d: Optional[dict]) -> dict[str, Any]:
    """ملخص قصير لما عرفته القواعد لتمريره للمنسّق عند الحاجة فقط."""
    if not isinstance(rule_d, dict):
        return {}
    out: dict[str, Any] = {}
    intent = str(rule_d.get("intent") or "").strip()
    if intent:
        out["rule_intent"] = intent
    msg = (rule_d.get("message") or "").strip()
    if msg:
        out["rule_message_preview"] = msg[:600]
    prods = rule_d.get("products") if isinstance(rule_d.get("products"), list) else []
    if prods:
        names = [
            str(p.get("name") or "").strip()
            for p in prods[:6]
            if isinstance(p, dict) and str(p.get("name") or "").strip()
        ]
        if names:
            out["rule_product_names"] = names
        out["rule_products_count"] = len(prods)
    brs = rule_d.get("branches") if isinstance(rule_d.get("branches"), list) else []
    if brs:
        out["rule_branch_options"] = [
            str(b.get("name") or b).strip()
            for b in brs[:8]
            if str(b.get("name") if isinstance(b, dict) else b or "").strip()
        ]
    return out


def _router_intent_branch_rules_only(
    message: str, branch_list: list, decision: dict
) -> Optional[Any]:
    """أقسام، فرع/دوام/هاتف، ثم rule_based و score_direct — دون المنسّق."""
    if _looks_like_section_stock_question(message):
        session["chat_current_intent"] = "section"
        session["chat_pending_action"] = None
        return _section_chat_response(message)

    t_norm = cs.normalize_message_for_branch_search(message)
    if any(k in t_norm for k in kw.SECTION_KEYWORDS):
        session["chat_current_intent"] = "section"
        session["chat_pending_action"] = None
        return _section_chat_response(message)

    rule_branch = _try_rule_based_branch_location_phone(message, branch_list)
    if rule_branch is not None:
        return rule_branch

    rb = _dispatch_rule_based_intent(message, branch_list, decision)
    if rb is not None:
        return rb

    sd = _dispatch_score_direct_intent(message, branch_list, decision)
    if sd is not None:
        return sd

    return None


def _route_main_chat_with_rules_and_ai(
    message: str,
    branch_list: list,
    pending_precalc: Any = _PENDING_PRECALC_MISSING,
) -> Any:
    """
    يجمع نتيجة القواعد (دون إيقاف المسار)، ثم يستدعي المنسّق عند الحاجة ويدمج (AI أولاً).
    pending_precalc: إن وُجد (بما فيه None بعد تشغيل pending مرة) يُستخدم بدل إعادة استدعاء _router_pending_and_services.
    """
    pending = (
        pending_precalc
        if pending_precalc is not _PENDING_PRECALC_MISSING
        else _router_pending_and_services(message, branch_list)
    )
    decision = get_intent_routing_decision(message, cs.resolve_branch_from_message)
    session["chat_intent_score_snapshot"] = decision.get("score_snapshot") or decision

    if pending is not None:
        intent_rules = None
    else:
        intent_rules = _router_intent_branch_rules_only(message, branch_list, decision)

    rule_resp = pending if pending is not None else intent_rules
    rule_d = _response_to_dict(rule_resp)
    # حوار شكوى منظم: رد واحد من القواعد فقط — بدون دمج منسّق (يمنع التكرار والاختلاط).
    if (
        rule_resp is not None
        and isinstance(rule_d, dict)
        and str(rule_d.get("intent") or "").startswith("complaint")
        and (
            session.get("complaint_data")
            or session.get("complaint_wizard")
            or session.get("complaint_policy_precheck")
        )
    ):
        return rule_resp
    if not session.get("complaint_active") and decision.get("route") == "score_direct" and rule_resp is not None:
        return rule_resp
    if isinstance(rule_d, dict):
        needs_orchestrator = _rule_payload_needs_orchestrator(rule_d, decision, message)
        if not session.get("complaint_active") and not needs_orchestrator:
            return rule_resp
    else:
        needs_orchestrator = True
    ai_resp = _execute_ai_orchestrator(
        message,
        branch_list,
        intent_decision=decision,
        rule_findings=_build_rule_findings_context(rule_d),
    )

    ai_d = _response_to_dict(ai_resp)
    rd = rule_d if isinstance(rule_d, dict) else {}
    intent_r = str(rd.get("intent") or "")
    use_complaint_append = (
        intent_r == "complaint_rule"
        and isinstance(ai_d, dict)
        and (ai_d.get("message") or "").strip()
        and not session.get("complaint_data")
    )
    if use_complaint_append:
        merged = _merge_complaint_ai_append(rd, ai_d)
    else:
        merged = _merge_ai_over_rule(ai_d, rule_d)

    msg_out = (merged.get("message") or "").strip()
    prods_out = merged.get("products") or []
    if not msg_out and (not isinstance(prods_out, list) or len(prods_out) == 0):
        if session.get("complaint_active"):
            return jsonify(
                {
                    "products": [],
                    "message": "ممكن توضح لي أكثر؟",
                    "intent": "complaint_rule",
                }
            )
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(
                    d, "unknown_fallback", name=cs._display_name()
                ),
                "intent": "general",
            }
        )
    return jsonify(merged)


def _resolve_branch_for_location(message: str) -> Optional[str]:
    """فرع من الرسالة، أو آخر فرع في السياق (منتج/قسم سابق)، بدون إجبار المستخدم بإعادة التحديد."""
    bn = cs.resolve_branch_from_message(message)
    if bn:
        chat_ctx.remember_branch_by_name(bn)
        chat_ctx.set_last_intent("location")
        return bn
    fb = chat_ctx.get_last_branch()
    if fb:
        chat_ctx.set_last_intent("location")
        return fb
    return None

def _should_reset_product_section_context(message: str) -> bool:
    """إشارات موضوعية لترك سياق المنتج/القسم — دون تصنيف نية كامل."""
    t = cs.normalize_message_for_branch_search((message or "").strip())
    if not t:
        return False
    tl = t.lower()
    if any(k in t for k in kw.THANKS_KEYWORDS):
        return True
    if any(k in tl for k in kw.GOODBYE_KEYWORDS):
        return True
    if any(k in t for k in kw.BRANCH_PHONE_CONTACT_TRIGGERS):
        return True
    if any(k in t for k in kw.BRANCH_LOCATION_KEYWORDS):
        return True
    if any(k in t for k in kw.BRANCH_HOURS_KEYWORDS):
        return True
    if user_wants_open_now(t):
        return True
    if any(k in t for k in kw.RETURN_POLICY_KEYWORDS):
        return True
    if any(k in t for k in kw.GREETING_KEYWORDS):
        return True
    if any(k in t for k in kw.RECOMMENDATION_PHRASES):
        return True
    if any(k in t for k in kw.COMPLAINT_KEYWORDS) or any(
        p in t for p in kw.COMPLAINT_NATURAL_PHRASES
    ):
        return True
    return False


def _maybe_enrich_json_response(resp: Any) -> Any:
    """يُكمّل رسالة طلب الاسم الاختياري بعد أول رد خدمة."""
    from flask import Response

    if not isinstance(resp, Response):
        return resp
    data = resp.get_json(silent=True)
    if not isinstance(data, dict):
        return resp
    try:
        enriched = chat_ctx.enrich_service_message(dict(data))
        return jsonify(enriched)
    except Exception:
        return resp


def _try_time_or_phone_followup(message: str, branch_list: list) -> Optional[Any]:
    """رسائل وقت/ساعة/رقم فرع تُفسَّر حتى لو النية unknown — مع last_branch."""
    kind = enhanced_location_reply_kind(message)
    if kind not in (
        "clock_now",
        "opening_clock_explain",
        "phone",
        "hours",
        "when_open",
        "open_now",
        "location_link",
    ):
        return None
    bn = _resolve_branch_for_location(message)
    if not bn:
        return None
    session["chat_pending_action"] = None
    session["chat_selected_branch"] = bn
    chat_ctx.remember_branch_by_name(bn)
    session["chat_current_intent"] = "location"
    return jsonify(_branch_location_json(bn, message))


def _maybe_reset_product_section_context(message: str) -> None:
    """
    إذا كان هناك سياق منتج/قسم ثم غيّر المستخدم الموضوع بشكل واضح (نية غير تسوّق)،
    نُزيل last_product / last_section وما يتبعها حتى لا تُفسَّر الرسالة كمتابعة سابقة.
    """
    if not (
        session.get("chat_last_product")
        or session.get("last_products")
        or session.get("last_section")
        or session.get("pending_section_choices")
    ):
        return
    if not _should_reset_product_section_context(message):
        return
    session.pop("chat_last_product", None)
    session.pop("last_products", None)
    session.pop("last_section", None)
    session.pop("pending_section_choices", None)
    session.pop("pending_product_intent", None)
    session.pop("remaining_products", None)
    session.pop("remaining_products_intent", None)


def _try_rule_based_branch_location_phone(message: str, branch_list: list) -> Optional[Any]:
    """
    قواعد فقط: دوام/موقع/رقم فرع — بدون تصنيف نية بالكامل (انظر intent_handler للكلمات).
    """
    t = cs.normalize_message_for_branch_search((message or "").strip())
    if not t:
        return None

    if any(x in t for x in kw.BRANCH_PHONE_CONTACT_TRIGGERS):
        session["chat_pending_action"] = None
        session["chat_current_intent"] = "branch_phone"
        bn = cs.resolve_branch_from_message(message) or chat_ctx.get_last_branch()
        if not bn:
            labels = [
                (b.get("name") or "").strip()
                for b in branch_list
                if (b.get("name") or "").strip()
            ]
            if len(labels) >= 2:
                msg = f"أي فرع؟ {labels[0]} ولا {labels[1]}؟"
            elif len(labels) == 1:
                msg = f"تقصد {labels[0]}؟ أكّد اسم المدينة لأرسل رقم التواصل."
            else:
                msg = f"أي مدينة يا {cs._display_name()}؟ {cs._branch_selection_prompt()}"
            return jsonify(
                {
                    "products": [],
                    "message": msg,
                    "branches": branch_list,
                    "intent": "branch_phone",
                }
            )
        chat_ctx.remember_branch_by_name(bn)
        return jsonify(branch_phone_payload(bn))

    if (
        any(k in t for k in kw.BRANCH_LOCATION_KEYWORDS)
        or any(k in t for k in kw.BRANCH_HOURS_KEYWORDS)
        or user_wants_open_now(t)
    ):
        session["chat_current_intent"] = "location"
        branch_name = _resolve_branch_for_location(message)
        if not branch_name:
            session["chat_pending_action"] = cs._CHAT_PENDING_BRANCH
            return jsonify(
                {
                    "products": [],
                    "message": f"حاضر يا {cs._display_name()}، {cs._branch_selection_prompt()}",
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = branch_name
        chat_ctx.remember_branch_by_name(branch_name)
        return jsonify(_branch_location_json(branch_name, message))

    return None


def _execute_ai_orchestrator(
    message: str,
    branch_list: list,
    intent_decision: Optional[dict] = None,
    rule_findings: Optional[dict[str, Any]] = None,
) -> Any:
    db = cs.get_db()
    # database_context يشمل company_info (سياسات/خدمات من لوحة الإدارة) عبر build_orchestrator_context
    context = ai_fb.build_orchestrator_context(
        db,
        message,
        session.get("chat_dialect"),
        intent_decision=intent_decision,
        rule_findings=rule_findings,
    )
    plan = ai_fb.run_chat_orchestrator_openai(message, context)
    if logger.isEnabledFor(logging.DEBUG):
        try:
            import json as _json

            logger.debug(
                "orchestrator plan: %s",
                _json.dumps(plan, ensure_ascii=False)[:2000],
            )
        except Exception:
            logger.debug("orchestrator plan: %r", plan)

    action = str(plan.get("action") or "").strip().lower()
    filters = plan.get("filters") if isinstance(plan.get("filters"), dict) else {}
    ai_msg = str(plan.get("message") or "").strip()
    needs_branch = bool(plan.get("needs_branch"))
    gender_f = filters.get("gender")
    if gender_f not in ("male", "female"):
        gender_f = ai_fb.infer_gender_from_message(message)
    rx = ai_fb.extract_user_context(message)
    px = plan.get("context") if isinstance(plan.get("context"), dict) else {}
    uctx = {
        "occasion": rx.get("occasion") or px.get("occasion"),
        "target": rx.get("target") or px.get("target"),
        "style": rx.get("style") or px.get("style"),
    }

    if action == "complaint":
        session["chat_pending_action"] = None
        br = cs.resolve_branch_from_message(message)
        if br:
            chat_ctx.remember_branch_by_name(br)
        needs_b = bool(plan.get("needs_branch", True)) and not br
        needs_d = bool(plan.get("needs_details", True))
        if len((message or "").strip()) < 22:
            needs_d = True
        session["complaint_wizard"] = {
            "apology_sent": True,
            "issue": message,
            "branch": br,
            "step": "collecting",
        }
        dn = cs._display_name()
        opening = ai_msg
        if not opening:
            if needs_b:
                opening = (
                    f"يا {dn}، نعتذر منك… عشان نخدمك صح، وش الفرع اللي صار فيه الموضوع؟ "
                    f"واكتب لي تفاصيل اللي صار."
                )
            elif needs_d:
                opening = (
                    f"يا {dn}، نعتذر منك… زوّدني بتفاصيل أوضح عن المشكلة عشان نراجعها مع الفريق."
                )
            else:
                opening = random_opening_apology()
        else:
            if needs_b:
                opening = (
                    f"{opening}\n\n"
                    f"يا {dn}، أكّد لي اسم الفرع إن ما كان واضح، واكتب تفاصيلك كاملة."
                )
            elif needs_d:
                opening = (
                    f"{opening}\n\n"
                    f"يا {dn}، لو تقدر تزيد تفاصيل (متى، وش صار بالضبط) يساعدنا نتابع أسرع."
                )
        return jsonify(
            {
                "products": [],
                "message": opening,
                "intent": "complaint_wizard",
                "branches": branch_list,
            }
        )

    if action == "return_policy":
        session["chat_pending_action"] = None
        session["chat_current_intent"] = "return_policy"
        return jsonify(
            {
                "products": [],
                "message": _return_policy_reply_for_chat(cs._display_name(), message),
                "intent": "return_policy",
            }
        )

    if action == "branch_request":
        session["chat_current_intent"] = "location"
        bn = _resolve_branch_for_location(message)
        if not bn:
            session["chat_pending_action"] = cs._CHAT_PENDING_BRANCH
            base = (
                ai_msg
                if ai_msg
                else f"حاضر يا {cs._display_name()}، {cs._branch_selection_prompt()}"
            )
            return jsonify(
                {
                    "products": [],
                    "message": base,
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = bn
        chat_ctx.remember_branch_by_name(bn)
        payload = _branch_location_json(bn, message)
        if isinstance(payload, dict) and ai_msg:
            om = (payload.get("message") or "").strip()
            payload["message"] = (ai_msg + ("\n\n" + om if om else "")).strip()
        return jsonify(payload)

    if action == "general_response":
        session["chat_current_intent"] = "general"
        session["chat_pending_action"] = None
        msg_out = ai_msg
        if not msg_out:
            d = session.get("chat_dialect") or "default"
            msg_out = dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            )
        if needs_branch:
            msg_out = (msg_out + "\n\n" + cs._branch_selection_prompt()).strip()
        return jsonify(
            {
                "products": [],
                "message": msg_out,
                "intent": "general",
            }
        )

    if action == "category_suggestion":
        session["chat_current_intent"] = "general"
        cats = filters.get("suggested_categories") or []
        if not isinstance(cats, list):
            cats = []
        cats = [c for c in cats if isinstance(c, str) and c.strip()]
        if not cats:
            cats = ai_fb.pick_fallback_categories(
                context,
                message,
                gender_f if gender_f in ("male", "female") else None,
            )
        msg_parts = [ai_msg] if ai_msg else []
        if cats:
            msg_parts.append("أقسام قد تناسبك: " + "، ".join(str(c) for c in cats[:8] if c))
        if needs_branch:
            msg_parts.append(cs._branch_selection_prompt())
        final_msg = "\n\n".join(p for p in msg_parts if p).strip()
        if not final_msg:
            d = session.get("chat_dialect") or "default"
            final_msg = dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            )
        final_msg = ai_fb.contextualize_no_product_message(
            final_msg, message, uctx, cats
        )
        if not (final_msg or "").strip():
            d = session.get("chat_dialect") or "default"
            final_msg = dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            )
        return jsonify({"products": [], "message": final_msg, "intent": "general"})

    if action == "product_search":
        sq = str(filters.get("search_query") or "").strip()
        base_q = sq if len(sq) >= 2 else message
        g_use = gender_f if gender_f in ("male", "female") else None
        merged_q = ai_fb.apply_shopping_context_to_search_query(base_q, g_use, uctx)
        psq = ai_fb.enhance_search_query_with_openai(merged_q, "product")
        prod = _build_products_response(psq, hint_source_message=message)
        if prod and prod.get("products"):
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            chat_ctx.set_last_intent("product")
            out = dict(prod)
            titles = [
                str(p.get("name") or "").strip()
                for p in (out.get("products") or [])
                if p.get("name")
            ]
            out["message"] = ai_fb.enrich_product_recommendation_message(
                message, uctx, ai_msg, titles
            )
            return jsonify(out)

        cats = filters.get("suggested_categories") or []
        if not isinstance(cats, list):
            cats = []
        cats = [c for c in cats if isinstance(c, str) and c.strip()]
        if not cats:
            cats = ai_fb.pick_fallback_categories(
                context,
                message,
                gender_f if gender_f in ("male", "female") else None,
            )
        msg_parts = []
        if ai_msg:
            msg_parts.append(ai_msg)
        if cats:
            msg_parts.append("ممكن تتفرّج على أقسام قريبة من طلبك: " + "، ".join(cats[:8]))
        if needs_branch:
            msg_parts.append(cs._branch_selection_prompt())
        final_msg = "\n\n".join(msg_parts).strip()
        if not final_msg:
            d = session.get("chat_dialect") or "default"
            final_msg = dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            )
        final_msg = ai_fb.contextualize_no_product_message(
            final_msg, message, uctx, cats
        )
        if not (final_msg or "").strip():
            d = session.get("chat_dialect") or "default"
            final_msg = dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            )
        return jsonify({"products": [], "message": final_msg, "intent": "general"})

    d = session.get("chat_dialect") or "default"
    return jsonify(
        {
            "products": [],
            "message": dialect_message(
                d, "unknown_fallback", name=cs._display_name()
            ),
            "intent": "general",
        }
    )


def _router_early_exits(data: dict) -> Optional[Any]:
    """account_session_sync، مؤسس، جمع الاسم، سلام… (المُرفقات تُعالَج قبل dispatch)."""
    if data.get("account_session_sync"):
        proposed = (data.get("user_name") or "").strip()
        if proposed and len(proposed) >= 2 and is_acceptable_display_name(proposed):
            session["user_name"] = proposed[:120]
            session["awaiting_user_name"] = False
            session.pop("chat_name_declined", None)
        uc = (data.get("user_contact") or "").strip()
        if uc:
            session["user_contact"] = uc[:320]
        return jsonify({"ok": True, "intent": "account_session_sync"})

    message = (data.get("message") or "").strip()
    proposed = (data.get("user_name") or "").strip()
    uc = (data.get("user_contact") or "").strip()
    if uc:
        session["user_contact"] = uc[:320]
    account_logged_in = bool(data.get("account_logged_in"))
    cs._apply_session_display_name(proposed, account_logged_in=account_logged_in)

    founder_payload = founder_attribution_payload_if_asked(message, cs._display_name())
    if founder_payload is not None:
        return jsonify(founder_payload)

    if chat_ctx.is_decline_name_message(message) and (
        session.get("awaiting_user_name") or session.get("chat_awaiting_optional_name")
    ):
        chat_ctx.mark_name_declined()
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(d, "collect_name_declined"),
                "intent": "collect_name",
            }
        )

    if session.get("chat_awaiting_optional_name") and not chat_ctx.has_declined_name():
        if is_acceptable_display_name(message):
            session["user_name"] = message.strip()[:120]
            session["chat_awaiting_optional_name"] = False
            u = session["user_name"]
            d = session.get("chat_dialect") or "default"
            return jsonify(
                {
                    "products": [],
                    "message": dialect_message(d, "after_optional_name", name=u),
                    "intent": "greeting",
                    "user_name": u,
                }
            )
        session["chat_awaiting_optional_name"] = False

    if chat_ctx.is_islamic_salam_message(message):
        pl: dict[str, Any] = {
            "products": [],
            "message": SALAM_REPLY_FIRST,
            "intent": "greeting",
        }
        if not looks_like_direct_request(message):
            pl["followup_message"] = SALAM_REPLY_SECOND
        return jsonify(pl)

    return None


def _router_pending_and_services(message: str, branch_list: list) -> Optional[Any]:
    """فرع معلّق، شكوى، منتج، أقسام — بالترتيب الأصلي."""
    ticket_lookup = try_complaint_ticket_status_lookup(message)
    if ticket_lookup is not None:
        return ticket_lookup
    maybe_clear_complaint_session_before_router(message)
    if session.get("complaint_active"):
        policy_precheck_r = _handle_complaint_policy_precheck_turn(message, branch_list)
        if policy_precheck_r is not None:
            return policy_precheck_r

        rule_complaint_r = _try_complaint_rule_flow(message, branch_list)
        if rule_complaint_r is not None:
            return rule_complaint_r

        wiz_resp = _try_complaint_wizard(message, branch_list)
        if wiz_resp is not None:
            return wiz_resp

        active_followup = _try_chat_active_complaint_turn(message, branch_list)
        if active_followup is not None:
            return active_followup

    time_fu = _try_time_or_phone_followup(message, branch_list)
    if time_fu is not None:
        return time_fu

    pending = session.get("chat_pending_action")
    if pending == cs._CHAT_PENDING_BRANCH:
        branch_name = cs.resolve_branch_from_message(message)
        if branch_name:
            session["chat_pending_action"] = None
            session["chat_selected_branch"] = branch_name
            chat_ctx.remember_branch_by_name(branch_name)
            session["chat_current_intent"] = "location"
            return jsonify(_branch_location_json(branch_name, message))
        session["chat_current_intent"] = "location"
        return jsonify(
            {
                "products": [],
                "message": f"ما التقطنا الفرع يا {cs._display_name()}.\n{cs._branch_selection_prompt()}",
                "branches": branch_list,
                "intent": "location",
            }
        )

    policy_precheck_r = _handle_complaint_policy_precheck_turn(message, branch_list)
    if policy_precheck_r is not None:
        return policy_precheck_r

    rule_complaint_r = _try_complaint_rule_flow(message, branch_list)
    if rule_complaint_r is not None:
        return rule_complaint_r

    wiz_resp = _try_complaint_wizard(message, branch_list)
    if wiz_resp is not None:
        return wiz_resp

    active_followup = _try_chat_active_complaint_turn(message, branch_list)
    if active_followup is not None:
        return active_followup

    pending_prod = _try_pending_product_intent_confirmation(message)
    if pending_prod is not None:
        return pending_prod

    next_rem = _try_next_remaining_product_response(message)
    if next_rem is not None:
        return next_rem

    detail_r = _try_product_detail_reply(message)
    if detail_r is not None:
        return detail_r

    sec_pick = _try_resolve_pending_section_choice(message)
    if sec_pick is not None:
        return sec_pick

    sec_fu = _try_last_section_product_followup(message)
    if sec_fu is not None:
        return sec_fu

    return None


def _dispatch_rule_based_intent(message: str, branch_list: list, decision: dict) -> Optional[Any]:
    """ردود فورية لنية rule_based (ترحيب، شكر، …) بدون OpenAI."""
    if decision.get("route") != "rule_based":
        return None
    li = decision.get("legacy_intent") or ""
    d = session.get("chat_dialect") or "default"
    dn = cs._display_name()
    if li == "greeting":
        return jsonify(
            {"products": [], "message": dialect_message(d, "greeting", name=dn), "intent": "greeting"}
        )
    if li == "thanks":
        return jsonify(
            {"products": [], "message": dialect_message(d, "thanks", name=dn), "intent": "thanks"}
        )
    if li == "goodbye":
        return jsonify(
            {"products": [], "message": dialect_message(d, "goodbye", name=dn), "intent": "goodbye"}
        )
    if li == "general":
        return jsonify(
            {"products": [], "message": dialect_message(d, "general", name=dn), "intent": "general"}
        )
    if li == "return_policy":
        session["chat_pending_action"] = None
        session["chat_current_intent"] = "return_policy"
        return jsonify(
            {
                "products": [],
                "message": _return_policy_reply_for_chat(dn, message),
                "intent": "return_policy",
            }
        )
    if li == "location_pick":
        bn = cs.resolve_branch_from_message(message)
        if bn:
            session["chat_pending_action"] = None
            session["chat_selected_branch"] = bn
            chat_ctx.remember_branch_by_name(bn)
            session["chat_current_intent"] = "location"
            return jsonify(_branch_location_json(bn, message))
        return None
    return None


def _dispatch_score_direct_intent(message: str, branch_list: list, decision: dict) -> Optional[Any]:
    """مسارات product / branch / complaint عندما تكون النقاط واضحة (بدون منسّق OpenAI)."""
    if decision.get("route") != "score_direct":
        return None
    si = decision.get("score_intent")
    if not si:
        return None

    if si == "product":
        uctx = ai_fb.extract_user_context(message)
        g = ai_fb.infer_gender_from_message(message)
        g_use = g if g in ("male", "female") else None
        widened = ai_fb.apply_shopping_context_to_search_query(message, g_use, uctx)
        psq = ai_fb.enhance_search_query_with_openai(widened, "product")
        prod = _build_products_response(psq, hint_source_message=message)
        if prod and prod.get("products"):
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            chat_ctx.set_last_intent("product")
            out = dict(prod)
            titles = [
                str(p.get("name") or "").strip()
                for p in (out.get("products") or [])
                if p.get("name")
            ]
            out["message"] = ai_fb.enrich_product_recommendation_message(
                message, uctx, out.get("message") or "", titles
            )
            logger.debug("score_direct: product DB hit from rules-only path")
            return jsonify(out)
        return None

    if si == "branch":
        t = cs.normalize_message_for_branch_search((message or "").strip())
        if any(x in t for x in kw.BRANCH_PHONE_CONTACT_TRIGGERS) and any(
            x in t for x in ("رقم", "جوال", "تواصل", "اتصل", "واتس", "فرع")
        ):
            bn = cs.resolve_branch_from_message(message) or chat_ctx.get_last_branch()
            if bn:
                chat_ctx.remember_branch_by_name(bn)
                session["chat_current_intent"] = "branch_phone"
                session["chat_pending_action"] = None
                logger.debug("score_direct: branch phone from rules-only path")
                return jsonify(branch_phone_payload(bn))
        bn = _resolve_branch_for_location(message)
        if bn:
            session["chat_pending_action"] = None
            session["chat_selected_branch"] = bn
            chat_ctx.remember_branch_by_name(bn)
            session["chat_current_intent"] = "location"
            logger.debug("score_direct: branch location from rules-only path")
            return jsonify(_branch_location_json(bn, message))
        session["chat_pending_action"] = cs._CHAT_PENDING_BRANCH
        session["chat_current_intent"] = "location"
        logger.debug("score_direct: ask branch from rules-only path")
        return jsonify(
            {
                "products": [],
                "message": f"حاضر يا {cs._display_name()}، {cs._branch_selection_prompt()}",
                "branches": branch_list,
                "intent": "location",
            }
        )

    return None


def _router_intent_branch(
    message: str,
    branch_list: list,
    pending_precalc: Any = _PENDING_PRECALC_MISSING,
) -> Any:
    """قواعد + دمج اختياري مع المنسّق (المسار الموحّد)."""
    return _route_main_chat_with_rules_and_ai(
        message, branch_list, pending_precalc=pending_precalc
    )


def dispatch_chat_query():
    """مسار /chat_query — تحليل نية أولي ثم نفس الترتيب المعتاد."""
    data = request.get_json(silent=True) or {}
    if request.form:
        for key in ("user_name", "user_contact", "message"):
            if key in request.form:
                data[key] = request.form.get(key)
        if "account_logged_in" in request.form:
            data["account_logged_in"] = request.form.get("account_logged_in")

    up = request.files.get("file")
    if up and up.filename and cs.allowed_file(up.filename):
        ext = up.filename.rsplit(".", 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        save_dir = current_app.config["UPLOAD_FOLDER"]
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, unique_name)
        up.save(path)
        proposed = (request.form.get("user_name") or data.get("user_name") or "").strip()
        account_logged_in = (request.form.get("account_logged_in") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        cs._apply_session_display_name(proposed, account_logged_in=account_logged_in)

        derived = None
        try:
            derived = attachment_openai.text_from_saved_file(path, ext)
        except Exception:
            logger.exception("attachment OpenAI processing failed")

        if (derived or "").strip():
            data["message"] = derived.strip()
        else:
            db = cs.get_db()
            is_image = ext in {"png", "jpg", "jpeg", "gif", "webp"}
            msg = (
                f"تم استلام الصورة يا {cs._display_name()}، صفّ لي طلبك أو استفسارك بالنص لأساعدك بشكل أدق."
                if is_image
                else f"تم استلام التسجيل الصوتي يا {cs._display_name()}، اكتب لي طلبك بالنص وسأساعدك."
            )
            return _finalize_chat_outputs_with_trends(
                db,
                jsonify({"products": [], "message": msg, "intent": "attachment"}),
                "",
            )

    message = (data.get("message") or "").strip()

    session["chat_dialect"] = detect_dialect(message)
    session["chat_intent_snapshot"] = pre_route_intent_snapshot(
        message, cs.resolve_branch_from_message
    )

    cust_ch.apply_request_basics(data)
    db = cs.get_db()

    mc = cust_ch.try_marketing_consent_reply(message, db)
    if mc is not None:
        logger.debug("marketing consent / consent reply path (no main router)")
        return _finalize_chat_outputs_with_trends(db, mc, message)

    cust_ch.sync_customer_from_session(db, message)

    try:
        early = _router_early_exits(data)
        if early is not None:
            if data.get("account_session_sync"):
                logger.debug("early exit: account_session_sync")
                return early
            logger.debug("early exit: greeting/attachment/name/salam/…")
            return _finalize_chat_outputs_with_trends(db, early, message)

        _maybe_reset_product_section_context(message)

        branches = db.get_all_branches()
        branch_list = [{"name": b["city_name"]} for b in branches]

        mid = _router_pending_and_services(message, branch_list)
        return _finalize_chat_outputs_with_trends(
            db,
            _router_intent_branch(message, branch_list, pending_precalc=mid),
            message,
        )
    except Exception:
        logger.exception("dispatch_chat_query failed")
        d = session.get("chat_dialect") or "default"
        return (
            jsonify(
                {
                    "products": [],
                    "message": dialect_message(
                        d, "server_error", name=cs._display_name()
                    ),
                    "intent": "error",
                }
            ),
            500,
        )
