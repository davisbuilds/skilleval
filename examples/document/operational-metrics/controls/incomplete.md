# Operational metrics outline

Add a read-only console panel backed by `GET /api/v2/metrics`.
Keep ingestion, storage, provider configuration and accounting unchanged.

The panel will help inspect recent Codex memory activity and skips.
TODO: decide what the returned counts mean, which outcomes to expose, how
recentness is communicated, where the panel goes, how it is refreshed, and
what empty and failed reads mean. Acceptance checks will be written later.
