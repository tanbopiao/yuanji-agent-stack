# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.2.x | ✅ |
| 0.1.x | ❌ (upgrade to 0.2.x) |

## Reporting a Vulnerability

**Do NOT open a public issue for security vulnerabilities.**

Please report privately via one of:

1. AtomGit private message to [@zongyuangen](https://atomgit.com/zongyuangen)
2. Open a minimal issue titled `[security] <area>` **without exploit details** and wait for maintainer contact — we will move to a private channel

We aim to acknowledge reports within **72 hours** and publish fixes with a
patch release. Credit is given in the release notes unless you prefer
otherwise.

## Scope

High-value areas for review:

- `axiomkit/invariants` — ledger hash-chain integrity (can the append-only guarantee be subverted?)
- `axiomkit/evolution` — whitelist/frozen-core bypass (can a mutation reach protected params?)
- `axiomkit/gateway` — HMAC signing, signature timing, replay protection
- `axiomkit/fedmem` — raw-text leakage through published fragments

## Known Limitations (Alpha)

- `Gateway.verify` uses `hmac.compare_digest` but the HTTP layer has no
  replay protection (no nonce/timestamp) — do not expose it on untrusted
  networks yet
- `EvolutionKernel.evaluate` runs evaluators synchronously; untrusted
  evaluator callables should be wrapped with timeouts by the caller
- No formal Z3 encodings ship by default; without `z3-solver`, verification
  falls back to structural checks and marks candidates `manual-review`
