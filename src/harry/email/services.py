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

from . import constants
from .models import EmailMessage, EmailMessageAttachment, EmailMessageWebhook
from .utils import trim_string, validate_request_body_json

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

        # FIXME
        # if global_setting_get_value("disable_outbound_email"):
        #     raise RuntimeError("GlobalSetting disable_outbound_email is True")
        else:
            message_ids = django_email_message.send()

            # FIXME
            # Postmark has a setting for returning MessageIDs
            if isinstance(message_ids, list):
                if len(message_ids) == 1:
                    email_message.message_id = message_ids[0]

    except Exception as e:
        email_message.status = constants.EmailMessage.Status.ERROR
        email_message.error_message = repr(e)
        email_message.full_clean()
        email_message.save()
        logger.exception(
            f"EmailMessage.id={email_message.id} Exception caught in send_email_message"
        )
    else:
        email_message.status = constants.EmailMessage.Status.SENT
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


def email_message_webhook_create_from_request(
    *, body: str, headers: dict
) -> EmailMessageWebhook:
    """Create an EmailMessageWebhook from a request object."""
    payload = validate_request_body_json(body=body)
    if not isinstance(payload, dict):
        raise ValueError("Invalid payload")

    headers_processed = {}
    for key in headers:
        value = headers[key]
        if isinstance(value, str):
            headers_processed[key] = value

    webhook = email_message_webhook_create(
        body=payload,
        headers=headers_processed,
        status=constants.EmailMessageWebhook.Status.NEW,
    )
    logger.info(f"EmailMessageWebhook.id={webhook.id} received")

    return webhook


def email_message_webhook_create(**kwargs) -> EmailMessageWebhook:
    return EmailMessageWebhook.objects.create(**kwargs)


def email_message_webhook_process(
    *, email_message_webhook: EmailMessageWebhook
) -> None:
    webhook = email_message_webhook
    try:
        if webhook.status != constants.EmailMessageWebhook.Status.NEW:
            raise RuntimeError(
                f"EmailMessageWebhook.id={webhook.id} process_email_message_webhook called on a webhook that is not status=NEW"
            )
        webhook.status = constants.EmailMessageWebhook.Status.PENDING
        webhook.full_clean()
        webhook.save()

        # Store the type
        if "RecordType" in webhook.body:
            webhook.type = webhook.body["RecordType"]
            webhook.full_clean()
            webhook.save()

        # Find the related EmailMessage and connect it
        if "MessageID" in webhook.body:
            email_message = EmailMessage.objects.filter(
                message_id=webhook.body["MessageID"]
            ).first()
            if email_message:
                webhook.email_message = email_message
                if webhook.type in constants.WEBHOOK_TYPE_TO_EMAIL_STATUS:
                    # Make sure this is the most recent webhook, in case it arrived out of order.
                    all_ts = []
                    for other_webhook in EmailMessageWebhook.objects.filter(
                        email_message=email_message
                    ):
                        ts_key = constants.WEBHOOK_TYPE_TO_TIMESTAMP[other_webhook.type]
                        ts = other_webhook.body[ts_key]
                        ts = ts.replace("Z", "+00:00")
                        all_ts.append(datetime.fromisoformat(ts))
                    all_ts.sort()

                    ts_key = constants.WEBHOOK_TYPE_TO_TIMESTAMP[webhook.type]
                    ts = webhook.body[ts_key]
                    ts = ts.replace("Z", "+00:00")
                    ts_dt = datetime.fromisoformat(ts)
                    if len(all_ts) == 0 or all_ts[-1] < ts_dt:
                        new_status = constants.WEBHOOK_TYPE_TO_EMAIL_STATUS[
                            webhook.type
                        ]
                        email_message.status = new_status
                        email_message.full_clean()
                        email_message.save()

        webhook.status = constants.EmailMessageWebhook.Status.PROCESSED
        webhook.full_clean()
        webhook.save()
        logger.debug(f"EmailMessageWebhook.id={webhook.id} processed")
    except Exception:
        logger.exception(f"EmailMessageWebhook.id={webhook.id} in error state")
        webhook.status = constants.EmailMessageWebhook.Status.ERROR
        webhook.note = traceback.format_exc()
        webhook.full_clean()
        webhook.save()
