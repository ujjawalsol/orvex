# THREAT MODEL

## Assets

User session integrity (shell, input, files, processes), private data
(documents, clipboard, credentials, cookies), browser profile, system stability.

## Actors

- Malicious or compromised AI client crafting intents (untrusted input).
- Buggy automation plans (wrong targets, runaway loops).
- Co-tenancy: user working alongside automation (foreground theft both ways).

## Abuse cases considered

1. **Malicious intent → system action.** Mitigated: closed verb vocabulary;
   compiler owns timeouts/retries/backends; allowlist launches; SYSTEM blocked;
   approvals required for SENSITIVE+; no shell/registry/driver surface at all.
2. **Confused deputy (wrong window).** Mitigated: PID-verified attach, task
   affinity, exact-owned closes, ambiguity refusal, foreground/desktop/integrity
   gates on every injection, same-pid post-check. Residual: ms-scale foreground
   theft mid-keystroke (documented limitation).
3. **Runaway loops.** Mitigated: budgets (steps/time/launches/windows/inputs),
   bounded retries (safe ops only), cancellation + ESC, idle eviction.
4. **Data exfiltration via receipts/logs.** Mitigated: compact receipts (no DOM/
   tree dumps by default), secret redaction + audit (0 leaks in 5000+ events),
   sandbox-only test files, no clipboard persistence.
5. **Persistence/escalation.** No installers, services, drivers, registry, or
   scheduled tasks in codebase (audited). Medium-integrity operation; UIAccess/
   elevation explicitly out of scope (would need separate signed-track review).
6. **Browser escape.** Isolated profiles + ephemeral ports; user profile never
   attached in tests; no downloads/uploads/settings changes.

## Out of scope (explicit non-goals)

Secure-desktop/UAC interaction, kernel/driver threats, malicious local admin,
side-channels, supply-chain (pin deps before distribution).
