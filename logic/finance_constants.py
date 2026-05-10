# -*- coding: utf-8 -*-
"""مفاتيح تخزين مركز المناخ المالي — قيم ضمن system_settings."""

KEY_SETUP_COMPLETE = "finance_vault_setup_complete"
KEY_PIN_HASH = "finance_vault_pin_hash"
KEY_RECOVERY_EMAIL_CIPHER = "finance_vault_recovery_email_aes"
KEY_AMAZON_BASE_CIPHER = "finance_amazon_base_url_aes"
KEY_AMAZON_API_KEY_CIPHER = "finance_amazon_api_key_aes"
KEY_AMAZON_SECRET_CIPHER = "finance_amazon_secret_aes"
KEY_AI_PROVIDER = "finance_ai_provider"  # openai | anthropic
KEY_AI_MODEL = "finance_ai_model"
KEY_AI_API_KEY_CIPHER = "finance_ai_api_key_aes"

KEY_RECOVERY_OTP_HASH = "finance_recovery_otp_hash"
KEY_RECOVERY_OTP_EXPIRES = "finance_recovery_otp_expires"

SESSION_UNLOCK_UNTIL = "finance_vault_until"
SESSION_OK = "finance_vault_ok"

TTL_SECONDS_DEFAULT = 30 * 60

PIN_MIN_LEN = 6
PIN_MAX_LEN = 10
