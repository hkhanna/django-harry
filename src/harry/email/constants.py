from django.db import models


class EmailMessage:
    class Status(models.TextChoices):
        NEW = "new"
        READY = "ready"
        PENDING = "pending"
        ACCEPTED = "accepted"  # was "sent"
        CANCELED = "canceled"
        ERROR = "error"

        # ESP status tracking
        DELIVERED = "delivered"
        REJECTED = "rejected"
        BOUNCED = "bounced"
        COMPLAINED = "complained"  # was "spam"
        UNSUBSCRIBED = "unsubscribed"
        OPENED = "opened"
        CLICKED = "clicked"
        UNKNOWN = "unknown"
