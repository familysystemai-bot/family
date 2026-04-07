# -*- coding: utf-8 -*-
"""
توجيه طلبات الشات — ترتيب المعالجة ومسارات النية دون تغيير السلوك.
يستورد دوال الجلسة وقاعدة البيانات من chat_service.
"""
from __future__ import annotations

import os
import uuid
from typing import Any, Optional

from flask import current_app, jsonify, request, session

from config import LLM_ENABLED
from logic.branch_service import _branch_location_json, branch_phone_payload
from logic.category_service import (
    _looks_like_section_stock_question,
    _section_chat_response,
    _try_resolve_pending_section_choice,
)
from logic.complaint_service import (
    _handle_complaint_policy_precheck_turn,
    _handle_new_complaint_intent,
    _try_chat_active_complaint_turn,
    _try_complaint_wizard,
)
from logic import ai_fallback as ai_fb
from logic.ai_fallback import generate_ai_response
from logic.dialect_detector import detect_dialect
from logic.dialect_responses import dialect_message
from logic.llm_analyzer import analyze_user_message
from logic.intent_handler import detect_chat_intent
from logic.product_service import (
    _build_products_response,
    _build_search_text_from_llm,
    _recommendation_response,
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

def _try_openai_fallback(message: str, intent: str) -> Optional[Any]:
    """رد OpenAI الاحتياطي — فقط عند السماح ولوجود مفتاح."""
    if not ai_fb.is_ai_fallback_allowed(message, intent):
        return None
    db = cs.get_db()
    db_data = ai_fb.build_fallback_db_data(db, message)
    # أولوية لعرض منتجات حقيقية من DB إذا وُجدت مطابقة (حتى لو كانت قليلة)
    if db_data.get("products"):
        prod = _build_products_response(message)
        if prod:
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

# نيات تدل على خروج واضح من سياق المنتج/القسم (موقع، شكوى، سياسة، إلخ.)
_CHAT_RESET_PRODUCT_SECTION_INTENTS = frozenset(
    {
        "location",
        "location_pick",
        "branch_phone",
        "complaint",
        "return_policy",
        "greeting",
        "thanks",
        "goodbye",
        "recommendation",
    }
)


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
    intent = detect_chat_intent(message, cs.resolve_branch_from_message)
    if intent not in _CHAT_RESET_PRODUCT_SECTION_INTENTS:
        return
    session.pop("chat_last_product", None)
    session.pop("last_products", None)
    session.pop("last_section", None)
    session.pop("pending_section_choices", None)
    session.pop("pending_product_intent", None)
    session.pop("remaining_products", None)
    session.pop("remaining_products_intent", None)


def _apply_llm_classification(parsed: dict, original_message: str, branch_list: list):
    """
    يوجّه حسب مخرجات المحلل فقط. يعيد كائن الاستجابة من jsonify أو None.
    """
    if not parsed:
        return None
    ai = (parsed.get("intent") or "unknown").strip().lower()
    cleaned = (parsed.get("cleaned_message") or "").strip()
    branch_hint = parsed.get("branch")
    keywords = parsed.get("keywords") or []

    base_msg = cleaned if cleaned else original_message
    composite = base_msg
    if branch_hint:
        hint = str(branch_hint).strip()
        if hint and hint not in composite:
            composite = f"{composite} {hint}".strip()

    if ai == "complaint":
        session["chat_pending_action"] = None
        return _handle_new_complaint_intent(composite, branch_list)

    if ai in ("branch", "location"):
        session["chat_current_intent"] = "location"
        branch_name = _resolve_branch_for_location(composite)
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
        return jsonify(_branch_location_json(branch_name, composite))

    if ai == "product":
        needle = _build_search_text_from_llm(cleaned, keywords, original_message)
        prod = _build_products_response(needle)
        if prod:
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            return jsonify(prod)
        prod = _build_products_response(original_message)
        if prod:
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            return jsonify(prod)
        return None

    return None


def _try_llm_fallback_route(message: str, branch_list: list):
    """محاولة واحدة: محلل LLM ثم توجيه للنظام الحالي."""
    if not LLM_ENABLED:
        return None
    try:
        parsed = analyze_user_message(message)
    except Exception:
        return None
    if not parsed:
        return None
    return _apply_llm_classification(parsed, message, branch_list)


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
    """تحليل النية ثم التوجيه حسب intent."""
    intent = detect_chat_intent(message, cs.resolve_branch_from_message)

    if _looks_like_section_stock_question(message):
        session["chat_current_intent"] = "section"
        session["chat_pending_action"] = None
        return _section_chat_response(message)

    if intent == "greeting":
        session.pop("complaint_wizard", None)
        session["chat_pending_action"] = None
        session["chat_current_intent"] = "greeting"
        chat_ctx.set_last_intent("greeting")
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(
                    d, "greeting", name=cs._display_name()
                ),
                "intent": "greeting",
            }
        )

    if intent in ("thanks", "goodbye"):
        session["chat_pending_action"] = None
        session["chat_current_intent"] = intent
        d = session.get("chat_dialect") or "default"
        key = "thanks" if intent == "thanks" else "goodbye"
        msg = dialect_message(d, key, name=cs._display_name())
        resp = jsonify({"products": [], "message": msg, "intent": intent})
        cust_ch.prepare_closing_marketing_offer(cs.get_db())
        return cust_ch.attach_marketing_followup_if_needed(resp)

    if intent == "complaint":
        session["chat_pending_action"] = None
        return _handle_new_complaint_intent(message, branch_list)

    if intent == "return_policy":
        session["chat_pending_action"] = None
        session["chat_current_intent"] = "return_policy"
        return jsonify(
            {
                "products": [],
                "message": build_return_policy_chat_message(cs._display_name(), message),
                "intent": "return_policy",
            }
        )

    if intent == "branch_phone":
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
                msg = f"أي فرع تقصد؟ {labels[0]} ولا {labels[1]}؟"
            elif len(labels) == 1:
                msg = f"تقصد فرع {labels[0]}؟ أكّد لي اسم الفرع عشان أرسل لك رقم التواصل."
            else:
                msg = f"أي فرع تقصد يا {cs._display_name()}؟ {cs._branch_selection_prompt()}"
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

    if intent == "location":
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

    if intent == "location_pick":
        session["chat_current_intent"] = "location"
        bn = _resolve_branch_for_location(message)
        if not bn:
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
        session["chat_selected_branch"] = bn
        chat_ctx.remember_branch_by_name(bn)
        return jsonify(_branch_location_json(bn, message))

    if intent == "section":
        session["chat_current_intent"] = "section"
        session["chat_pending_action"] = None
        return _section_chat_response(message)

    if intent == "recommendation":
        session["chat_current_intent"] = "recommendation"
        session["chat_pending_action"] = None
        return _recommendation_response()

    if intent == "general":
        session["chat_current_intent"] = "general"
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(d, "general", name=cs._display_name()),
                "intent": "general",
            }
        )

    if intent == "unknown":
        session["chat_current_intent"] = "unknown"
        # DB أولاً: استعلام المنتجات قبل LLM/AI (كلمات مثل تشيرت قد لا تُصنَّف كـ product)
        prod_db = _build_products_response(message)
        if prod_db:
            session["chat_current_intent"] = "product"
            session["chat_pending_action"] = None
            chat_ctx.set_last_intent("product")
            return jsonify(prod_db)
        r = _try_llm_fallback_route(message, branch_list)
        if r is not None:
            return r
        if ai_fb.is_ai_fallback_allowed(message, "unknown"):
            db_data = ai_fb.build_fallback_db_data(cs.get_db(), message)
            if db_data.get("products"):
                prod = _build_products_response(message)
                if prod:
                    session["chat_current_intent"] = "product"
                    session["chat_pending_action"] = None
                    chat_ctx.set_last_intent("product")
                    return jsonify(prod)
            ai_reply = generate_ai_response(message, db_data)
            if ai_reply:
                return jsonify(
                    {
                        "products": [],
                        "message": ai_reply,
                        "intent": "unknown",
                    }
                )
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(d, "unknown_fallback"),
                "intent": "unknown",
            }
        )

    session["chat_current_intent"] = "product"
    session["chat_pending_action"] = None
    chat_ctx.set_last_intent("product")
    prod = _build_products_response(message)
    if not prod:
        ai_r = _try_openai_fallback(message, "product")
        if ai_r is not None:
            return ai_r
        d = session.get("chat_dialect") or "default"
        return jsonify(
            {
                "products": [],
                "message": dialect_message(d, "product_fallback"),
                "intent": "product",
            }
        )
    return jsonify(prod)


def dispatch_chat_query():
    """مسار /chat_query — نفس الترتيب والردود السابقة."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session["chat_dialect"] = detect_dialect(message)

    cust_ch.apply_request_basics(data)
    db = cs.get_db()

    mc = cust_ch.try_marketing_consent_reply(message, db)
    if mc is not None:
        return mc

    cust_ch.sync_customer_from_session(db, message)

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
