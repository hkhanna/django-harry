# ADR 0002: Fleet-wide baseline alerts in Terraform, not per-service provisioning

- Status: accepted
- Date: 2026-07-06

## Context

All projects ship telemetry to one shared SigNoz Cloud tenant, identified per
project by `OTEL_SERVICE_NAME`. The standard alert set has to hold for every
project, present and future — "consistent across all projects" was the design
goal from the start.

The first mechanism (the original §7 of `docs/observability-signoz.md`) was
per-service provisioning: create the rules by hand for a reference service,
export them as `{{SERVICE}}` templates, and re-stamp them for each new project
with `scripts/signoz-alerts.py`. That shape made two assumptions worth
questioning: that rules must be per-service (so every new project needs a
provisioning step, the drift point the doc itself named), and that Terraform
would be net-new overhead (every project already carries Terraform for its
VPS, and `infra-misc` already holds fleet-level Terraform with a DigitalOcean
Spaces state backend).

Two facts dissolved the assumptions. SigNoz alert rules take a `Group By` on
`service.name` and evaluate each group independently — one rule behaves like N
per-service rules, including for services that don't exist yet. And SigNoz
ships a first-party Terraform provider (`SigNoz/signoz`) with a
`signoz_alert` resource and import support.

## Decision

The standard alert set is **five fleet-wide rules, declared once in
Terraform** (`infra-misc/terraform/signoz.tf`), each grouped by `service.name`
and filtered to `deployment.environment = prod`. No alert is ever provisioned
per project; a new service is covered the moment it emits telemetry.
`scripts/signoz-alerts.py` is deleted.

Supporting decisions made with it:

- **Home**: the SigNoz tenant's config lives in `infra-misc` (the existing
  fleet-level infra repo), not in this library and not in any one project —
  fleet config must not be coupled to a single project's lifecycle, and this
  repo stays pure code. The trade accepted: a change to `JSONFormatter`'s
  field contract and the alert queries that depend on it now spans two repos.
- **Tuning is data**: per-service threshold overrides are entries in a map in
  `signoz.tf`, from which both the tuned rule and the baseline carve-out are
  computed — they cannot drift apart, and a tune is a one-line PR.
- **Rule 5 (hygiene)** was added to the standard set: any ERROR log carrying
  no `service.name`. Group-by rules are blind to records without the grouping
  key, so an execution context missing `OTEL_SERVICE_NAME` (typically cron)
  would otherwise fail invisibly. Its companion doctrine: the OTEL env vars
  are set in *every* execution context, not just the app unit and Caddy.
- **The notification channel stays a manual, one-time UI step** (the provider
  has no channel resource as of v0.0.14); rules reference it by name.
- **External uptime checks stay manual checklist steps**: Terraform is used
  where the provider is first-party; UptimeRobot/Healthchecks.io community
  providers are not worth the dependency for set-once-per-project config.

## Consequences

- The per-project SigNoz procedure shrinks to verification (service row,
  log↔trace click-through) — there is no provisioning step to forget, which
  eliminates the drift point rather than automating it.
- Rules are authored once in the UI and adopted via `terraform import`; from
  then on the UI is read-only for managed rules — a UI edit is silently
  reverted on the next apply. Tuning happens only through the overrides map.
- One threshold per baseline rule fleet-wide is the default posture; a
  service that needs different treatment must be carved out explicitly, which
  is deliberate friction.
- Log-based fleet rules filter on `deployment.environment`, which journald
  logs don't naturally carry — every host collector's logs pipeline must
  stamp it (canonical collector config, §1 of the observability doc).
- Re-opening this decision requires group-by evaluation proving inadequate in
  practice (e.g. most services ending up carved out), not discomfort with
  fleet-wide thresholds.
