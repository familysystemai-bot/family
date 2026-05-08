import os
import smtplib
import traceback
import logging
from email.message import EmailMessage

logger = logging.getLogger(__name__)


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

        if not from_email or not password:
            logger.warning("mail_service: sender credentials are not configured")
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
            logger.info("mail_service: connecting to SMTP")
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(from_email, password)
                smtp.send_message(msg, to_addrs=to_addrs)
            logger.info("mail_service: email sent successfully to %s recipient(s)", len(to_addrs))
            return True
        except Exception as e:
            err = str(e)
            logger.error("mail_service: SMTP send failed: %s", err)
            el = err.lower()
            if "timed out" in el or "timeout" in el or "unreachable" in el or "network" in el:
                logger.warning("mail_service: possible network/firewall issue while sending email")
            if (
                "authentication" in el
                or "535" in err
                or "534" in err
                or "username and password not accepted" in el
            ):
                logger.warning("mail_service: possible SMTP authentication issue")
            traceback.print_exc()
            return False
