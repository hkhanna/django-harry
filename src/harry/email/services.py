import logging
import mimetypes
import traceback
from datetime import datetime, timedelta
from typing import IO, AnyStr, List
from uuid import uuid4

from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.mail.message import EmailMultiAlternatives, sanitize_address
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone

from anymail.signals import AnymailTrackingEvent

from . import constants
from .models import EmailMessage, EmailMessageAttachment
from .utils import trim_string

logger = logging.getLogger(__name__)


def email_message_check_cooling_down(
    *, email_message: EmailMessage, period: int, allowed: int, scopes: List[str]
) -> bool:
    """Check that this created_by/template_prefix/to_email combination hasn't been recently sent.
    You can tighten the suppression by removing scopes. An empty list will cancel if any email
    at all has been sent in the cooldown period."""
    e = email_message
    cooldown_period = timedelta(seconds=period)
    email_messages = EmailMessage.objects.filter(
        sent_at__gt=timezone.now() - cooldown_period
    )
    if "created_by" in scopes:
        email_messages = email_messages.filter(created_by=e.created_by)
    if "template_prefix" in scopes:
        email_messages = email_messages.filter(template_prefix=e.template_prefix)
    if "to" in scopes:
        email_messages = email_messages.filter(to_email=e.to_email)

    return email_messages.count() >= allowed


def email_message_prepare(*, email_message: EmailMessage) -> None:
    """Updates the context with defaults and other sanity checking"""
    e = email_message

    if e.status != constants.EmailMessage.Status.NEW:
        raise RuntimeError(
            f"EmailMessage.id={e.id} email_message_prepare() called on an email that is not status=NEW"
        )

    assert settings.SITE_CONFIG["default_from_email"] is not None
    e.sender_email = trim_string(
        field=e.sender_email or settings.SITE_CONFIG["default_from_email"]
    )
    e.sender_name = trim_string(
        field=e.sender_name or settings.SITE_CONFIG["default_from_name"] or ""
    )
    e.reply_to_email = trim_string(field=e.reply_to_email or "")
    e.reply_to_name = trim_string(field=e.reply_to_name or "")
    e.to_name = trim_string(field=e.to_name)
    e.to_email = trim_string(field=e.to_email)
    e.full_clean()
    e.save()

    if e.reply_to_name and not e.reply_to_email:
        email_message.status = constants.EmailMessage.Status.ERROR
        email_message.full_clean()
        email_message.save()
        raise RuntimeError("Reply to has a name but does not have an email")

    # Set defaults for template context if not provided.
    template_context = {
        "logo_url": settings.SITE_CONFIG["logo_url"],
        "logo_url_link": settings.SITE_CONFIG["logo_url_link"],
        "contact_email": settings.SITE_CONFIG["contact_email"],
        "site_name": settings.SITE_CONFIG["name"],
        "company": settings.SITE_CONFIG["company"],
        "company_address": settings.SITE_CONFIG["company_address"],
        "company_city_state_zip": settings.SITE_CONFIG["company_city_state_zip"],
    } | e.template_context

    # Render subject from template if not already set
    subject = e.subject
    if not subject:
        subject = render_to_string(
            "{0}_subject.txt".format(e.template_prefix), template_context
        )
    subject = trim_string(field=subject)
    if len(subject) > settings.MAX_SUBJECT_LENGTH:
        subject = subject[: settings.MAX_SUBJECT_LENGTH - 3] + "..."
    template_context["subject"] = subject

    e.template_context = template_context
    e.subject = subject
    e.status = constants.EmailMessage.Status.READY
    e.full_clean()
    e.save()


def email_message_attach(
    *,
    email_message: EmailMessage,
    file: IO[AnyStr] | AnyStr,
    filename: str,
    mimetype: str,
) -> EmailMessageAttachment:
    """Attach a file to an EmailMessage via EmailMessageAttachment.
    For convenience, file can be a python file object or string or bytes of content.
    """

    if email_message.status != constants.EmailMessage.Status.READY:
        raise RuntimeError(
            f"EmailMessage.id={email_message.id} email_message_attach called on an email that is not status=READY. Did you run email_message_prepare()?"
        )

    # Filename's extension must match mimetype
    expected = mimetypes.guess_type(filename)[0]
    if mimetype != expected:
        raise ValueError(f"Filename {filename} does not match mimetype {mimetype}")

    ext = mimetypes.guess_extension(mimetype)  # For storage on S3
    uuid = uuid4()

    if not isinstance(file, (str, bytes)):
        django_file = File(file, name=f"{uuid}{ext}")
    else:
        django_file = ContentFile(file, name=f"{uuid}{ext}")

    attachment = email_message_attachment_create(
        uuid=uuid,
        email_message=email_message,
        filename=filename,
        mimetype=mimetype,
        file=django_file,
    )
    return attachment


def email_message_queue(
    *,
    email_message: EmailMessage,
    cooldown_period: int = 180,
    cooldown_allowed: int = 1,
    scopes: List[str] = ["created_by", "template_prefix", "to"],
) -> bool:
    e = email_message

    # If we've pre-prepared the email, skip the prepare step.
    if e.status != constants.EmailMessage.Status.READY:
        email_message_prepare(email_message=e)

    if email_message_check_cooling_down(
        email_message=e,
        period=cooldown_period,
        allowed=cooldown_allowed,
        scopes=scopes,
    ):
        e.status = constants.EmailMessage.Status.CANCELED
        e.error_message = "Cooling down"
        e.full_clean()
        e.save()
        return False
    else:
        # FIXME: Use Django tasks or celery
        email_message_send(email_message=email_message)
        return True


def email_message_send(*, email_message: EmailMessage) -> None:
    """Send an email_message immediately. Normally called by a celery task."""
    if email_message.status != constants.EmailMessage.Status.READY:
        raise RuntimeError(
            f"EmailMessage.id={email_message.id} email_message_send called on an email that is not status=READY. Did you run email_message_queue()"
        )
    email_message.status = constants.EmailMessage.Status.PENDING
    email_message.full_clean()
    email_message.save()
    template_name = email_message.template_prefix + "_message.txt"
    html_template_name = email_message.template_prefix + "_message.html"

    try:
        msg = render_to_string(
            template_name=template_name,
            context=email_message.template_context,
        )
        html_msg = None
        try:
            html_msg = render_to_string(
                template_name=html_template_name,
                context=email_message.template_context,
            )
        except TemplateDoesNotExist:
            logger.warning(
                f"EmailMessage.id={email_message.id} template not found {html_template_name}"
            )

        encoding = settings.DEFAULT_CHARSET
        from_email = sanitize_address(
            (email_message.sender_name, email_message.sender_email), encoding
        )
        to = [
            sanitize_address((email_message.to_name, email_message.to_email), encoding),
        ]

        if email_message.reply_to_email:
            reply_to = [
                sanitize_address(
                    (email_message.reply_to_name, email_message.reply_to_email),
                    encoding,
                )
            ]
        else:
            reply_to = None

        django_email_message = EmailMultiAlternatives(
            subject=email_message.subject,
            from_email=from_email,
            to=to,
            body=msg,
            reply_to=reply_to,
        )
        if html_msg:
            django_email_message.attach_alternative(html_msg, "text/html")

        for attachment in email_message.attachments.all():
            django_email_message.attach(
                attachment.filename, attachment.file.read(), attachment.mimetype
            )

        # See #2
        # if global_setting_get_value("disable_outbound_email"):
        #     raise RuntimeError("GlobalSetting disable_outbound_email is True")
        else:
            django_email_message.send()
            email_message.message_id = django_email_message.anymail_status.message_id

    except Exception as e:
        email_message.status = constants.EmailMessage.Status.ERROR
        email_message.error_message = repr(e)
        email_message.full_clean()
        email_message.save()
        logger.exception(
            f"EmailMessage.id={email_message.id} Exception caught in send_email_message"
        )
    else:
        email_message.status = constants.EmailMessage.Status.ACCEPTED
        email_message.sent_at = timezone.now()
        email_message.full_clean()
        email_message.save()


def email_message_create(*, save: bool = False, **kwargs) -> EmailMessage:
    # By default, we don't persist the email_message because often it is
    # not ready until email_message_prepare is called on it.
    email_message = EmailMessage(**kwargs)
    if save:
        email_message.full_clean()
        email_message.save()
    return email_message


def email_message_duplicate(*, original: EmailMessage) -> EmailMessage:
    """Duplicate an EmailMessage and return the new EmailMessage."""
    duplicate = EmailMessage.objects.get(pk=original.pk)

    duplicate.pk = None
    duplicate.uuid = uuid4()
    duplicate._state.adding = True

    duplicate.status = constants.EmailMessage.Status.NEW
    duplicate.error_message = ""
    duplicate.message_id = None
    duplicate.sent_at = None
    duplicate.full_clean()
    duplicate.save()

    email_message_prepare(email_message=duplicate)

    for attachment in original.attachments.all():
        email_message_attach(
            email_message=duplicate,
            file=attachment.file,
            filename=attachment.filename,
            mimetype=attachment.mimetype,
        )

    return duplicate


def email_message_attachment_create(**kwargs) -> EmailMessageAttachment:
    return EmailMessageAttachment.objects.create(**kwargs)


def email_message_webhook_process(*, event: AnymailTrackingEvent) -> None:
    logger.info(
        "Webhook received: event_type=%s message_id=%s event_id=%s recipient=%s",
        event.event_type,
        event.message_id,
        event.event_id,
        event.recipient,
    )
    try:
        if not event.message_id:
            logger.warning(
                "Webhook has no message_id, skipping: event_type=%s event_id=%s",
                event.event_type,
                event.event_id,
            )
            return

        email_message = EmailMessage.objects.filter(message_id=event.message_id).first()
        if not email_message:
            logger.warning(
                "No EmailMessage found for message_id=%s, skipping: event_type=%s event_id=%s",
                event.message_id,
                event.event_type,
                event.event_id,
            )
            return

        logger.debug(
            "Matched EmailMessage.id=%s (status=%s) for message_id=%s",
            email_message.id,
            email_message.status,
            event.message_id,
        )

        # Make sure this is the most recent webhook, in case it arrived out of order.
        if email_message.esp_event_at and email_message.esp_event_at > event.timestamp:
            logger.warning(
                "Stale webhook ignored: EmailMessage.id=%s has esp_event_at=%s but event timestamp=%s, event_type=%s event_id=%s esp_event=%s",
                email_message.id,
                email_message.esp_event_at,
                event.timestamp,
                event.event_type,
                event.event_id,
                event.esp_event,
            )
            return

        old_status = email_message.status
        valid_statuses = {choice.value for choice in constants.EmailMessage.Status}
        status = (
            event.event_type
            if event.event_type in valid_statuses
            else constants.EmailMessage.Status.UNKNOWN
        )
        email_message.status = status
        email_message.esp_event = event.esp_event
        email_message.esp_event_at = event.timestamp
        email_message.full_clean()
        email_message.save()
        logger.info(
            "EmailMessage.id=%s status updated: %s -> %s (event_id=%s)",
            email_message.id,
            old_status,
            event.event_type,
            event.event_id,
        )

    except Exception:
        logger.exception(
            "Error processing webhook: message_id=%s event_type=%s event_id=%s timestamp=%s esp_event=%s",
            event.message_id,
            event.event_type,
            event.event_id,
            event.timestamp,
            event.esp_event,
        )
