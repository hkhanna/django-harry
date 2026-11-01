# harry

Email primitives for Django projects.

harry is a set of independent modules that share one package. Each module is
adoptable on its own — installing the package costs nothing until you wire a
module into your settings, and no module requires another. The goal is that
every project that installs harry sends email the same way.

| Module | What it does | Needs |
|---|---|---|
| [`harry.email`](#email) | Transactional email via anymail, persisted to your database with delivery tracking | the `harry[email]` extra + a transactional ESP |

## Installation

harry is not on PyPI; install it from git. In the consuming project's
`pyproject.toml`:

```toml
dependencies = [
    "harry @ git+https://github.com/hkhanna/django-harry",
]
```

Each module's third-party dependencies ship as an extra, so a project only
installs what it wires up: [Email](#email) needs the `email` extra. Name what
you use in the requirement:

```toml
dependencies = [
    "harry[email] @ git+https://github.com/hkhanna/django-harry",
]
```

## Email

Transactional email through [django-anymail](https://anymail.dev/), with every
message persisted as an `EmailMessage` record in your database and delivery
tracked via ESP webhooks.

**Requires:** the `harry[email]` extra (see [Installation](#installation)) and
a transactional ESP account. Nothing else from harry.

### Setup

#### 1. Add to INSTALLED_APPS

```python
INSTALLED_APPS = [
    # ...
    "anymail",
    "harry.email",
]
```

#### 2. Configure your email provider

harry uses anymail to send email through any transactional ESP. Configure your
provider in `settings.py`:

```python
# Example using Mailgun
EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"
ANYMAIL = {
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY"),
    "MAILGUN_WEBHOOK_SIGNING_KEY": env("MAILGUN_WEBHOOK_SIGNING_KEY")
}
```

See the [anymail docs](https://anymail.dev/en/stable/) for the full list of
supported providers and their settings.

#### 3. Configure site defaults

harry pulls sender defaults and template context from a `SITE_CONFIG` dict in
settings. Values here are used as fallbacks when not provided per-message.

```python
SITE_CONFIG = {
    "name": "My App",
    "default_from_name": "My App",
    "default_from_email": "hello@myapp.com",
    "company": "My App Inc.",
    "company_address": "123 Main St",
    "company_city_state_zip": "New York, NY 10001",
    "contact_email": "support@myapp.com",
    "logo_url": "https://myapp.com/logo.png",
    "logo_url_link": "https://myapp.com",
}

MAX_SUBJECT_LENGTH = 78  # Subjects are truncated to this length
```

#### 4. Create email templates

Each email needs a template prefix that maps to template files in your Django
template directories:

```
templates/
  account/
    welcome_subject.txt       # Subject line (rendered as a template)
    welcome_message.txt       # Plain text body (required)
    welcome_message.html      # HTML body (optional)
```

Templates receive `SITE_CONFIG` values as context by default, plus any custom
context you pass. For example, `welcome_message.txt`:

```
Hi {{ to_name }},

Welcome to {{ site_name }}!

Thanks,
{{ company }}
{{ company_address }}
{{ company_city_state_zip }}
```

#### 5. Run migrations

```bash
python manage.py migrate
```

#### 6. Set up delivery tracking webhooks (optional)

harry stores webhook events from your ESP so you can track whether emails were
delivered, opened, bounced, or marked as spam. Add anymail's webhook URLs to
your root `urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    # ...
    path("anymail/", include("anymail.urls")),
]
```

Then configure a webhook secret in settings:

```python
ANYMAIL = {
    "MAILGUN_API_KEY": env("MAILGUN_API_KEY"),
    "MAILGUN_WEBHOOK_SIGNING_KEY": env("MAILGUN_WEBHOOK_SIGNING_KEY")
    "WEBHOOK_SECRET": env("ANYMAIL_WEBHOOK_SECRET"),
}
```

Generate a webhook secret:

```bash
python -c "from django.utils.crypto import get_random_string; print(':'.join(get_random_string(16) for _ in range(2)))"
```

Register the webhook URL with your ESP, using the secret as HTTP basic auth
credentials:

```
https://<part1>:<part2>@yourdomain.com/anymail/mailgun/tracking/
```

### Usage

The primary API is a set of service functions.

#### Basic send

```python
from harry.email.services import (
    email_message_create,
    email_message_queue,
)

email = email_message_create(
    created_by=user,
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    template_context={"coupon_code": "HELLO10"},
)

email_message_queue(email_message=email)
```

`email_message_queue` prepares the message (applies defaults, renders the
subject, validates) and sends it. It returns `True` if the email was sent, or
`False` if it was suppressed by cooldown (see below).

#### Setting the subject

The subject is rendered from `{template_prefix}_subject.txt` by default. You
can also set it explicitly:

```python
email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    subject="Welcome aboard!",
)
```

#### Custom sender and reply-to

```python
email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="account/welcome",
    sender_name="Support Team",
    sender_email="support@myapp.com",
    reply_to_name="Support",
    reply_to_email="support@myapp.com",
)
```

If `sender_name` and `sender_email` are omitted, they fall back to
`SITE_CONFIG["default_from_name"]` and `SITE_CONFIG["default_from_email"]`.

#### Attachments

Attach files after preparing the message:

```python
from harry.email.services import (
    email_message_create,
    email_message_prepare,
    email_message_attach,
    email_message_queue,
)

email = email_message_create(
    to_name="Alice",
    to_email="alice@example.com",
    template_prefix="billing/invoice",
)
email_message_prepare(email_message=email)

# From a file object
with open("invoice.pdf", "rb") as f:
    email_message_attach(
        email_message=email,
        file=f,
        filename="invoice.pdf",
        mimetype="application/pdf",
    )

# From bytes
email_message_attach(
    email_message=email,
    file=b"Name,Amount\nAlice,100",
    filename="data.csv",
    mimetype="text/csv",
)

email_message_queue(email_message=email)
```

Note: `email_message_attach` requires the message to be in `READY` status. Call
`email_message_prepare` first, then attach files, then call
`email_message_queue` (which skips re-preparing an already-`READY` message).

#### Cooldown / rate limiting

`email_message_queue` has built-in duplicate suppression. By default, it
prevents sending the same template to the same recipient by the same user more
than once within 180 seconds.

```python
# Defaults: 180s cooldown, 1 allowed, scoped to user + template + recipient
email_message_queue(email_message=email)

# Custom cooldown: allow 3 of the same template to the same recipient in 60s
email_message_queue(
    email_message=email,
    cooldown_period=60,
    cooldown_allowed=3,
    scopes=["template_prefix", "to"],
)

# No cooldown scoping: suppress if ANY email was sent in the last 60s
email_message_queue(
    email_message=email,
    cooldown_period=60,
    cooldown_allowed=1,
    scopes=[],
)
```

Available scopes: `"created_by"`, `"template_prefix"`, `"to"`. Suppressed
emails are saved with status `CANCELED`.

#### Duplicating a sent email

```python
from harry.email.services import email_message_duplicate

duplicate = email_message_duplicate(original=email)
# duplicate is a new EmailMessage in READY status with all attachments copied
email_message_queue(email_message=duplicate)
```

#### Querying email history

All emails are persisted as Django model instances:

```python
from harry.email.models import EmailMessage

# All emails to a recipient
EmailMessage.objects.filter(to_email="alice@example.com")

# Failed emails
EmailMessage.objects.filter(status="error")

# Bounced emails in the last 24 hours
from django.utils import timezone
from datetime import timedelta

EmailMessage.objects.filter(
    status="bounced",
    sent_at__gte=timezone.now() - timedelta(hours=24),
)
```

### Reference: email status lifecycle

Every `EmailMessage` moves through these statuses:

```
                 CANCELED
                 ▲
                 │
NEW ──> READY ──┼──> PENDING ──> SUBMITTED ──> DELIVERED
                │      │                          │
                ▼      ▼                          ├──> OPENED ──> CLICKED
              ERROR  ERROR                        ├──> BOUNCED
                                                  ├──> REJECTED
                                                  ├──> COMPLAINED
                                                  └──> UNSUBSCRIBED
```

Everything after `SUBMITTED` comes from ESP tracking webhooks. The ESP statuses
match Anymail's normalized event types one for one, so each webhook's
`event_type` is stored as-is. Unrecognized event types are stored as `UNKNOWN`.

| Status | Meaning |
|---|---|
| `NEW` | Created, not yet prepared |
| `READY` | Prepared with defaults applied, validated, ready to send |
| `PENDING` | Handed off to the email backend |
| `SUBMITTED` | Backend accepted the message and returned a message ID |
| `QUEUED` | ESP confirmed it has the message (Mailgun calls this "accepted") |
| `SENT` | ESP has handed the message to the recipient's mail server |
| `DEFERRED` | Soft failure, ESP will retry |
| `DELIVERED` | ESP confirmed delivery to recipient's mail server |
| `AUTORESPONDED` | Recipient's mail server sent an automatic reply |
| `OPENED` | Recipient opened the email (if tracking is enabled) |
| `CLICKED` | Recipient clicked a link in the email (if tracking is enabled) |
| `BOUNCED` | Delivery failed (hard or soft bounce) |
| `REJECTED` | ESP rejected the message before delivery |
| `FAILED` | ESP was unable to send the message |
| `COMPLAINED` | Recipient marked the email as spam |
| `UNSUBSCRIBED` | Recipient unsubscribed via email headers |
| `SUBSCRIBED` | Recipient re-subscribed |
| `UNKNOWN` | ESP reported an unrecognized event type |
| `CANCELED` | Suppressed by cooldown |
| `ERROR` | Something went wrong during preparation or sending |

## Development

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies (--all-extras so the email tests run)
uv sync --all-extras
```

### Running Tests

```bash
uv run pytest
```

### Code Quality

Format and lint code:

```bash
make format
```

Run type checking:

```bash
make mypy
```

### Database Migrations

Create new migrations:

```bash
make migrations
```
