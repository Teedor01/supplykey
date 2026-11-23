import os
import smtplib
import ssl

from email.message import EmailMessage
from email.utils import formataddr

from . import config as _config


def send_verification_email(to_email: str, otp: str):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_login = os.getenv("SMTP_LOGIN")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender_email = os.getenv("SMTP_SENDER_EMAIL")
    sender_name = os.getenv("SMTP_SENDER_NAME", "SupplyKey")

    required_settings = {
        "SMTP_SERVER": smtp_server,
        "SMTP_LOGIN": smtp_login,
        "SMTP_PASSWORD": smtp_password,
        "SMTP_SENDER_EMAIL": sender_email,
    }

    missing = [name for name, value in required_settings.items() if not value]

    if missing:
        raise RuntimeError(f"Missing SMTP environment settings: {', '.join(missing)}")

    message = EmailMessage()
    message["Subject"] = "Your SupplyKey verification code"
    message["From"] = formataddr((sender_name, sender_email))
    message["To"] = to_email

    message.set_content(f"""Hello,

Your SupplyKey email verification code is:

{otp}

This code expires in 10 minutes.

If you did not create a SupplyKey account, you can ignore this email.

SupplyKey Team
""")

    message.add_alternative(
        f"""\
<html>
  <body>
    <h2>Verify your SupplyKey account</h2>
    <p>Use the verification code below to verify your email address:</p>
    <h1 style="letter-spacing: 6px;">{otp}</h1>
    <p>This code expires in <strong>10 minutes</strong>.</p>
    <p>If you did not create a SupplyKey account, you can ignore this email.</p>
    <p>SupplyKey Team</p>
  </body>
</html>
""",
        subtype="html",
    )

    context = ssl.create_default_context()

    with smtplib.SMTP(
        smtp_server,
        smtp_port,
        timeout=20,
    ) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(smtp_login, smtp_password)
        server.send_message(message)
