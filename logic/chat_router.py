# -*- coding: utf-8 -*-
"""
توجيه طلبات الشات — ترتيب المعالجة ومسارات النية دون تغيير السلوك.
يستورد دوال الجلسة وقاعدة البيانات من chat_service.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from flask import current_app, jsonify, request, session

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
    _try_complaint_wizard,
)
from logic import ai_fallback as ai_fb
from logic.dialect_detector import detect_dialect
from logic.dialect_responses import dialect_message
from logic.intent_handler import user_wants_open_now
from logic.product_query_parse import normalize_for_product_search
from logic.product_service import (
    _build_products_response,
    _looks_like_next_product_request,
    _try_last_section_product_followup,
    _try_next_remaining_product_response,
    _try_pending_product_intent_confirmation,
    _try_product_detail_reply,
)
from site_config.company_policies import build_return_policy_chat_message
from site_config.founder_attribution import founder_attribution_payload_if_asked

import logic.chat_service as cs
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


def _try_openai_fallback(message: str, intent: str) -> Optional[Any]:
    """رد OpenAI الاحتياطي — فقط عند السماح ولوجود مفتاح."""
    if not ai_fb.is_ai_fallback_allowed(message, intent):
        return None
    search_q = ai_fb.enhance_search_query_with_openai(message, intent)
    db = cs.get_db()
    db_data = ai_fb.build_fallback_db_data(db, search_q)
    # أولوية لعرض منتجات حقيقية من DB إذا وُجدت مطابقة (حتى لو كانت قليلة)
    if db_data.get("products"):
        prod = _build_products_response(search_q, hint_source_message=message)
        if prod and prod.get("products"):
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            chat_ctx.set_last_intent("product")
            return jsonify(prod)
    text = ai_fb.generate_ai_response(message, db_data)
    if not text:
        return None
    session["chat_current_intent"] = intent
    return jsonify(
        {
            "products": [],
            "message": text,
            "intent": intent,
        }
    )


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


def _orchestrator_hard_fallback(message: str, branch_list: list) -> Any:
    """عند تعطيل/فشل المنسّق: بحث منتج ثم طبقة OpenAI الاحتياطية."""
    uctx = ai_fb.extract_user_context(message)
    g = ai_fb.infer_gender_from_message(message)
    g_use = g if g in ("male", "female") else None
    widened = ai_fb.apply_shopping_context_to_search_query(message, g_use, uctx)
    psq = ai_fb.enhance_search_query_with_openai(widened, "unknown")
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
        return jsonify(out)
    oai = _try_openai_fallback(message, "unknown")
    if oai is not None:
        return oai
    d = session.get("chat_dialect") or "default"
    return jsonify(
        {
            "products": [],
            "message": dialect_message(d, "unknown_fallback"),
            "intent": "unknown",
        }
    )


def _execute_ai_orchestrator(message: str, branch_list: list) -> Any:
    db = cs.get_db()
    context = ai_fb.build_orchestrator_context(
        db, message, session.get("chat_dialect")
    )
    plan = ai_fb.run_chat_orchestrator_openai(message, context)
    if not plan:
        return _orchestrator_hard_fallback(message, branch_list)

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
        br = cs.resolve_branch_from_message(message) or chat_ctx.get_last_branch()
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
                "message": build_return_policy_chat_message(cs._display_name(), message),
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
            msg_out = dialect_message(d, "general", name=cs._display_name())
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
            final_msg = dialect_message(d, "unknown_fallback")
        final_msg = ai_fb.contextualize_no_product_message(
            final_msg, message, uctx, cats
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
            final_msg = dialect_message(
                session.get("chat_dialect") or "default", "unknown_fallback"
            )
        final_msg = ai_fb.contextualize_no_product_message(
            final_msg, message, uctx, cats
        )
        return jsonify({"products": [], "message": final_msg, "intent": "general"})

    return _orchestrator_hard_fallback(message, branch_list)


def _router_early_exits(data: dict) -> Optional[Any]:
    """account_session_sync، مرفق، مؤسس، جمع الاسم."""
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

    up = request.files.get("file")
    if up and up.filename and cs.allowed_file(up.filename):
        ext = up.filename.rsplit(".", 1)[1].lower()
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        up.save(os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name))
        proposed = (request.form.get("user_name") or "").strip()
        account_logged_in = (request.form.get("account_logged_in") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        cs._apply_session_display_name(proposed, account_logged_in=account_logged_in)
        is_image = ext in {"png", "jpg", "jpeg", "gif", "webp"}
        msg = (
            f"تم استلام الصورة يا {cs._display_name()}، صفّ لي طلبك أو استفسارك بالنص لأساعدك بشكل أدق."
            if is_image
            else f"تم استلام التسجيل الصوتي يا {cs._display_name()}، اكتب لي طلبك بالنص وسأساعدك."
        )
        return jsonify({"products": [], "message": msg, "intent": "attachment"})

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


def _router_intent_branch(message: str, branch_list: list) -> Any:
    """قواعد ضيقة (أقسام، فرع/دوام) ثم المنسّق الذكي لباقي الرسائل."""
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

    return _execute_ai_orchestrator(message, branch_list)


def dispatch_chat_query():
    """مسار /chat_query — تحليل نية أولي ثم نفس الترتيب المعتاد."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session["chat_dialect"] = detect_dialect(message)
    session["chat_intent_snapshot"] = {
        "primary_intent": "ai_orchestrator",
        "product_sub_intent": (
            "product_followup" if _looks_like_next_product_request(message) else "product_search"
        ),
        "normalized_for_search": normalize_for_product_search(message),
    }

    cust_ch.apply_request_basics(data)
    db = cs.get_db()

    mc = cust_ch.try_marketing_consent_reply(message, db)
    if mc is not None:
        return mc

    cust_ch.sync_customer_from_session(db, message)

    try:
        early = _router_early_exits(data)
        if early is not None:
            if data.get("account_session_sync"):
                return early
            return cust_ch.attach_marketing_followup_if_needed(early)

        _maybe_reset_product_section_context(message)

        branches = db.get_all_branches()
        branch_list = [{"name": b["city_name"]} for b in branches]

        mid = _router_pending_and_services(message, branch_list)
        if mid is not None:
            return cust_ch.attach_marketing_followup_if_needed(_maybe_enrich_json_response(mid))

        return cust_ch.attach_marketing_followup_if_needed(
            _maybe_enrich_json_response(_router_intent_branch(message, branch_list))
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
