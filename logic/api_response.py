from typing import Any, Dict, Optional, List
from flask import jsonify

class APIResponse:
    SUCCESS = True
    ERROR = False
    
    @staticmethod
    def success(data: Any = None, message: str = "تم بنجاح", status_code: int = 200, meta: Optional[Dict] = None) -> tuple:
        response = {"ok": APIResponse.SUCCESS, "message": message, "data": data}
        if meta: response["meta"] = meta
        return jsonify(response), status_code
    
    @staticmethod
    def error(error: str, status_code: int = 400, details: Optional[Dict] = None, error_code: Optional[str] = None) -> tuple:
        response = {"ok": APIResponse.ERROR, "error": error}
        if error_code: response["error_code"] = error_code
        if details: response["details"] = details
        return jsonify(response), status_code
    
    @staticmethod
    def paginated(items: List[Any], total: int, page: int, per_page: int, message: str = "تم بنجاح") -> tuple:
        total_pages = (total + per_page - 1) // per_page
        response = {
            "ok": APIResponse.SUCCESS, "message": message, "data": items,
            "pagination": {"total": total, "page": page, "per_page": per_page, "total_pages": total_pages, "has_next": page < total_pages, "has_prev": page > 1}
        }
        return jsonify(response), 200
    
    @staticmethod
    def created(data: Any = None, message: str = "تم الإنشاء بنجاح", location: Optional[str] = None) -> tuple:
        response = {"ok": APIResponse.SUCCESS, "message": message, "data": data}
        if location: response["location"] = location
        return jsonify(response), 201
    
    @staticmethod
    def unauthorized(message: str = "غير مصرح") -> tuple:
        return APIResponse.error(message, 401, error_code="UNAUTHORIZED")
    
    @staticmethod
    def forbidden(message: str = "ممنوع الوصول") -> tuple:
        return APIResponse.error(message, 403, error_code="FORBIDDEN")
    
    @staticmethod
    def not_found(message: str = "غير موجود") -> tuple:
        return APIResponse.error(message, 404, error_code="NOT_FOUND")
