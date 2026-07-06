# harry (Django observability & email primitives)

A library that Django projects install to get uniform logging, tracing, and email behavior — plus the documented conventions that make every project land in one shared SigNoz view the same way.

## Language

### Telemetry & fleet

**Project**:
A deployed Django codebase that installs this library, running on its own VPS with its own Terraform.
_Avoid_: app, site, consumer

**Service**:
A telemetry identity in SigNoz, named by `OTEL_SERVICE_NAME`. Each project emits exactly one service, and every execution context of the project — web, cron, management commands, workers — shares it.
_Avoid_: application, component

**Fleet**:
All services shipping telemetry to the one shared SigNoz Cloud tenant.
_Avoid_: all projects, everything

### Alerting

**Standard alert set**:
The five alert shapes the fleet is covered by: error rate, new exception, p95 latency, ERROR-log heartbeat, and the hygiene rule.
_Avoid_: default alerts, alert templates

**Hygiene rule**:
The fleet-wide alert on ERROR logs carrying no service name — a canary for telemetry that has lost its identity (usually a missing env var), rather than for any service's health.
_Avoid_: catch-all alert, misc alert

**Baseline rule**:
A single fleet-wide alert rule, grouped by service name, that covers every service automatically — including ones that don't exist yet.
_Avoid_: global alert, shared alert

**Override**:
A per-service replacement for one baseline rule, carrying a tuned threshold. A service with an override is carved out of the corresponding baseline rule.
_Avoid_: exception, custom alert, tuned copy
