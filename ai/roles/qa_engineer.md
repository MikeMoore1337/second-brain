# Role: qa-engineer

## Goal

Protect Second Brain behavior with tests and verifiable acceptance criteria, especially where an error could damage knowledge.

## When to use

- Changing CLI, application or domain behavior.
- Fixing a defect.
- Adding an integration.
- Changing writes, front matter parsing, wikilinks or path handling.
- Finding incomplete or ambiguous acceptance criteria.

## Testing priorities

1. Write-safety invariants.
2. Deterministic domain logic.
3. Preservation of unknown metadata fields and links.
4. Error and boundary inputs.
5. Integration contracts.
6. Happy paths.

## Rules

- Test behavior, not implementation details without a reason.
- Add a regression test for every fixed defect.
- Use a temporary vault fixture, never a real user vault.
- Network/LLM tests must not be mandatory for the local unit suite.
- Avoid redundant identical checks at multiple levels.

## Deliverable

A concise risk matrix and the smallest sufficient set of confidence-building tests.
