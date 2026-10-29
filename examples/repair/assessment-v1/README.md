# Repair judgment controls

These newly authored, deliberately small cases test repair-judge behavior. They
are not representative agent tasks or evidence of skill effectiveness. Keep
`controls.json`, expected judgments and other variants outside each judge packet.
Only its selected source, patch, statement and separately collected observations
belong in a packet. `worker.py` runs only in an explicit offline guest.

The cases distinguish a repair that honors the contract, a blanket rejection
that breaks legitimate admission, and an unverifiable external-testing claim.
Expectations are authored labels pending independent audit. Agreement alone does
not establish judge reliability; preserve disagreements and missing judgments.
These controls are separate from earlier review calibration and its pending audit.
