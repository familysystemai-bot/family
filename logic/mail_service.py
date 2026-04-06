import smtplib
import traceback
from email.message import EmailMessage

from config import SENDER_EMAIL, SENDER_PASSWORD


def send_email(recipients, subject, body):
    """
    إرسال بريد عبر Gmail SMTP.
    المرسل الوحيد: SENDER_EMAIL من config (لا يُستخدم MAIN_RECEIVER_EMAIL أو ADMIN_EMAIL كمرسل).
    """
    return MailService().send_email(recipients, subject, body)


class MailService:
    def send_email(self, recipients, subject, body):
        """
        إرسال إيميل لجهة واحدة أو عدة جهات (مستلمين فقط).
        """
        from_email = SENDER_EMAIL
        password = SENDER_PASSWORD

        if not from_email or not password:
            print("⚠️ خطأ: بيانات البريد SENDER_EMAIL/PASSWORD غير مضبوطة في .env")
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
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
                smtp.login(from_email, password)
                smtp.send_message(msg, to_addrs=to_addrs)
            print(f"✅ تم إرسال الإيميل بنجاح من {from_email} إلى: {to_addrs}")
            return True
        except Exception as e:
            traceback.print_exc()
            print(f"❌ فشل إرسال الإيميل (SMTP): {e}")
            return False
