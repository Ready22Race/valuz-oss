# Citation protocol details

## When citations are required

Citations are required for claims derived from:

- project or uploaded documents;
- web/search/fetch tools;
- datasets and structured-data tools;
- connector records;
- prior-conversation retrieval;
- calculations whose inputs came from retrieved evidence.

They are not required for greetings, brainstorming that makes no factual
source claim, code you just wrote in the current workspace, or clearly marked
original reasoning that does not rely on retrieved facts.

## Good examples

```markdown
The policy took effect on 1 July [Policy document](evidence://ev_policy_date).
```

```markdown
The two filings report different totals
[Q1 filing](evidence://ev_q1_total)
[Q2 filing](evidence://ev_q2_total).
```

## Failure handling

- No relevant evidence: explain that the available sources do not support the
  requested claim.
- Evidence handle absent: run a source-bearing retrieval tool if available;
  otherwise report that a verifiable citation is unavailable.
- Conflicting evidence: cite each conflicting source and explain the conflict.
- Tool or source unavailable: preserve the useful part of the answer and make
  the limitation explicit.

Do not use raw URLs or fabricated identifiers as a substitute for the
`evidence://` protocol.
