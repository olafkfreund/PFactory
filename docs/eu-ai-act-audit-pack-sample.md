# EU AI Act Audit Pack — Task board service

> This audit pack is a descriptive evidence bundle assembled from PFactory's planning record. The EU AI Act article/heading references are navigational labels indicating where each artifact may be relevant; they are NOT an assertion of EU AI Act conformity for the system, model, or plan. Conformity determinations require a qualified legal / conformity assessment. PFactory makes no conformity claim.

- Plan id: `001-taskboard`
- Correlation key: `18`
- Epic issue: #18
- Generated: 2026-06-18T12:00:00+00:00
- Schema: `audit-pack/v0`

## Cross-reference

| Artifact | Present | EU AI Act headings (descriptive) |
| --- | --- | --- |
| Honoured source document | yes | Technical documentation (Art. 11 / Annex IV) |
| Review-gate findings & citations | yes | Risk management (Art. 9); Technical documentation (Art. 11 / Annex IV) |
| Human approval record | yes | Human oversight (Art. 14); Record-keeping & logging (Art. 12) |
| Signed Task Contract (RFC-0002) | yes | Technical documentation (Art. 11 / Annex IV); Record-keeping & logging (Art. 12) |
| Completion & correlation timeline | yes | Record-keeping & logging (Art. 12) |
| TFactory verification verdict | no | Technical documentation (Art. 11 / Annex IV); Risk management (Art. 9) |

## Evidence

### Honoured source document

_The plan as ingested: 'Task board service' (1 stated acceptance criteria)._

```json
{
  "title": "Task board service",
  "description": "A kanban task board REST API.",
  "target_kind": "software",
  "plan_type": null,
  "acceptance_criteria": [
    {
      "id": "AC#1",
      "text": "A task can be created"
    }
  ],
  "content_hash": "7ac91d487f0b4586dd4cd1d3da654806",
  "raw_text": null
}
```

### Review-gate findings & citations

_Multi-lens review: aggregate 0.92, gates_passed=True._

```json
{
  "aggregate_score": 0.92,
  "threshold": 0.75,
  "gates_passed": true,
  "lenses": [
    {
      "lens": "architecture",
      "score": 0.9,
      "findings": [
        {
          "title": "Stateless API",
          "detail": "Good separation.",
          "severity": "info",
          "source": "architecture",
          "blocking": false,
          "citations": [
            {
              "title": "12-factor",
              "uri": "https://12factor.net",
              "why": "cited norm"
            }
          ]
        }
      ]
    }
  ]
}
```

### Human approval record

_Approved by olaf at 2026-06-18T12:00:00+00:00 (valid=True, bound to plan hash)._

```json
{
  "approved": true,
  "approved_by": "olaf",
  "approved_at": "2026-06-18T12:00:00+00:00",
  "plan_hash": "abc123",
  "valid": true,
  "review_count": 1
}
```

### Signed Task Contract (RFC-0002)

_HMAC-signed Task Contract emitted to AIFactory._

```json
{
  "contract": {
    "correlation_key": "18",
    "signature": "deadbeef"
  }
}
```

### Completion & correlation timeline

_Correlation key '18'; epic issue #18._

```json
{
  "correlation_key": "18",
  "issue_number": 18,
  "aifactory_task_id": "task-42",
  "status": "emitted",
  "audit_trail": []
}
```

### TFactory verification verdict

_No TFactory verdict on this plan record (verified downstream in TFactory)._

Not present in this plan's record.
