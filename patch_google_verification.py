import re

def clean_google_verification(val):
    if not val:
        return ""
    # If the user pasted the entire meta tag
    match = re.search(r'content\s*=\s*[\'"]([^\'"]+)[\'"]', val, re.IGNORECASE)
    if match:
        return match.group(1)
    return val.strip()

with open("app.py", "r", encoding="utf-8") as f:
    content = f.read()

# Locate the context processor
target = 'google_site_verification = db.get_system_setting("GOOGLE_SITE_VERIFICATION", "") or ""'
replacement = '''google_site_verification_raw = db.get_system_setting("GOOGLE_SITE_VERIFICATION", "") or ""
    import re
    m = re.search(r\'content\s*=\s*["\\\']([^"\\\']+)["\\\']\', google_site_verification_raw, re.IGNORECASE)
    google_site_verification = m.group(1) if m else google_site_verification_raw.strip()'''

content = content.replace(target, replacement)

# Do the same for lines 2234 and 2262 which have:
target2 = 'google_site_verification = db.get_system_setting("GOOGLE_SITE_VERIFICATION")'
replacement2 = '''google_site_verification_raw = db.get_system_setting("GOOGLE_SITE_VERIFICATION") or ""
    import re
    m = re.search(r\'content\s*=\s*["\\\']([^"\\\']+)["\\\']\', google_site_verification_raw, re.IGNORECASE)
    google_site_verification = m.group(1) if m else google_site_verification_raw.strip()'''

content = content.replace(target2, replacement2)

with open("app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("app.py patched!")
