# SigNoz setup: the receiving side

The [README](../README.md) documents what the code ships: JSON logs on stdout,
OTLP traces, a `/health` endpoint. This doc is the other half — what gets
configured in SigNoz and adjacent infrastructure so that every project lands in
the same panes with the same alerts. Sections 3, 4, 6, and 7 are done once per
SigNoz tenant, section 1 once per host; everything in
"[Per-project setup](#8-per-project-setup)" repeats for each new project.

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
  resource/env:                         # journald logs never pass through the OTel SDK,
    attributes:                         # so the environment must be stamped here — the
      - key: deployment.environment    # fleet alert rules (§4) filter logs on it
        value: prod                     # (match the host: prod or staging)
        action: upsert
  batch: {}
exporters:
  otlp:
    endpoint: ingest.<region>.signoz.cloud:443   # or your self-hosted SigNoz OTLP endpoint
    headers: { signoz-ingestion-key: "${env:SIGNOZ_INGESTION_KEY}" }
service:
  pipelines:
    traces:  { receivers: [otlp],        processors: [batch],                                  exporters: [otlp] }
    logs:    { receivers: [journald],    processors: [resource/env, resourcedetection, batch], exporters: [otlp] }
    metrics: { receivers: [hostmetrics], processors: [resourcedetection, batch],               exporters: [otlp] }
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
channel. This stays a one-time UI step — the Terraform provider has no channel
resource (as of v0.0.14) — and the Terraform rules (§7) reference the channel
by name, so it must exist before the first apply.

## 4. Standard alert set (fleet-wide)

Five rules for the whole fleet, no more — not five per service. Each rule
groups by `service.name` (SigNoz evaluates every group as its own series and
fires with the service name in the notification) and filters on
`deployment.environment = prod`. The consequence that matters: **a new project
is covered by all five the moment it ships telemetry.** There is no
per-project alert provisioning, and therefore nothing to forget or drift.

| # | Alert | Type | Condition | Why this shape |
|---|---|---|---|---|
| 1 | Error rate | trace-based | error span percentage > **5%** over **5 min**, per service | a percentage, not a count, so quiet and busy services share one threshold |
| 2 | New exception | exceptions-based | any new-or-recurring exception, per service | highest-signal alert for a solo operator: fires once on novelty rather than repeatedly on volume |
| 3 | p95 latency | trace-based | p95 > **1 s** for **10 min**, per service | p95, never averages — averages hide the slow tail users actually feel |
| 4 | ERROR-log heartbeat | log-based | any log with `severity = ERROR`, per service, over **5 min** | safety net for failures outside request spans (management commands, startup, cron) that trace rules never see |
| 5 | Hygiene | log-based | any `severity = ERROR` log with **no `service.name` at all** | rule 4 groups by service name, so an execution context missing `OTEL_SERVICE_NAME` is invisible to it — this is the canary for telemetry that lost its identity |

Rule 5 monitors the telemetry pipeline, not any service's health. It exists
because every other log/trace rule keys on `service.name`; §8 step 1 is the
doctrine (env vars in *every* execution context) that keeps rule 5 quiet.

Thresholds are fleet-wide starting points. When one service genuinely needs a
different value, don't touch the UI — add an entry to the overrides map in the
infra repo (§7). The tuned rule and the baseline carve-out are both computed
from that one entry, so they can't drift apart.

Deliberately absent: CPU / memory / disk alerts. Symptoms over causes (the
README's alerting principles) — saturation users feel shows up as errors or
latency, which rules 1–4 already cover. Host metrics stay on dashboards.

Alert-type support was verified against SigNoz docs (May 2026, group-by
semantics re-verified July 2026): trace-based and log-based rules take a
`Group By` on `service.name` and evaluate per group, notification templates
can interpolate `$service.name`, and exceptions-based rules are ClickHouse
queries that can group by service in SQL. References:
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

## 7. Alert provisioning: Terraform in the infra repo

**Decision: fleet-wide Terraform, not per-service provisioning.** (Recorded in
[ADR 0002](adr/0002-fleet-baseline-alerts-not-per-service-provisioning.md);
this supersedes the earlier per-service script mechanism.) The five rules in
§4 exist exactly once, so they are declared exactly once — in the repo that
owns the SigNoz tenant's configuration (`infra-misc`, `terraform/signoz.tf`),
using the first-party
[SigNoz Terraform provider](https://registry.terraform.io/providers/SigNoz/signoz/latest/docs)
and the same DigitalOcean Spaces state backend as the rest of that root.
Applies are manual `terraform apply` from the laptop. Nothing is provisioned
per project, ever.

Auth: the provider reads `SIGNOZ_ACCESS_TOKEN` (an API key with admin role)
from the environment. The API endpoint is set explicitly in the provider
block, because in that repo `SIGNOZ_ENDPOINT` already means the OTLP *ingest*
endpoint the collectors use — a different URL.

**Per-service tuning is data, not hand-made rules.** `signoz.tf` holds an
overrides map (e.g. `p95_overrides_ms = { slow-reports = 2500 }`). From that
one entry Terraform derives both the service's tuned rule and the carve-out
that removes the service from the baseline rule's filter — so a tune is a
one-line PR, and the carve-out can never drift from its override. If a tuned
value proves right in general, promote it to the baseline default and delete
the entry.

**Bootstrap** (already done once per tenant; recorded for reconstruction):
create the notification channel in the UI (§3); author the five rules once in
the UI, where the query builder is the only reliable way to produce the
condition JSON; then adopt them with Terraform `import` blocks and reconcile
each resource's `condition` against `terraform state show`. After adoption the
UI is read-only for these rules — a UI edit to a managed rule is silently
reverted on the next apply.

## 8. Per-project setup

The SigNoz-side steps behind the README's
"[launch checklist](../README.md#per-project-integration-checklist)", in
order:

1. **Set the two env vars in every execution context** —
   `OTEL_SERVICE_NAME=<project>` and
   `OTEL_RESOURCE_ATTRIBUTES=deployment.environment=prod` — on the app
   service, on Caddy, **and on anything else that runs the code: cron jobs,
   systemd timers, one-off management commands** (§2). A context that misses
   `OTEL_SERVICE_NAME` emits logs with no service identity: the heartbeat rule
   can't see them, and the hygiene rule (§4 rule 5) will page about them.
   Ensure the host's collector `journald` receiver lists the new app's systemd
   unit and its logs pipeline stamps `deployment.environment` (§1).
2. **Confirm the service row appears** in SigNoz's Services list after the
   first traffic, and that its log lines carry the right severity and
   `service.name`.
3. **Verify log↔trace click-through**: open a JSON log line in the logs pane
   and click through to the trace waterfall. If the link is missing, the §1
   `trace` mapping isn't applied — a shared `trace_id` string attribute alone
   won't link.
4. **Nothing to provision in SigNoz** — the fleet baseline (§4) covers the new
   service automatically. Only if a threshold needs tuning later, add an entry
   to the overrides map in the infra repo (§7).
5. **Register `/health/` with the uptime monitor** (§5).
6. **Add Healthchecks.io pings** to the project's cron jobs (§5).
