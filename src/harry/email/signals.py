from anymail.signals import tracking
from django.dispatch import receiver

from harry.email.services import (
    email_message_webhook_process,
)


@receiver(tracking)
def handle_email_tracking(sender, event, esp_name, **kwargs):
    email_message_webhook_process(event=event)
