from django.db import models


class EmailMessage:
    class Status(models.TextChoices):
        NEW = "new"
        READY = "ready"
        PENDING = "pending"
        SENT = "sent"
        DELIVERED = "delivered"
        OPENED = "opened"
        BOUNCED = "bounced"
        SPAM = "spam"
        CANCELED = "canceled"
        ERROR = "error"
