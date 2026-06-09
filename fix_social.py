import os

file_path = r"c:\Users\almth\Desktop\‏‏‏‏‏‏family-system-main\templates\founder\integrations\social.html"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(".values.get", "['values'].get")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Replaced successfully!")
