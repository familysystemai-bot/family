import os

path = 'c:/Users/almth/Desktop/‏‏‏‏‏‏family-system-main/static/css/mobile-system.css'
with open(path, 'rb') as f:
    content = f.read()

# The null bytes were appended. Let's just strip everything after the print styles
# The string before the corruption is "@media print {\n  .ms-bottom-nav,\n  .ms-topbar,\n  .dash-menu-btn,\n  .ms-btn-icon { display: none !important; }\n}\n"
marker = b"@media print {\r\n  .ms-bottom-nav,\r\n  .ms-topbar,\r\n  .dash-menu-btn,\r\n  .ms-btn-icon { display: none !important; }\r\n}\r\n"

if marker in content:
    idx = content.find(marker) + len(marker)
    clean_content = content[:idx]
    clean_content += b"canvas { touch-action: pan-y !important; }\n"
    with open(path, 'wb') as f:
        f.write(clean_content)
    print("Fixed corrupted css")
else:
    # Try just \n without \r
    marker2 = b"@media print {\n  .ms-bottom-nav,\n  .ms-topbar,\n  .dash-menu-btn,\n  .ms-btn-icon { display: none !important; }\n}\n"
    if marker2 in content:
        idx = content.find(marker2) + len(marker2)
        clean_content = content[:idx]
        clean_content += b"canvas { touch-action: pan-y !important; }\n"
        with open(path, 'wb') as f:
            f.write(clean_content)
        print("Fixed corrupted css (LF)")
    else:
        print("Marker not found!")
