import os

css_append = """
/* ——— الوضع الداكن (Dark Mode) ——— */
html[data-theme="dark"] {
  --fd-beige: #0f172a;
  --fd-beige-soft: #1e293b;
  --fd-white-card: #1e293b;
  --fd-sidebar-luxury: #022c22;
  --fd-sidebar-luxury-deep: #064e3b;
  --fd-green-dark: #10b981;
  --fd-green-mid: #34d399;
  --fd-green-btn: #059669;
  --fd-green-btn-hover: #10b981;
  --fd-gold: #fbbf24;
  --fd-gold-dim: #f59e0b;
  --fd-text: #f8fafc;
  --fd-text-body: #e2e8f0;
  --fd-text-soft: #cbd5e1;
  --fd-title-accent: #fcd34d;
  --fd-border: rgba(255, 255, 255, 0.1);
  --fd-border-gold: rgba(251, 191, 36, 0.35);
  --fd-shadow: 0 8px 28px rgba(0, 0, 0, 0.4);
  --fd-shadow-sm: 0 4px 16px rgba(0, 0, 0, 0.2);
  --fd-green-glow: rgba(52, 211, 153, 0.18);
}

html[data-theme="dark"] body.fd-dashboard {
  background: var(--fd-beige);
  background-image: none; /* remove gradient for cleaner dark mode */
}

html[data-theme="dark"] .fd-card,
html[data-theme="dark"] .fd-chart-wrap,
html[data-theme="dark"] .fd-premium-overview,
html[data-theme="dark"] .fd-kpi-card,
html[data-theme="dark"] .stat-card,
html[data-theme="dark"] .fd-dashboard .form-control,
html[data-theme="dark"] .fd-dashboard .form-select {
  background: var(--fd-white-card);
  color: var(--fd-text);
  border-color: var(--fd-border);
}

html[data-theme="dark"] .fd-traffic-pill {
  background: rgba(255, 255, 255, 0.05);
  border-color: var(--fd-border);
}

html[data-theme="dark"] .fd-kpi-card__value,
html[data-theme="dark"] .fd-traffic-pill__num,
html[data-theme="dark"] .fd-kpi-card__label,
html[data-theme="dark"] .fd-traffic-pill__lbl {
  color: var(--fd-text);
}

html[data-theme="dark"] .table.fd-table thead th {
  background: #0f172a;
  color: #f8fafc;
  border-color: var(--fd-border);
}

html[data-theme="dark"] .table.fd-table tbody td {
  border-color: var(--fd-border);
  color: var(--fd-text-body);
}

html[data-theme="dark"] .fd-btn--outline {
  background: transparent !important;
  color: var(--fd-gold) !important;
  border-color: var(--fd-border-gold) !important;
}

html[data-theme="dark"] .fd-dashboard code {
  background: rgba(255, 255, 255, 0.1);
  color: var(--fd-gold);
}

html[data-theme="dark"] .fd-company-identity-modal,
html[data-theme="dark"] .fd-company-identity-modal__form-wrap .stat-card {
  background: var(--fd-beige-soft);
  color: var(--fd-text);
  border-color: var(--fd-border);
}
"""

with open("c:/Users/almth/Desktop/‏‏‏‏‏‏family-system-main/static/css/founder-dashboard.css", "a", encoding="utf-8") as f:
    f.write(css_append)

print("Dark mode CSS appended!")
