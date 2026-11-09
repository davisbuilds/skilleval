# Questions before a specification

The existing `GET /api/v2/metrics` read surface exposes operational metrics.
Ingestion, storage, provider configuration and usage accounting are out of scope.
Missing reports cannot establish whether memory is healthy or disabled.

Should this live in Monitor or Analytics? Which window should be the default?
Would you like a table or cards? Should it refresh automatically? Which states
should get badges? Do you want a session filter? Please answer before I write
the specification. I have not selected behavior or provided acceptance checks.
