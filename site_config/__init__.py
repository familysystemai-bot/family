"""حزمة إعدادات الموقع — بيانات الفروع في site_config.branches."""

from site_config.branches import BRANCHES, get_branch, get_management_emails
from site_config.company_policies import (
    build_return_policy_chat_message,
    build_return_policy_complaint_precheck_summary,
    return_policy,
)

__all__ = [
    "BRANCHES",
    "get_branch",
    "get_management_emails",
    "return_policy",
    "build_return_policy_chat_message",
    "build_return_policy_complaint_precheck_summary",
]
