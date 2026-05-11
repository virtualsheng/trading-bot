
import os
import smtplib

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(subject, body):

    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")

    recipient = os.getenv("EMAIL_RECIPIENT")

    if not all([
        smtp_server,
        sender,
        password,
        recipient
    ]):
        print("Missing email configuration")
        return

    msg = MIMEMultipart()

    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP(
        smtp_server,
        smtp_port
    )

    server.starttls()

    server.login(sender, password)

    server.sendmail(
        sender,
        recipient,
        msg.as_string()
    )

    server.quit()

    print("Email sent")
