from django.db import models


class EmailMessage:
    class Status(models.TextChoices):
        NEW = "new"
        READY = "ready"
        PENDING = "pending"
        SUBMITTED = "submitted"  # was "accepted", before that "sent"
        CANCELED = "canceled"
        ERROR = "error"

        # ESP status tracking. These match Anymail's normalized tracking
        # event types, so a webhook's event_type can be stored as-is.
        # https://anymail.dev/en/stable/sending/tracking/#anymail.signals.AnymailTrackingEvent.event_type
        QUEUED = "queued"  # Mailgun's "accepted" event
        SENT = "sent"
        DELIVERED = "delivered"
        REJECTED = "rejected"
        FAILED = "failed"
        BOUNCED = "bounced"
        DEFERRED = "deferred"
        AUTORESPONDED = "autoresponded"
        COMPLAINED = "complained"  # was "spam"
        UNSUBSCRIBED = "unsubscribed"
        SUBSCRIBED = "subscribed"
        OPENED = "opened"
        CLICKED = "clicked"
        UNKNOWN = "unknown"
