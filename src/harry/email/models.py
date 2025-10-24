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
    # FIXME
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


class EmailMessageAttachment(models.Model):
    """File attachments for EmailMessages"""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="UUID",
        help_text="Secondary ID",
    )
    created_at = models.DateTimeField(db_index=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    email_message = models.ForeignKey(
        EmailMessage, on_delete=models.CASCADE, related_name="attachments"
    )

    # Files are stored with the uuid.ext as the filename on S3 to avoid
    # collisions. We also store the original filename to allow us to
    # reproduce it when necessary.
    file = models.FileField(upload_to="email_message_attachments/")
    filename = models.CharField(max_length=254)
    mimetype = models.CharField(max_length=254)

    class Meta:
        order_with_respect_to = "email_message"

    def __str__(self) -> str:
        return f"{self.email_message} / {self.filename} ({self.uuid})"


class EmailMessageWebhook(models.Model):
    """Webhooks related to an outgoing EmailMessage, like bounces, spam complaints, etc."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name="UUID",
        help_text="Secondary ID",
    )
    created_at = models.DateTimeField(db_index=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    received_at = models.DateTimeField(auto_now_add=True)
    body = models.JSONField()
    headers = models.JSONField()

    type = models.CharField(max_length=254, blank=True)
    email_message = models.ForeignKey(
        EmailMessage, null=True, blank=True, on_delete=models.SET_NULL
    )
    note = models.TextField(blank=True)

    Status = constants.EmailMessageWebhook.Status
    status = models.CharField(
        max_length=127,
        choices=Status.choices,
        default=Status.NEW,
    )

    def __str__(self) -> str:
        if self.type:
            return f"{self.type} ({self.id})"
        else:
            return f"unknown ({self.id})"
