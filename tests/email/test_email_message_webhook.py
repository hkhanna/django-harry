from datetime import timedelta

import pytest
from django.utils import timezone

from .. import factories

from harry.email import constants, models, services


def test_bad_json():
    """Bad JSON isn't processed"""
    with pytest.raises(ValueError):
        services.email_message_webhook_create_from_request(
            body="bad json", headers={"X-Some-Header": "id-xyz456"}
        )

    assert models.EmailMessageWebhook.objects.count() == 0


def test_email_message_webhook_process_type_recorded():
    """An EmailMessageWebhook records its type"""
    services.email_message_webhook_process(
        email_message_webhook=factories.email_message_webhook_create()
    )
    assert models.EmailMessageWebhook.objects.count() == 1
    assert models.EmailMessageWebhook.objects.first().type == "some_type"


def test_email_message_webhook_process_linked():
    """An EmailMessageWebhook is linked to its related EmailMessage"""
    linked = factories.email_message_create(message_id="id-abc123")
    _notlinked = factories.email_message_create(message_id="other-id")

    services.email_message_webhook_process(
        email_message_webhook=factories.email_message_webhook_create()
    )
    assert models.EmailMessageWebhook.objects.count() == 1
    assert models.EmailMessageWebhook.objects.first().email_message == linked


Status = constants.EmailMessage.Status


@pytest.mark.parametrize(
    "record_type,new_status",
    [
        ("Delivery", Status.DELIVERED),
        ("Open", Status.OPENED),
        ("Bounce", Status.BOUNCED),
        ("SpamComplaint", Status.SPAM),
    ],
)
def test_update_email_message_status(record_type, new_status):
    email_message = factories.email_message_create(message_id="id-abc123")
    ts_key = constants.WEBHOOK_TYPE_TO_TIMESTAMP[record_type]
    email_message_webhook = factories.email_message_webhook_create(
        body={
            "RecordType": record_type,
            "MessageID": email_message.message_id,
            ts_key: timezone.now().isoformat().replace("+00:00", "Z"),
        }
    )
    services.email_message_webhook_process(email_message_webhook=email_message_webhook)
    email_message.refresh_from_db()
    assert email_message.status == new_status


def test_update_email_message_status_order():
    """An EmailMessageWebhook that arrives out of order should not regress the status."""
    email_message = factories.email_message_create(message_id="id-abc123")
    delivered_at = timezone.now()
    opened_at = delivered_at + timedelta(seconds=2)
    spam_at = opened_at + timedelta(seconds=5)

    body = {
        "RecordType": "Open",
        "MessageID": email_message.message_id,
        "ReceivedAt": opened_at.isoformat().replace("+00:00", "Z"),
    }

    email_message_webhook = factories.email_message_webhook_create(body=body)
    services.email_message_webhook_process(email_message_webhook=email_message_webhook)

    body = {
        "RecordType": "Delivery",
        "MessageID": email_message.message_id,
        "DeliveredAt": delivered_at.isoformat().replace("+00:00", "Z"),
    }
    email_message_webhook = factories.email_message_webhook_create(body=body)
    services.email_message_webhook_process(email_message_webhook=email_message_webhook)

    email_message.refresh_from_db()
    assert email_message.status == constants.EmailMessage.Status.OPENED

    # IN-order webhook should still update
    body = {
        "RecordType": "SpamComplaint",
        "MessageID": email_message.message_id,
        "BouncedAt": spam_at.isoformat().replace("+00:00", "Z"),
    }
    email_message_webhook = factories.email_message_webhook_create(body=body)
    services.email_message_webhook_process(email_message_webhook=email_message_webhook)

    email_message.refresh_from_db()
    assert email_message.status == constants.EmailMessage.Status.SPAM
