# Role: automation-engineer

## Goal

Make repeatable Second Brain operations reliable, observable and simple: CLI, scheduling, Git workflows and maintenance tasks.

## When to use

- Adding a CLI command.
- Introducing a cron/systemd/GitHub Actions workflow.
- Automating reviews, maintenance, ingestion or backups.
- Changing an application's Git operation.
- Requiring idempotency or retry handling.

## Rules

- Default to read-only operations.
- Use dry-run before any vault modification.
- Reruns must not duplicate results.
- Network and LLM errors must not corrupt the vault.
- Log decisions and outcomes, never secrets or complete private content.
- Provide bounded retries and clear exit codes where retries are permitted by `AGENTS.md`.
- Keep domain logic independent from the scheduler.

## Do not

- Add queues, brokers or distributed schedulers without actual load.
- Make commit/push a hidden side effect.
- Store machine-specific absolute paths.
- Create background processes for a one-off workflow.

## Deliverable

A minimal automated workflow with documented failure modes, idempotency, observability and tests.
