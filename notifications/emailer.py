import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(subject: str, body: str, html_body: str = None):
    """
    Send email with optional HTML body.
    If html_body is provided, sends multipart/alternative with HTML + plain text fallback.
    """
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port   = int(os.getenv("SMTP_PORT", 587))
    sender      = os.getenv("EMAIL_SENDER")
    password    = os.getenv("EMAIL_PASSWORD")
    recipient   = os.getenv("EMAIL_RECIPIENT")

    if not all([smtp_server, sender, password, recipient]):
        print("Missing email configuration")
        return

    msg            = MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = subject

    # Plain text always attached first (fallback for email clients that don't render HTML)
    msg.attach(MIMEText(body, "plain"))

    # HTML version — displayed preferentially by most email clients
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    server = None
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
        print("Email sent")
    except Exception as e:
        print(f"Email error: {e}")
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass