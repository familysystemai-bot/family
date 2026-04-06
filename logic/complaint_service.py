# -*- coding: utf-8 -*-
"""
منطق الشكاوى في الشات (wizard، تصنيف، بريد، سياسة ما قبل التسجيل).
يُستدعى من chat_service — استيراد chat_service داخل الدوال لتفادي الدائرة.
"""
from __future__ import annotations

from datetime import datetime

from flask import jsonify, session

from config import MAIN_RECEIVER_EMAIL
from logic.complaint_classifier import classify_complaint_issue, complaint_type_label_ar
from logic.mail_service import send_email
from logic.branch_service import _branch_location_json
from logic.product_service import _PRODUCT_UNAVAILABLE_MSG, _build_products_response
from logic import chat_context as chat_ctx
from logic.chat_handlers.complaint_handler import random_opening_apology, success_message
from site_config.branches import get_branch, get_management_emails
from site_config.company_policies import build_return_policy_complaint_precheck_summary


def _cs():
    import logic.chat_service as m

    return m


# حوار شكوى متعدد الخطوات (جمع التفاصيل + الفرع)
_MIN_COMPLAINT_DETAIL = 22

# كلمات تربط الشكوى بسياسة الاستبدال/الاسترجاع → ملخص سياسة قبل التسجيل
_COMPLAINT_RETURN_POLICY_KW = (
    "استبدال",
    "استرجاع",
    "رفضوا",
    "ما يقبلون",
)

# خروج صريح من وضع الشكوى فقط بهذه الصياغ
_EXPLICIT_PRODUCT_SWITCH = (
    "أبغى منتج",
    "ابغى منتج",
    "أبي منتج",
    "ابي منتج",
    "ابغي منتج",
    "أريد منتج",
    "اريد منتج",
    "دور على منتج",
    "ابغى اشتري",
    "ابغي اشتري",
    "ابي اشتري",
    "أبحث عن منتج",
)
_EXPLICIT_LOCATION_SWITCH = (
    "فين الموقع",
    "وين الموقع",
    "ابغى الموقع",
    "أبغى الموقع",
    "ابي الموقع",
    "موقع الفرع",
    "ابغى العنوان",
    "أبغى العنوان",
    "وين الفرع",
    "فين الفرع",
)


def _complaint_type_fields(complaint_type: str) -> dict:
    ct = (complaint_type or "unspecified").strip() or "unspecified"
    return {
        "complaint_type": ct,
        "complaint_type_label": complaint_type_label_ar(ct),
    }


def _explicit_switch_from_complaint(message: str):
    """أثناء الشكوى: الخروج لمسار منتج/موقع فقط بعبارات صريحة."""
    t = (message or "").strip()
    if any(p in t for p in _EXPLICIT_PRODUCT_SWITCH):
        return "product"
    if any(p in t for p in _EXPLICIT_LOCATION_SWITCH):
        return "location"
    return None


def _exit_complaint_mode_after_successful_submit():
    """بعد حفظ الشكوى بنجاح: إنهاء وضع الشكوى حتى لا تُعالَج الرسائل التالية كملاحقات."""
    session.pop("complaint_wizard", None)
    session.pop("chat_active_complaint_id", None)
    session.pop("complaint_branch_label", None)
    session["chat_current_intent"] = None


def _send_complaint_email(complaint_id, branch_label, issue_text, is_append=False, branch_id=None):
    """إرسال بريد: MAIN_RECEIVER_EMAIL + إيميل الفرع (من قاعدة البيانات) + إداريون اختياريون."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kind = "تحديث شكوى" if is_append else "شكوى جديدة"
    subject = f"{kind} (#{complaint_id})"
    crow = get_db().get_complaint_row(complaint_id)
    ct_label = complaint_type_label_ar((crow or {}).get("complaint_type") or "unspecified")
    body = (
        f"{kind}\n\n"
        f"نص الشكوى:\n{issue_text}\n\n"
        f"تصنيف الشكوى: {ct_label}\n"
        f"اسم الفرع: {branch_label or 'غير محدد'}\n"
        f"اسم العميل (محادثة): {_display_name()}\n"
        f"الوقت: {now}\n"
    )
    recipients = []
    if MAIN_RECEIVER_EMAIL:
        recipients.append(MAIN_RECEIVER_EMAIL.strip())

    branch_email = None
    if branch_id is not None:
        branch_email = get_db().get_branch_complaint_email(branch_id)
    if not branch_email and branch_label:
        bid = get_db().get_branch_id_by_city_name(branch_label)
        if bid is not None:
            branch_email = get_db().get_branch_complaint_email(bid)
    if not branch_email and branch_label:
        contact = get_branch(branch_label) or {}
        branch_email = (contact.get("manager_email") or "").strip() or None

    if branch_email:
        recipients.append(branch_email)

    recipients.extend(get_management_emails())
    recipients = list(dict.fromkeys([r for r in recipients if r]))
    ok = send_email(recipients, subject, body)
    if not ok:
        print(
            f"❌ فشل إرسال بريد الشكوى #{complaint_id} — "
            "تحقق من SENDER_EMAIL و SENDER_PASSWORD (App Password) في .env"
        )
    return ok


def _customer_contact_from_session():
    raw = (session.get("user_contact") or "").strip()
    phone, email = None, None
    if raw:
        if "@" in raw:
            email = raw[:320]
        else:
            phone = raw[:64]
    name = (session.get("user_name") or "").strip()
    if not name or name == "حضرتك":
        name = None
    return name, phone, email


def _submit_new_complaint(message: str, branch_name_override=None):
    """إنشاء سجل شكوى جديد + إشعار بريد. يعيد (complaint_id, branch_name, خطأ_للعرض, email_ok)."""
    cs = _cs()
    get_db = cs.get_db
    extract_branch_name = cs.extract_branch_name
    _display_name = cs._display_name
    branch_name = branch_name_override
    if branch_name is None:
        branch_name = extract_branch_name(message)
    branch_id = get_db().get_branch_id_by_city_name(branch_name) if branch_name else None
    nm = _display_name()
    message_plain = (message or "").strip()
    issue_body = f"[العميل: {nm}]\n{message_plain}" if nm != "حضرتك" else message_plain
    ctype = classify_complaint_issue(issue_body)
    branch_display = (branch_name or "").strip()
    if not branch_display and branch_id:
        br = get_db().get_branch_by_id(int(branch_id))
        if br:
            branch_display = (br.get("city_name") or "").strip()
    cust_name, cust_phone, cust_email = _customer_contact_from_session()
    complaint_id = get_db().add_complaint(
        user_id=session.get("user_id", "web_user_unknown"),
        issue=issue_body,
        branch_id=branch_id,
        status="open",
        complaint_type=ctype,
        message=message_plain,
        branch_name=branch_display or None,
        customer_name=cust_name,
        customer_phone=cust_phone,
        customer_email=cust_email,
    )
    if complaint_id is None:
        print("❌ لم يُحفظ سجل الشكوى — أرجِع add_complaint بقيمة None")
        return None, branch_name, "تعذر حفظ الشكوى حالياً، حاول بعد قليل.", None, None
    row = get_db().get_complaint_row(complaint_id)
    full_issue = row["issue"] if row else issue_body
    email_ok = _send_complaint_email(
        complaint_id, branch_name or "", full_issue, is_append=False, branch_id=branch_id
    )
    return complaint_id, branch_name, None, email_ok, ctype


def _complaint_mentions_return_policy(text: str) -> bool:
    t = text or ""
    return any(k in t for k in _COMPLAINT_RETURN_POLICY_KW)


def _user_confirms_complaint_after_policy(msg: str) -> bool:
    m = (msg or "").strip()
    if not m:
        return False
    ml = m.lower()
    if "لا" in ml[:8] or ml.startswith("لأ"):
        return False
    if any(k in m for k in ("سجل الشكوى", "سجّل الشكوى", "تسجيل شكوى")):
        return True
    if len(m) <= 40:
        if any(w in ml for w in ("نعم", "ايوه", "أيوة", "أكد", "اكد", "كمل", "أكمل", "اكمل", "واضح")):
            return True
        if ml in ("تم", "اوكي", "أوكي", "ok", "yes"):
            return True
    return False


def _user_cancels_policy_precheck(msg: str) -> bool:
    m = (msg or "").strip().lower()
    if not m or len(m) > 56:
        return False
    if m in ("تمام", "شكرا", "شكراً", "الله يعطيك العافية", "يعطيك العافية"):
        return True
    if m.startswith("لا") or m.startswith("لأ"):
        return True
    if "لا شكر" in m or "لا أبغى" in m or "لا ابغى" in m or "ملغي" in m or "إلغاء" in m:
        return True
    return False


def _handle_complaint_policy_precheck_turn(message: str, branch_list: list):
    """خطوة التأكيد بعد عرض ملخص السياسة."""
    cs = _cs()
    _display_name = cs._display_name
    _branch_label_for_chat = cs._branch_label_for_chat
    cp = session.get("complaint_policy_precheck")
    if not cp:
        return None
    msg = (message or "").strip()
    if _user_cancels_policy_precheck(msg):
        session.pop("complaint_policy_precheck", None)
        return jsonify(
            {
                "products": [],
                "message": f"تمام يا {_display_name()}، إذا احتجتنا نحن موجودين.",
                "intent": "general",
            }
        )
    if _user_confirms_complaint_after_policy(msg):
        issue = (cp.get("issue") or "").strip()
        br = cp.get("branch")
        session.pop("complaint_policy_precheck", None)
        if not issue:
            return jsonify(
                {
                    "products": [],
                    "message": "صار لغبطة بسيطة، اكتب نص الشكوى مرة ثانية.",
                    "intent": "general",
                }
            )
        complaint_id, branch_name, err, email_ok, ctype = _submit_new_complaint(
            issue, branch_name_override=br
        )
        if err:
            return jsonify({"products": [], "message": err, "intent": "complaint"})
        _exit_complaint_mode_after_successful_submit()
        msg_ok = success_message()
        if email_ok is False:
            msg_ok += " (تعذر إرسال نسخة البريد؛ تم حفظ الشكوى في النظام.)"
        payload = {
            "products": [],
            "message": msg_ok,
            "complaint_id": complaint_id,
            "intent": "complaint",
            "email_sent": bool(email_ok),
        }
        payload.update(_complaint_type_fields(ctype or "unspecified"))
        return jsonify(payload)

    return jsonify(
        {
            "products": [],
            "message": (
                f"يا {_display_name()}، عشان أسجّل الشكوى اكتب **نعم** أو **سجّل الشكوى**. "
                "ولو ما تحتاج، قل لا أو تمام."
            ),
            "intent": "complaint_policy_precheck",
        }
    )


def _try_complaint_wizard(message: str, branch_list: list):
    """
    متابعة حوار جمع الشكوى (تفاصيل + فرع) بأسلوب بشري.
    يُرجع jsonify(...) أو None إن لم يكن هناك معالج نشط.
    """
    cs = _cs()
    resolve_branch_from_message = cs.resolve_branch_from_message
    get_db = cs.get_db
    _display_name = cs._display_name
    _branch_selection_prompt = cs._branch_selection_prompt
    _CHAT_PENDING_BRANCH = cs._CHAT_PENDING_BRANCH
    w = session.get("complaint_wizard")
    if not w:
        return None
    msg = (message or "").strip()

    apology_merge = bool(w.pop("_apology_turn_merged", False))

    if w.get("apology_sent"):
        prev = (w.get("issue") or "").strip()
        merged_issue = f"{prev}\n{msg}".strip() if prev else msg
        br = (
            resolve_branch_from_message(msg)
            or resolve_branch_from_message(merged_issue)
            or w.get("branch")
            or chat_ctx.get_last_branch()
        )
        if br:
            chat_ctx.remember_branch_by_name(br)
        w = {
            **w,
            "apology_sent": False,
            "issue": merged_issue,
            "branch": br,
            "step": w.get("step") or "collecting",
            "_apology_turn_merged": True,
        }
        session["complaint_wizard"] = w
        apology_merge = True

    sw = _explicit_switch_from_complaint(msg)
    if sw == "product":
        session.pop("complaint_wizard", None)
        session["chat_pending_action"] = None
        prod = _build_products_response(msg)
        if not prod:
            return jsonify(
                {
                    "products": [],
                    "message": _PRODUCT_UNAVAILABLE_MSG,
                    "intent": "product",
                }
            )
        return jsonify(prod)
    if sw == "location":
        session.pop("complaint_wizard", None)
        bn = resolve_branch_from_message(msg)
        if not bn:
            session["chat_pending_action"] = _CHAT_PENDING_BRANCH
            session["chat_current_intent"] = "location"
            return jsonify(
                {
                    "products": [],
                    "message": f"حاضر يا {_display_name()}، {_branch_selection_prompt()}",
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = bn
        session["chat_current_intent"] = "location"
        return jsonify(_branch_location_json(bn, msg))

    w = session.get("complaint_wizard") or {}
    prev_issue = (w.get("issue") or "").strip()
    prev_branch = w.get("branch")
    step = w.get("step") or "collecting"

    if step == "need_branch":
        issue_text = prev_issue
        br = (
            resolve_branch_from_message(msg)
            or prev_branch
            or chat_ctx.get_last_branch()
        )
    else:
        if apology_merge:
            issue_text = prev_issue
            br = (
                w.get("branch")
                or resolve_branch_from_message(msg)
                or resolve_branch_from_message(issue_text)
                or prev_branch
                or chat_ctx.get_last_branch()
            )
        else:
            issue_text = f"{prev_issue}\n{msg}".strip() if prev_issue else msg
            br = (
                resolve_branch_from_message(msg)
                or resolve_branch_from_message(issue_text)
                or prev_branch
                or chat_ctx.get_last_branch()
            )
    if br:
        chat_ctx.remember_branch_by_name(br)

    detail_ok = len(issue_text.strip()) >= _MIN_COMPLAINT_DETAIL
    branch_ok = bool(br) and get_db().get_branch_id_by_city_name(br) is not None

    if detail_ok and not branch_ok:
        session["complaint_wizard"] = {
            "step": "need_branch",
            "issue": issue_text,
            "branch": None,
        }
        session["chat_pending_action"] = None
        return jsonify(
            {
                "products": [],
                "message": "في أي فرع حصلت المشكلة؟",
                "intent": "complaint_wizard",
                "branches": branch_list,
            }
        )

    if not detail_ok:
        session["complaint_wizard"] = {
            "step": "collecting",
            "issue": issue_text,
            "branch": br,
        }
        return jsonify(
            {
                "products": [],
                "message": "ممكن توضح المشكلة أكثر؟",
                "intent": "complaint_wizard",
                "branches": branch_list,
            }
        )

    if _complaint_mentions_return_policy(issue_text):
        session["complaint_policy_precheck"] = {"issue": issue_text, "branch": br}
        session.pop("complaint_wizard", None)
        return jsonify(
            {
                "products": [],
                "message": build_return_policy_complaint_precheck_summary(_display_name()),
                "intent": "complaint_policy_precheck",
            }
        )

    session.pop("complaint_wizard", None)
    complaint_id, branch_name, err, email_ok, ctype = _submit_new_complaint(
        issue_text, branch_name_override=br
    )
    if err:
        return jsonify({"products": [], "message": err, "intent": "complaint"})
    _exit_complaint_mode_after_successful_submit()
    msg_ok = success_message()
    if email_ok is False:
        msg_ok += " (تعذر إرسال نسخة البريد؛ تم حفظ الشكوى في النظام.)"
    payload = {
        "products": [],
        "message": msg_ok,
        "complaint_id": complaint_id,
        "intent": "complaint",
        "email_sent": bool(email_ok),
    }
    payload.update(_complaint_type_fields(ctype or "unspecified"))
    return jsonify(payload)


def _handle_new_complaint_intent(message: str, branch_list: list):
    """أول رد: اعتذار فقط؛ تُستكمل التفاصيل في complaint_wizard."""
    cs = _cs()
    resolve_branch_from_message = cs.resolve_branch_from_message
    msg = (message or "").strip()
    br = resolve_branch_from_message(msg) or chat_ctx.get_last_branch()
    if br:
        chat_ctx.remember_branch_by_name(br)
    session["complaint_wizard"] = {
        "apology_sent": True,
        "issue": msg,
        "branch": br,
        "step": "collecting",
    }
    return jsonify(
        {
            "products": [],
            "message": random_opening_apology(),
            "intent": "complaint_wizard",
            "branches": branch_list,
        }
    )


def _try_chat_active_complaint_turn(message: str, branch_list: list):
    """متابعة شكوى نشطة (chat_active_complaint_id): ملاحقات أو خروج لمنتج/موقع."""
    cs = _cs()
    get_db = cs.get_db
    _display_name = cs._display_name
    resolve_branch_from_message = cs.resolve_branch_from_message
    _branch_selection_prompt = cs._branch_selection_prompt
    _CHAT_PENDING_BRANCH = cs._CHAT_PENDING_BRANCH
    cid = session.get("chat_active_complaint_id")
    if session.get("chat_current_intent") != "complaint" or not cid:
        return None
    sw = _explicit_switch_from_complaint(message)
    if sw == "product":
        session.pop("chat_active_complaint_id", None)
        session.pop("complaint_branch_label", None)
        session["chat_current_intent"] = None
        prod = _build_products_response(message)
        if not prod:
            return jsonify(
                {
                    "products": [],
                    "message": _PRODUCT_UNAVAILABLE_MSG,
                    "intent": "product",
                }
            )
        return jsonify(prod)
    if sw == "location":
        session.pop("chat_active_complaint_id", None)
        session.pop("complaint_branch_label", None)
        branch_name = resolve_branch_from_message(message)
        if not branch_name:
            session["chat_pending_action"] = _CHAT_PENDING_BRANCH
            session["chat_current_intent"] = "location"
            return jsonify(
                {
                    "products": [],
                    "message": f"حاضر يا {_display_name()}، {_branch_selection_prompt()}",
                    "branches": branch_list,
                    "intent": "location",
                }
            )
        session["chat_pending_action"] = None
        session["chat_selected_branch"] = branch_name
        session["chat_current_intent"] = "location"
        return jsonify(_branch_location_json(branch_name, message))
    if not get_db().append_complaint_issue(cid, message):
        return jsonify(
            {
                "products": [],
                "message": f"يا {_display_name()}، حصل خطأ أثناء حفظ الملاحظة. حاول مرة ثانية.",
                "intent": "complaint",
            }
        )
    row = get_db().get_complaint_row(cid)
    full_issue = row["issue"] if row else message
    ct = classify_complaint_issue(full_issue)
    get_db().update_complaint_type(cid, ct)
    bl = session.get("complaint_branch_label") or ""
    bid = row["branch_id"] if row else None
    email_ok = _send_complaint_email(cid, bl, full_issue, is_append=True, branch_id=bid)
    note = f"تم تسجيل ملاحظتك يا {_display_name()}، وتم إيصالها للفريق."
    if email_ok is False:
        note += " (تعذر إرسال التنبيه بالبريد؛ تم حفظ الملاحظة في النظام.)"
    payload = {
        "products": [],
        "message": note,
        "complaint_id": cid,
        "intent": "complaint",
        "email_sent": bool(email_ok),
    }
    payload.update(_complaint_type_fields(ct))
    return jsonify(payload)
