# SigNoz setup: the receiving side

The [README](../README.md) documents what the code ships: JSON logs on stdout,
OTLP traces, a `/health` endpoint. This doc is the other half — what gets
configured in SigNoz and adjacent infrastructure so that every project lands in
the same panes with the same alerts. Sections 1, 3, and 6 are done once per
SigNoz instance/host; everything in "[Per-project setup](#8-per-project-setup)"
repeats for each new project.

## 1. Collector ingest (once per host)

The host runs one OpenTelemetry Collector with two app-facing pipelines (plus
host metrics for dashboards):

- **Traces**: an `otlp` receiver on gRPC `:4317`, targeted by both Caddy's
  `tracing` directive and `init_observability()`; exported to SigNoz.
- **Logs**: a `journald` receiver over the app service's stdout (systemd
  captures it in the journal), with operators that translate harry's JSON into
  the OTel log data model.

The log operators are the deployment half of the contract that
`harry.logconfig.JSONFormatter` defines. Four mappings do the real work:

| JSON field | Mapped to | Why |
|---|---|---|
| `level` | log record **severity** | SigNoz filters and alerts on severity, not arbitrary JSON keys; journald otherwise stamps everything on stdout as INFO, flattening an app ERROR to INFO |
| `ts` | record **timestamp** | the record carries the time the app logged, not the time the collector read the journal |
| `trace_id` / `span_id` | the OTel log data model's **trace fields** | this mapping is what makes log↔trace click-through work in SigNoz — a shared string attribute is not enough |
| `msg` | log **body** | a clean human message in the logs pane; `logger`, `func`, `lineno`, and any `extra` fields remain queryable attributes |

### Canonical collector configuration

This snippet is kept in sync with `JSONFormatter`'s field names
(`ts`/`level`/`logger`/`func`/`lineno`/`msg`, plus `trace_id`/`span_id`/
`service.name` when tracing is on). If the formatter's fields change, this
config changes with them.

```yaml
receivers:
  otlp:                                 # traces from Caddy `tracing` + init_observability()
    protocols:
      grpc:
        endpoint: 127.0.0.1:4317        # app and proxy are on-host; don't expose publicly
  hostmetrics:                          # OS metrics, for dashboards (never alerts — see §4)
    collection_interval: 60s
    scrapers: { cpu: {}, memory: {}, disk: {}, filesystem: {}, load: {}, network: {} }
  journald:
    units: [your-django.service]        # one entry per app service on the host
    start_at: end
    operators:
      # journald delivers the entry as a map; collapse it to the MESSAGE line...
      - type: move
        from: body.MESSAGE
        to: body
        if: 'body.MESSAGE != nil'
      # ...then parse harry's JSON. Severity comes from `level` (journald would
      # otherwise stamp everything INFO), the record timestamp from `ts`, and
      # trace_id/span_id land in the log data model's trace fields — that last
      # mapping is what makes log↔trace click-through work.
      - type: json_parser
        if: 'body != nil and body matches "^[{]"'
        parse_from: body
        parse_to: attributes
        severity:
          parse_from: attributes.level
          mapping: { debug: DEBUG, info: INFO, warn: WARNING, error: ERROR, fatal: CRITICAL }
        timestamp:
          parse_from: attributes.ts
          layout_type: gotime
          layout: '2006-01-02T15:04:05.999999-07:00'   # JSONFormatter's ISO 8601 (UTC offset form)
        trace:
          trace_id:
            parse_from: attributes.trace_id
          span_id:
            parse_from: attributes.span_id
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
    endpoint: ingest.<region>.signoz.cloud:443   # or your self-hosted SigNoz OTLP endpoint
    headers: { signoz-ingestion-key: "${env:SIGNOZ_INGESTION_KEY}" }
service:
  pipelines:
    traces:  { receivers: [otlp],        processors: [batch],                    exporters: [otlp] }
    logs:    { receivers: [journald],    processors: [resourcedetection, batch], exporters: [otlp] }
    metrics: { receivers: [hostmetrics], processors: [resourcedetection, batch], exporters: [otlp] }
```

Log lines that never ran inside a span (management commands, startup) simply
have no `trace_id` — the `trace` block is a no-op for them. When tracing is on,
every line also carries a `service.name` attribute (promoted by
`JSONFormatter`), which is what the log-based alert in §4 filters on.

SigNoz's authoritative references:
[install the Collector on a VM](https://signoz.io/docs/opentelemetry-collection-agents/vm/install/),
[systemd/journald logs](https://signoz.io/docs/logs-management/send-logs/collect-systemd-logs/),
[host metrics](https://signoz.io/docs/infrastructure-monitoring/hostmetrics/).

## 2. Service identity

Every project — and its Caddy — sets the same two env vars (they're in the
README's launch checklist):

```bash
OTEL_SERVICE_NAME=<project>
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=<prod|staging>
```

The consequence to internalize: **SigNoz's Services list is the fleet
dashboard.** One row per project, with RED metrics (rate / error % / p95)
derived from spans, for free. Do not build per-project dashboards — dashboard
sprawl is the failure mode, and everything a per-project dashboard would show
is already in the service's row, its traces, and its logs.

## 3. Notification channel

One channel, created **before any alert rules** (Settings → Alert Channels) —
email, Slack, or ntfy via webhook; whatever reliably reaches the phone. No
routing policies at this scale: every alert from every service goes to the one
channel. Rules reference the channel by name, so creating it first means the
exported alert templates (§7) carry a working `preferredChannels` value.

## 4. Standard alert set (per service)

Four alerts per service, no more. Thresholds are starting points — tune per
project, but change the template (§7) when a tuned value proves right
everywhere.

| # | Alert | Type | Condition | Why this shape |
|---|---|---|---|---|
| 1 | Error rate | trace-based | error span percentage > **5%** over **5 min** | a percentage, not a count, so quiet and busy services share one threshold |
| 2 | New exception | exceptions-based | any new-or-recurring exception for the service | highest-signal alert for a solo operator: fires once on novelty rather than repeatedly on volume |
| 3 | p95 latency | trace-based | p95 > **1 s** for **10 min** (tune per project) | p95, never averages — averages hide the slow tail users actually feel |
| 4 | ERROR-log heartbeat | log-based | any log with `severity = ERROR` and `service.name = <project>` over **5 min** | safety net for failures outside request spans (management commands, startup, cron) that trace rules never see |

Deliberately absent: CPU / memory / disk alerts. Symptoms over causes (the
README's alerting principles) — saturation users feel shows up as errors or
latency, which alerts 1–4 already cover. Host metrics stay on dashboards.

Alert-type support was verified against SigNoz docs (May 2026): metrics-based,
log-based, trace-based, anomaly-based, and exceptions-based ("new or recurring
exceptions") rules all exist. References:
[alert types](https://signoz.io/docs/alerts-management/alert-types/),
[trace-based alerts](https://signoz.io/docs/alerts-management/trace-based-alerts/),
[log-based alerts](https://signoz.io/docs/alerts-management/log-based-alerts/).

## 5. External uptime (what SigNoz cannot do)

SigNoz only sees telemetry that arrives. If the server, Caddy, or the collector
dies, telemetry stops and SigNoz goes *quiet* — no rule inside it can tell
"healthy and silent" from "dead and silent". Two outside-the-walls checks
cover that:

- **An external pinger** (e.g. [UptimeRobot](https://uptimerobot.com/)) against
  each web app's `/health/` endpoint, probing from outside the infrastructure.
- **[Healthchecks.io](https://healthchecks.io/) pings from cron jobs on
  completion** — catches "the nightly job silently stopped running," which
  nothing inside SigNoz observes.

## 6. Retention

Set retention explicitly (Settings → General) rather than accepting defaults —
logs from N projects land on one ClickHouse and grow unbounded. Starting
point:

| Signal | Retention |
|---|---|
| Traces | 15 days |
| Logs | 30 days |
| Metrics | 90 days |

Raise logs first if forensics ever demand it.

## 7. Alert provisioning: the repeat mechanism

The four alerts are per-service, so every new project means recreating them —
this is the drift point for "consistent across all projects." The mechanism:

**Decision: a script, not Terraform.** The four rules are created once by hand
in the UI (for the reference project), exported as JSON templates via the
SigNoz API, and re-created for each new service by
[`scripts/signoz-alerts.py`](../scripts/signoz-alerts.py) with the service name
substituted. The [SigNoz Terraform provider](https://registry.terraform.io/providers/SigNoz/signoz/latest/docs)
manages alert resources and is the conscious escalation path — adopt it only
if SigNoz configuration ever sprawls beyond the four alerts. Until then a
50-line script beats a Terraform state file.

The script needs two env vars:

```bash
export SIGNOZ_URL=https://signoz.example.com   # base URL of the SigNoz UI/API
export SIGNOZ_API_KEY=...                      # Settings → API Keys (admin role for rule writes)
```

**Once** — after creating the four rules by hand for the reference project,
export them as templates (checked into this repo):

```bash
scripts/signoz-alerts.py export <reference-service>
# writes scripts/signoz-alert-templates/*.json with the service name
# replaced by the {{SERVICE}} placeholder — review and commit them
```

**Per new project** — re-create the four rules with the service name
substituted:

```bash
scripts/signoz-alerts.py provision <new-service>            # idempotent: skips rules that already exist
scripts/signoz-alerts.py provision <new-service> --dry-run  # print what would be created
```

If a threshold gets hand-tuned in the UI for one service and the tuned value
proves right in general, re-run `export` against that service and commit the
updated templates.

## 8. Per-project setup

The SigNoz-side steps behind the README's
"[launch checklist](../README.md#per-project-integration-checklist)", in
order:

1. **Set the two env vars** — `OTEL_SERVICE_NAME=<project>` and
   `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod` — on the app service
   and on Caddy (§2). Ensure the host's collector `journald` receiver lists the
   new app's systemd unit (§1).
2. **Confirm the service row appears** in SigNoz's Services list after the
   first traffic, and that its log lines carry the right severity and
   `service.name`.
3. **Verify log↔trace click-through**: open a JSON log line in the logs pane
   and click through to the trace waterfall. If the link is missing, the §1
   `trace` mapping isn't applied — a shared `trace_id` string attribute alone
   won't link.
4. **Run the alert script** — `scripts/signoz-alerts.py provision <project>`
   (§7).
5. **Register `/health/` with the uptime monitor** (§5).
6. **Add Healthchecks.io pings** to the project's cron jobs (§5).
