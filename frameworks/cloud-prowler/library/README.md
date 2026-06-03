# cloud-prowler check library (#138)

A catalogue of the **high-signal cloud misconfigurations** PFactory surfaces.
These are reference material, not generated test files — the actual checks run
inside Prowler (CIS) and arrive as OCSF findings, which
`agents/cloud/assessment.py` maps to verdicts and
`agents/cloud/remediation.py` turns into a fix plan.

Each entry is provider-spanning (AWS · GCP · Azure) and records: what's wrong,
why it matters, the relevant Prowler check ids, and how to fix it. They feed
the remediation report's "what / why / how" sections.

| Check | Risk |
|-------|------|
| [`s3-public`](./s3-public.yaml) | Public object storage — data exposure |
| [`iam-overprivileged`](./iam-overprivileged.yaml) | Wildcard / over-privileged identities — blast radius |
| [`nsg-open-ports`](./nsg-open-ports.yaml) | Management ports open to the internet — attack surface |
