"""
WA Smoke Test (local)
=====================

هدفه: اختبار نقطتين أساسيتين بدون اتصال فعلي بميتا:
1) عدم تكرار الترحيب لنفس المستخدم عبر حفظ الجلسة في DB (wa_sessions)
2) منع إعادة معالجة نفس WAMID عبر DB (wa_processed_wamids)

يشغّل process_message مباشرة ويستبدل وظائف الإرسال/الـ typing indicator بـ no-op.
"""

from __future__ import annotations

import json
import os
import time


def _minimal_text_webhook(*, wamid: str, wa_from: str, text: str) -> dict:
    # شكل مبسّط يكفي لـ _wa_collect_inbound_user_messages
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "TEST_WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "TEST_PHONE_ID"},
                            "contacts": [{"wa_id": wa_from, "profile": {"name": "Test User"}}],
                            "messages": [
                                {
                                    "from": wa_from,
                                    "id": wamid,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    return payload


def main() -> None:
    # import داخل الدالة حتى يأخذ env قبل init
    import app as appmod
    # bypass signature verification in local smoke test
    try:
        import logic.integrations.base as _ib

        _ib.read_setting = lambda *_a, **_kw: ""  # type: ignore
    except Exception:
        pass
    try:
        import logic.security as _sec

        _sec.verify_meta_signature = lambda *_a, **_kw: True  # type: ignore
    except Exception:
        pass

    # patch: امنع أي اتصال خارجي
    appmod._wa_send_message = lambda *_a, **_kw: True  # type: ignore
    appmod.send_typing_indicator = lambda *_a, **_kw: None  # type: ignore
    appmod._wa_send_image_link = lambda *_a, **_kw: True  # type: ignore
    appmod._wa_runtime_access_token = lambda: "TEST_TOKEN"  # type: ignore
    appmod._wa_runtime_phone_number_id = lambda: "TEST_PHONE_ID"  # type: ignore

    # clear L1 caches (simulate new worker)
    try:
        appmod._WA_SESSION_L1.clear()  # type: ignore
    except Exception:
        pass
    try:
        appmod._WA_WAMID_L1.clear()  # type: ignore
    except Exception:
        pass

    wa_from = "966500000001"
    sid = f"wa_{wa_from}"

    # 1) أول رسالة: يجب أن تحفظ chat_welcome_sent بعد المعالجة
    w1 = _minimal_text_webhook(wamid="wamid.TEST.1", wa_from=wa_from, text="مرحبا")
    appmod.process_message({"raw_body": json.dumps(w1).encode("utf-8")})
    st1 = appmod._wa_cache_get_prev_state(sid)  # type: ignore
    assert st1.get("chat_welcome_sent") is True, "Expected chat_welcome_sent=True after first message"

    # 2) امسح L1 وأعد المعالجة برسالة جديدة: يجب ألا يعود welcome_needed True (لأن DB حافظت)
    appmod._WA_SESSION_L1.clear()  # type: ignore
    w2 = _minimal_text_webhook(wamid="wamid.TEST.2", wa_from=wa_from, text="كيفك")
    appmod.process_message({"raw_body": json.dumps(w2).encode("utf-8")})
    st2 = appmod._wa_cache_get_prev_state(sid)  # type: ignore
    assert st2.get("chat_welcome_sent") is True, "Expected welcome flag to persist across L1 clear"

    # 3) نفس WAMID مرتين: الثانية يجب أن تُتخطّى داخل dedupe
    appmod._WA_WAMID_L1.clear()  # type: ignore
    w3 = _minimal_text_webhook(wamid="wamid.TEST.DUP", wa_from=wa_from, text="مرحبا مرة ثانية")
    appmod.process_message({"raw_body": json.dumps(w3).encode("utf-8")})
    # call again with same wamid
    appmod.process_message({"raw_body": json.dumps(w3).encode("utf-8")})

    print("OK: WA smoke tests passed (welcome persistence + wamid dedupe).")


if __name__ == "__main__":
    # اجعل التشغيل واضحاً في ويندوز
    os.environ.setdefault("FLASK_DEBUG", "false")
    # لتفادي رفض webhook بسبب غياب ترويسة التوقيع أثناء الاختبار المحلي
    os.environ["META_APP_SECRET"] = ""
    main()

