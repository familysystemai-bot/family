# -*- coding: utf-8 -*-
"""
social_dashboard_routes.py — Blueprint للوحة التحكم الموحدة لوسائل التواصل الاجتماعي.
=============================================================================
"""
from __future__ import annotations

import logging
from flask import Blueprint, render_template, session, redirect, url_for, flash, request

logger = logging.getLogger(__name__)

def create_social_dashboard_blueprint(db):
    bp = Blueprint("social_dashboard", __name__, url_prefix="/founder/social")

    def _is_founder():
        return session.get("role") == "founder"

    @bp.route("/")
    @bp.route("/dashboard")
    def dashboard():
        if not _is_founder():
            return redirect(url_for("login"))
        
        # بيانات تجريبية للهيكل الأولي
        stats = {
            "total_followers": 0,
            "total_reach": 0,
            "total_engagement": 0,
            "active_platforms": ["WhatsApp"] # واتساب مفعل حالياً في النظام
        }
        
        return render_template(
            "founder/social/dashboard.html",
            stats=stats
        )

    @bp.route("/publisher")
    def publisher():
        if not _is_founder():
            return redirect(url_for("login"))
        return render_template("founder/social/publisher.html")

    @bp.route("/inbox")
    def unified_inbox():
        if not _is_founder():
            return redirect(url_for("login"))
        return render_template("founder/social/inbox.html")

    return bp
