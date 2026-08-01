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
The policy took effect on 1 July [source](evidence://ev_policy_date).
```

```markdown
The two filings report different totals
[source](evidence://ev_q1_total)
[source](evidence://ev_q2_total).
```

The claim text and value must remain outside each evidence link because the
client renders the link itself as a numbered citation marker.

## Derived values

Do not treat an input citation as proof of a calculation performed in prose.
For a growth rate, margin, ratio, difference, sum, or other derived number:

1. retrieve a structured or text evidence handle for every numeric input;
2. call `citation_calculate` with those exact handles, input values, units,
   and a simple arithmetic expression; for `%`, a unitless ratio expression is
   normalized to percentage points by the tool;
3. use the tool's returned result in the answer; and
4. cite the returned calculation evidence handle on the derived claim.

The host recomputes the expression and checks each input against its cited
evidence before the calculation can pass quality validation.

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
