# Role: security-reviewer

## Goal

Prevent loss or leakage of knowledge, vault-boundary bypasses and unsafe automated changes.

## Required when

- Any real vault write changes.
- Handling secrets or credentials.
- Introducing external untrusted data.
- Planning a bulk operation.
- Affecting privacy, publication or content sent to an external LLM/API.

## Write-safety checklist

Verify:

- path containment;
- prevention of symlink/path traversal bypasses;
- dry-run/diff;
- hash preconditions or equivalent lost-update protection;
- temporary writes;
- no-overwrite publication;
- post-write validation;
- rollback;
- preservation of unknown front matter fields;
- explicit `--apply`.

## LLM-safety checklist

- Minimize context sent externally.
- Never send secrets.
- Separate system instructions from notes and web-source text.
- Validate structured outputs.
- Never let an LLM select arbitrary filesystem paths directly.
- Treat LLM output as a proposal until the application validates it.

## Deliverable

Concrete risks, blockers and verification steps. Do not demand a perfect security architecture when the risk is outside current scope.
