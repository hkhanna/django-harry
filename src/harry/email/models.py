import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import constants


class EmailMessage(models.Model):
    """Keep a record of every email sent in the DB."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="UUID",
        help_text="Secondary ID",
    )
    created_at = models.DateTimeField(db_index=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        help_text="User that caused the EmailMessage to be created.",
        null=True,
        blank=True,
    )
    # org = models.ForeignKey(
    #     "core.Org",
    #     on_delete=models.SET_NULL,
    #     help_text="The active Org of the User that caused the EmailMessage to be created.",
    #     null=True,
    #     blank=True,
    # )
    sender_name = models.CharField(max_length=254, blank=True)
    sender_email = models.EmailField()
    to_name = models.CharField(max_length=254, blank=True)
    to_email = models.EmailField()
    reply_to_name = models.CharField(max_length=254, blank=True)
    reply_to_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=254, blank=True)
    template_prefix = models.CharField(max_length=254)
    template_context = models.JSONField(default=dict, blank=True)
    message_id = models.CharField(
        max_length=254,
        unique=True,
        null=True,
        blank=True,
        default=None,
        help_text="Message-ID provided by the sending service as per RFC 5322",
    )
    postmark_message_stream = models.CharField(
        max_length=254, blank=True, help_text="Leave blank if not using Postmark"
    )

    Status = constants.EmailMessage.Status
    status = models.CharField(
        max_length=254,
        choices=Status.choices,
        default=Status.NEW,
    )
    error_message = models.TextField(blank=True)

    def __str__(self) -> str:
        # This will return something like 'reset-password' since its the last part of the template prefix
        template_prefix = self.template_prefix.split("/")[-1]
        return f"{template_prefix} ({self.uuid})"
