import re
from typing import Dict, List, Tuple, Any, Optional

class Validator:
    EMAIL_PATTERN = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    PHONE_SA_PATTERN = r'^(05|966)[0-9]{8}$'
    
    @staticmethod
    def validate_email(email: str) -> bool:
        if not email or not isinstance(email, str): return False
        return re.match(Validator.EMAIL_PATTERN, email.strip()) is not None
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        if not phone or not isinstance(phone, str): return False
        phone = phone.replace('+', '').replace('-', '').replace(' ', '')
        return re.match(Validator.PHONE_SA_PATTERN, phone) is not None
    
    @staticmethod
    def validate_string(value: str, min_length: int = 1, max_length: int = 5000, allow_empty: bool = False) -> Tuple[bool, str]:
        if not value and allow_empty: return True, ""
        if not value or not isinstance(value, str): return False, "يجب أن تكون قيمة نصية"
        value = value.strip()
        if len(value) < min_length: return False, f"يجب أن تكون على الأقل {min_length} أحرف"
        if len(value) > max_length: return False, f"يجب ألا تتجاوز {max_length} أحرف"
        return True, ""
    
    @staticmethod
    def validate_integer(value: Any, min_value: Optional[int] = None, max_value: Optional[int] = None) -> Tuple[bool, str]:
        try: num = int(value)
        except (ValueError, TypeError): return False, "يجب أن تكون قيمة رقمية"
        if min_value is not None and num < min_value: return False, f"يجب أن تكون أكبر من أو تساوي {min_value}"
        if max_value is not None and num > max_value: return False, f"يجب أن تكون أقل من أو تساوي {max_value}"
        return True, ""
    
    @staticmethod
    def validate_required_fields(data: Dict, required: List[str]) -> Tuple[bool, Dict[str, str]]:
        errors = {}
        for field in required:
            if field not in data or data[field] is None or data[field] == '':
                errors[field] = f"الحقل '{field}' مطلوب"
        return len(errors) == 0, errors
