import os
import smtplib
import traceback
from email.message import EmailMessage


def send_email(recipients, subject, body):
    """
    إرسال بريد عبر Gmail SMTP.
    المرسل: SENDER_EMAIL / SENDER_PASSWORD من متغيرات البيئة (لا يُستخدم MAIN_RECEIVER_EMAIL كمرسل).
    """
    return MailService().send_email(recipients, subject, body)


class MailService:
    def send_email(self, recipients, subject, body):
        """
        إرسال إيميل لجهة واحدة أو عدة جهات (مستلمين فقط).
        """
        from_email = (os.environ.get("SENDER_EMAIL") or "").strip()
        password = os.environ.get("SENDER_PASSWORD") or ""

        print("EMAIL ENV CHECK")
        print("SENDER_EMAIL:", os.environ.get("SENDER_EMAIL"))
        print("SENDER_PASSWORD EXISTS:", bool(os.environ.get("SENDER_PASSWORD")))

        if not from_email or not password:
            print("⚠️ خطأ: بيانات البريد SENDER_EMAIL/SENDER_PASSWORD غير مضبوطة (متغيرات البيئة).")
            print("EMAIL DIAG: failure stage = env/config (missing sender or password after load)")
            return False

        msg = EmailMessage()
        msg.set_content(body)
        msg["Subject"] = subject
        msg["From"] = from_email

        if isinstance(recipients, list):
            msg["To"] = ", ".join(recipients)
            to_addrs = recipients
        else:
            msg["To"] = recipients
            to_addrs = [recipients]

        try:
            print("CONNECTING SMTP (587 STARTTLS)...")
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as smtp:
                print("CONNECTED")
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                print("LOGGING IN...")
                smtp.login(from_email, password)
                print("LOGIN SUCCESS")
                print("SENDING EMAIL...")
                smtp.send_message(msg, to_addrs=to_addrs)
                print("EMAIL SENT")
            print(f"✅ تم إرسال الإيميل بنجاح من {from_email} إلى: {to_addrs}")
            return True
        except Exception as e:
            err = str(e)
            print("EMAIL ERROR:", err)
            el = err.lower()
            if "timed out" in el or "timeout" in el or "unreachable" in el or "network" in el:
                print("EMAIL DIAG HINT: possible network/firewall (Render blocking SMTP outbound?)")
            if (
                "authentication" in el
                or "535" in err
                or "534" in err
                or "username and password not accepted" in el
            ):
                print("EMAIL DIAG HINT: possible auth — use Gmail App Password, check SENDER_EMAIL")
            traceback.print_exc()
            print(f"❌ فشل إرسال الإيميل (SMTP): {e}")
            return False
