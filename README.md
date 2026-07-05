# harry

Orgs, emails, and events for Django

## Development

### Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies (--all-extras so the OpenTelemetry integration tests run)
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

The merge is shallow: an entry in `extra_loggers` replaces a built-in logger wholesale, so
when overriding one (say, `django.request`) spell out `handlers` and `propagate` too.

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
(installing `harry[otel]` and calling `init_observability()` in settings), your
Collector-shipped logs automatically correlate with traces in SigNoz — no in-app log
exporter and no change to harry required. See "Request logging & tracing" below for the
full setup.

## Request logging & tracing

harry can log one structured access line per request and — with OpenTelemetry — tie every
log line to a distributed trace that spans reverse proxy, app server, and Django.

**Required vs. optional.** Everything in this section beyond the middleware itself is an
optional enhancer, not a prerequisite:

| Tool | Required? | Notes |
|---|---|---|
| OpenTelemetry | No | With it, every log line carries `trace_id` and links to its trace in SigNoz. Without it, the middleware still logs fully. No OpenTelemetry package is a hard dependency of harry — they live in the opt-in `harry[otel]` extra. |
| Caddy | No | Any reverse proxy, or none. Caddy's `tracing` is only needed for proxy↔app trace correlation. |
| gunicorn | No | Any WSGI/ASGI server. The snippet below is just an example of surfacing `traceparent` at the server layer. |
| SigNoz / OTel Collector | No | harry only writes JSON to stdout; shipping it anywhere is a deployment choice. |

### Access logging

Add the middleware to `MIDDLEWARE`, after `AuthenticationMiddleware` (it reads
`request.user`):

```python
MIDDLEWARE = [
    # ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # ...
    "harry.middleware.RequestLogMiddleware",
]
```

Each request emits one line at `INFO` on the `harry.request` logger — readable in
development (`GET /invoices/42 200 (12ms)`), structured in production:

```json
{"ts": "…", "level": "INFO", "logger": "harry.request", "msg": "GET /invoices/42 200 (12ms)",
 "method": "GET", "path": "/invoices/42", "status": 200, "duration_ms": 12, "user_id": 7}
```

Field notes:

- `duration_ms` measures middleware entry to response return — for streaming responses
  that is time-to-headers, not time-to-last-byte.
- `user_id` is the authenticated user's primary key, else `null`.
- Client IP, user agent, and response size are deliberately absent — your reverse proxy's
  access log owns those. The query string is also excluded: it's the classic place
  password-reset tokens and magic links leak into logs. The request id concept is absent
  too — when tracing is on, `trace_id` is the per-request id on every line.

There is no on/off setting: membership in `MIDDLEWARE` is the switch. To silence access
lines in one environment without touching `MIDDLEWARE`, raise the logger's level:

```python
LOGGING = build_logging_config(
    extra_loggers={"harry.request": {"level": "WARNING", "handlers": ["console"], "propagate": False}},
)
```

#### Ignoring noise endpoints

Requests whose path is in `REQUEST_LOG_IGNORE_PATHS` get no access line. Entries ending
in `/` match as path prefixes; all other entries match exactly — never by substring, so
an entry can't silently swallow a real route. The default:

```python
REQUEST_LOG_IGNORE_PATHS = {
    "/favicon.ico",
    "/robots.txt",
    "/apple-touch-icon.png",
    "/apple-touch-icon-precomposed.png",
    "/.well-known/",   # trailing slash → prefix match
}
```

Healthcheck paths are deliberately *not* ignored: a probe every 30 seconds is a useful
status/latency heartbeat in SigNoz. If you find it too chatty, add your healthcheck path
to the set (overriding replaces the default set wholesale).

### Correlating logs with traces

Install harry's `otel` extra and call `init_observability()` in `settings.py`, and every
log line — access lines included — carries the request's `trace_id`/`span_id`, which
harry's JSON formatter emits and SigNoz uses to link logs to traces:

```bash
pip install "harry[otel]"
```

```python
# settings.py
from harry.observability import init_observability

init_observability()  # OTEL_SERVICE_NAME + OTEL_EXPORTER_OTLP_ENDPOINT from env
```

```bash
OTEL_SERVICE_NAME=myapp \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod \
gunicorn myproject.wsgi
```

That one call wires everything:

- a `TracerProvider` with an OTLP span exporter — endpoint, headers, and protocol (gRPC
  by default; set `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` for HTTP) come from the
  standard `OTEL_EXPORTER_OTLP_*` env vars, the service name from the argument
  (`init_observability(service_name="myapp")`) or `OTEL_SERVICE_NAME`;
- Django, psycopg, and requests instrumentation — each enabled only when the library is
  installed;
- log↔trace correlation — trace ids are stamped onto every `LogRecord`, and harry's JSON
  formatter promotes them to `trace_id`/`span_id`/`service.name`.

Because initialization is programmatic — there is no `opentelemetry-instrument` wrapper —
the same `settings.py` works identically under gunicorn, `manage.py` commands, and any
future task runner without changing how processes are launched. Calling it twice (e.g.
under the autoreloader) is a no-op, and calling it without the extra installed raises an
`ImportError` that tells you to install `harry[otel]`. The extra is also the single place
the interdependent `opentelemetry-*` package versions are managed — don't pin them
yourself.

<details>
<summary>Manual path (without the <code>harry[otel]</code> extra)</summary>

To assemble your own instrumentation set instead, OpenTelemetry's zero-code path works
with harry unchanged — the formatter promotes the trace ids either way:

```bash
pip install opentelemetry-distro opentelemetry-exporter-otlp
opentelemetry-bootstrap -a install   # detects Django etc. and installs instrumentations
```

```bash
OTEL_SERVICE_NAME=myapp \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod \
OTEL_PYTHON_LOG_CORRELATION=true \
opentelemetry-instrument gunicorn myproject.wsgi
```

`OTEL_PYTHON_LOG_CORRELATION=true` is the piece that stamps trace ids onto log records;
without it you get traces but uncorrelated logs.

</details>

### One trace across the full lifecycle

For the proxy, app server, and Django to share a single trace id, the proxy must *join
the trace*. With Caddy, that's the `tracing` directive:

```
example.com {
    tracing
    reverse_proxy 127.0.0.1:8000
}
```

Caddy continues an inbound W3C trace context or starts a new trace, exports its own span
(honoring the standard `OTEL_EXPORTER_OTLP_*` / `OTEL_SERVICE_NAME` env vars), injects
`traceparent` into the proxied request — so Django's instrumentation continues the same
trace — and stamps `traceID`/`spanID` onto its own access logs. The result in SigNoz:
Caddy's span, Django's spans, and every app log line share one trace id.

To also surface the id in gunicorn's access log:

```python
# gunicorn.conf.py
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sus traceparent=%({traceparent}i)s'
```

**Caveat:** without Caddy `tracing` (or another tracing proxy), the trace originates in
Django — requests still trace and logs still correlate, but the proxy hop is not part of
the trace. "One id, full lifecycle" specifically requires the proxy to participate.
