# harry

Orgs, emails, and events for Django

## Development

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies
uv sync
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

## Usage

### Installation

```bash
pip install harry
```

### 1. Add to INSTALLED_APPS

```python
INSTALLED_APPS = [
    # ...
    "anymail",
    "harry.email",
]
```

### 2. Configure your email provider

harry uses [django-anymail](https://anymail.dev/) to send email through any
transactional ESP. Configure your provider in `settings.py`:

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

### 3. Configure site defaults

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

### 4. Create email templates

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

### 5. Run migrations

```bash
python manage.py migrate
```

### Sending email

The primary API is a set of service functions. Every email sent is persisted as
an `EmailMessage` record in your database.

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

### Tracking delivery with webhooks

harry stores webhook events from your ESP so you can track whether emails were
delivered, opened, bounced, or marked as spam.

#### 1. Set up anymail webhook URLs

Add anymail's webhook URLs to your root `urls.py`:

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

### Email status lifecycle

Every `EmailMessage` moves through these statuses:

```
                 CANCELED
                 ▲
                 │
NEW ──> READY ──┼──> PENDING ──> ACCEPTED ──> DELIVERED
                │      │                         │
                ▼      ▼                         ├──> OPENED ──> CLICKED
              ERROR  ERROR                       ├──> BOUNCED
                                                 ├──> REJECTED
                                                 ├──> COMPLAINED
                                                 └──> UNSUBSCRIBED
```

Unrecognized ESP event types are stored as `UNKNOWN`.

| Status | Meaning |
|---|---|
| `NEW` | Created, not yet prepared |
| `READY` | Prepared with defaults applied, validated, ready to send |
| `PENDING` | Handed off to the email backend |
| `ACCEPTED` | Backend accepted the message |
| `DELIVERED` | ESP confirmed delivery to recipient's mail server |
| `OPENED` | Recipient opened the email (if tracking is enabled) |
| `CLICKED` | Recipient clicked a link in the email (if tracking is enabled) |
| `BOUNCED` | Delivery failed (hard or soft bounce) |
| `REJECTED` | ESP rejected the message before delivery |
| `COMPLAINED` | Recipient marked the email as spam |
| `UNSUBSCRIBED` | Recipient unsubscribed via email headers |
| `UNKNOWN` | ESP reported an unrecognized event type |
| `CANCELED` | Suppressed by cooldown |
| `ERROR` | Something went wrong during preparation or sending |

### Querying email history

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

## Logging

harry ships a reusable logging configuration so every project that installs it logs the same
way. It has no extra dependencies: it builds a standard Django
[`LOGGING`](https://docs.djangoproject.com/en/5.2/topics/logging/) dictionary.

### Setup

In your project's `settings.py`:

```python
from harry.logconfig import build_logging_config

LOGGING = build_logging_config()
```

That's it. By default the configuration:

- logs human-readable, colorless console output in development and **structured JSON** in
  production;
- writes to **stdout** (12-factor) — no file handlers, so the platform (systemd, Docker, your
  log collector) owns persistence and rotation;
- keeps `disable_existing_loggers` off so Django's and third-party loggers keep working;
- configures the `django`, `django.request`, `django.security`, and `harry` loggers plus the
  root logger.

### Configuration

`build_logging_config()` resolves each setting from its keyword argument, then an environment
variable, then a per-environment default:

| Argument | Env var | Values | Default |
|---|---|---|---|
| `env` | `DJANGO_ENV` | `local` / `test` / `prod` | `local` |
| `level` | `DJANGO_LOG_LEVEL` | any logging level | `DEBUG` (local), `WARNING` (test), `INFO` (prod) |
| `fmt` | `DJANGO_LOG_FORMAT` | `console` / `json` | `console` (local/test), `json` (prod) |

```python
# Add or override loggers for your project without losing the defaults:
LOGGING = build_logging_config(
    extra_loggers={"myapp": {"level": "INFO", "handlers": ["console"], "propagate": False}},
)
```

`env` is read from `os.environ`, not `settings.DEBUG`, because the builder runs while
`settings.py` is still being evaluated. Set `DJANGO_ENV` in each environment.

A production log line looks like:

```json
{"ts": "2026-06-30T12:34:56.789012+00:00", "level": "INFO", "logger": "harry.email.services", "msg": "Sent email message", "message_id": "…"}
```

Anything you pass via `logger.info("…", extra={"message_id": mid})` is merged into the JSON.

### Shipping logs to SigNoz (production)

The production assumption is [SigNoz](https://signoz.io/). Because you'll already run an
OpenTelemetry Collector on the host for OS/VPS metrics and system logs, the simplest setup is
to let that **same Collector** pick up the application logs — no OpenTelemetry packages in the
app itself.

1. Run Django under **systemd** (e.g. gunicorn). harry's JSON goes to stdout, which systemd
   captures in the **journal**.
2. Install the OpenTelemetry Collector on the host and give it one pipeline for host metrics
   and one for logs. Sketch:

   ```yaml
   receivers:
     hostmetrics:
       collection_interval: 60s
       scrapers: { cpu: {}, memory: {}, disk: {}, filesystem: {}, load: {}, network: {} }
     journald:
       units: [your-django.service]      # or a `filelog` receiver if you log to a file
       start_at: end
       operators:
         # journald delivers the entry as a map; collapse it to the MESSAGE line...
         - type: move
           from: body.MESSAGE
           to: body
           if: 'body.MESSAGE != nil'
         # ...then parse harry's JSON. Recover the real severity from the `level`
         # field — journald otherwise stamps everything on stdout as INFO, which
         # would flatten an app ERROR to INFO in SigNoz.
         - type: json_parser
           if: 'body != nil and body matches "^[{]"'
           parse_from: body
           parse_to: attributes
           severity:
             parse_from: attributes.level
             mapping: { debug: DEBUG, info: INFO, warn: WARNING, error: ERROR, fatal: CRITICAL }
         # Surface the human message as the log body in SigNoz (level/logger/func/
         # lineno and any extras remain queryable attributes).
         - type: move
           from: attributes.msg
           to: body
           if: 'attributes.msg != nil'
   processors:
     resourcedetection: { detectors: [system] }
     batch: {}
   exporters:
     otlp:
       endpoint: ingest.<region>.signoz.cloud:443
       headers: { signoz-ingestion-key: "${env:SIGNOZ_INGESTION_KEY}" }
   service:
     pipelines:
       logs:    { receivers: [journald],    processors: [resourcedetection, batch], exporters: [otlp] }
       metrics: { receivers: [hostmetrics], processors: [resourcedetection, batch], exporters: [otlp] }
   ```

   The two pieces that matter: the `json_parser` recovers severity from harry's `level`
   field (without it, journald reports every line as INFO), and moving `msg` to the body
   gives a clean message in SigNoz with everything else as attributes. If you also emit
   `trace_id`/`span_id` (see below), they flow through `parse_to: attributes` and SigNoz
   correlates the log with its trace. See the SigNoz docs for the authoritative configuration:
   [install the Collector on a VM](https://signoz.io/docs/opentelemetry-collection-agents/vm/install/),
   [host metrics](https://signoz.io/docs/infrastructure-monitoring/hostmetrics/),
   [systemd/journald logs](https://signoz.io/docs/logs-management/send-logs/collect-systemd-logs/),
   [logs from a file](https://signoz.io/docs/userguide/collect_logs_from_file/).

**Trace↔log correlation.** harry's JSON formatter emits `trace_id`/`span_id`/`service.name`
whenever OpenTelemetry has populated them on the log record. So if you later add tracing
(running the app under `opentelemetry-instrument` with the `OTEL_*` environment variables),
your Collector-shipped logs automatically correlate with traces in SigNoz — no in-app log
exporter and no change to harry required.
