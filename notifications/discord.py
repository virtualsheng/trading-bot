
import os
import requests

def send_discord_message(message):

    webhook = os.getenv("DISCORD_WEBHOOK_URL")

    if not webhook:
        return

    requests.post(
        webhook,
        json={"content": message},
        timeout=10
    )
