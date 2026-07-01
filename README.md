# Provenance Guard

A backend system that classifies submitted creative writing as likely
AI-generated, likely human-written, or uncertain — with a confidence score,
a plain-language transparency label, an appeals workflow, rate limiting, and
a structured audit log.

Repo: https://github.com/grcyaa0-eng/project4



## Architecture Overview

A submission takes the following path from input to transparency label:

`POST /submit {text, creator\_id}` → the text is run through two independent
detection signals in parallel: a Groq LLM judgment (`llm\_score`) and a
stylometric heuristic analysis (`style\_score`). These two scores are combined
into a single weighted `confidence` value (`0.6 \* llm\_score + 0.4 \* style\_score`). The confidence score is mapped to one of three transparency
labels based on threshold bands, a `content\_id` is generated, and the full
result — both individual signal scores, the combined confidence, the label,
and a timestamp — is written to a structured audit log (SQLite) before being
returned to the caller as JSON.

Separately, `POST /appeal {content\_id, creator\_reasoning}` looks up the
original submission by `content\_id`, sets its status to `under\_review`, and
appends the creator's reasoning to the same audit log entry — without
triggering any automated re-classification. `GET /log` returns all audit log
entries as JSON, which is how a human reviewer would see everything needed
to evaluate an appeal: the original text's signal scores, the label that was
shown, and the creator's stated reasoning.

Full diagram: see `planning.md`.



## Detection Signals

**Signal 1 — Groq LLM judgment (`llama-3.3-70b-versatile`)**
Sends the raw text to the model with a prompt asking it to rate how likely
the text is AI-generated on a 0.0–1.0 scale. Captures holistic semantic and
stylistic coherence — generic transitions, hedging phrasing, evenness of
tone — that a human reader would intuitively notice but that's hard to
reduce to a formula. Chosen because it captures the kind of judgment an
actual human moderator would make, just at scale.

*Blind spot:* it's a black-box judgment. It can be fooled by AI text that's
been lightly human-edited, and — more concerning for a creative-writing
platform — it can over-flag formal, careful human writing (e.g. from
non-native English speakers or academic writers) as AI-like, simply because
formality resembles some AI stylistic tendencies.

**Signal 2 — Stylometric heuristics (pure Python)**
Computes three measurable statistical properties of the text: sentence-length
variance, type-token ratio (vocabulary diversity), and punctuation density,
then combines them into a single score (weighted 50/35/15 respectively).
AI-generated text tends toward more uniform sentence lengths and lower
vocabulary variance; human writing is typically "spikier." Chosen because
it's structurally independent from Signal 1 — it measures *how* the text is
built rather than *what* it sounds like, so the two signals shouldn't fail in
the same way at the same time.

*Blind spot:* unreliable on short passages (see Known Limitations below), and
it can't detect content-level AI tells (generic claims, hallucinated facts) —
purely structural, so a human writer with a deliberately flat or repetitive
style (children's writing, minimalist poetry) can score as "AI-like" for
reasons that have nothing to do with actual authorship.



## Confidence Scoring

The two signal scores are combined as a weighted average:
`confidence = 0.6 \* llm\_score + 0.4 \* style\_score`, with the LLM weighted
higher since it's the more holistic of the two signals. Confidence maps to
three bands:

* `confidence < 0.35` → likely human
* `0.35 <= confidence <= 0.65` → uncertain
* `confidence > 0.65` → likely AI

These bands are deliberately not centered symmetrically around a binary
flip at 0.5 — a wide "uncertain" zone means a single borderline signal
doesn't produce an overconfident claim in either direction.

**Validating the scoring is meaningful:** rather than trusting that the
numbers "look reasonable," I tested with 4 deliberately chosen inputs
(a clearly AI-generated paragraph, a clearly human casual review, and two
borderline cases) and inspected both individual signal scores and the
combined result for each.

*High-confidence example* — casual human ramen review:
`llm\_score: 0.0, style\_score: 0.247, confidence: 0.099` → **likely human**.
Both signals agreed clearly; the combined score reflects that agreement.

*Lower-confidence example* — formal academic writing about monetary policy:
`llm\_score: 0.8, style\_score: 0.355, confidence: 0.622` → **uncertain**.
Here the two signals disagreed: the LLM flagged it as AI-like (likely due to
its formal tone), while the stylometric signal disagreed. The combined score
landed in the "uncertain" band rather than confidently calling it AI — which
is the intended behavior for a platform where a false "AI" accusation against
a real human writer is worse than an ambiguous label.





## Transparency Label

|Band|Exact label text|
|-|-|
|Likely AI (confidence > 0.65)|"This content shows strong indicators of AI generation. Confidence: {score}."|
|Uncertain (0.35–0.65)|"We can't confidently determine whether this content is AI-generated or human-written. Confidence: {score}."|
|Likely human (confidence < 0.35)|"This content shows strong indicators of human authorship. Confidence: {score}."|

All three variants are implemented in `compute\_label()` in `app.py`.
`likely\_human` and `uncertain` were both triggered organically by real test
submissions. `likely\_ai` was verified directly by calling
`compute\_label(0.9)`, which correctly returns the AI variant — see Known
Limitations for why no real test submission crossed the 0.65 threshold
during manual testing.





## Rate Limiting

`/submit` is limited to **10 requests per minute, 100 per day**, per IP
(via Flask-Limiter). Reasoning: a real creator submitting their own writing
for review would realistically submit a handful of pieces in a sitting, not
dozens per minute — 10/minute comfortably covers legitimate use while making
a naive flooding script hit the wall almost immediately. The 100/day cap
guards against a slower, sustained abuse pattern that stays under the
per-minute limit.

**Verified:** sent 12 rapid requests in a loop; the first 10 returned `200`
and the last 2 returned `429`:

```
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

Every submission and appeal is written to a structured SQLite table
(`audit\_log.db`) with fields: `content\_id`, `creator\_id`, `timestamp`,
`attribution`, `confidence`, `llm\_score`, `style\_score`, `label`, `status`,
and `appeal\_reasoning`. Retrievable via `GET /log`. Sample (6 entries,
including one appeal):

```json
{
  "content\_id": "e4b48313-d7ae-4b1a-a724-f5d540f8f801",
  "creator\_id": "test-formal",
  "attribution": "uncertain",
  "confidence": 0.622,
  "llm\_score": 0.8,
  "style\_score": 0.355,
  "label": "We can't confidently determine whether this content is AI-generated or human-written. Confidence: 0.62.",
  "status": "under\_review",
  "appeal\_reasoning": "I wrote this myself for an economics class. My writing style is formal because it is an academic topic."
}
```

Full log available via `GET /log` once the app is running.





## Known Limitations

**1. Stylometric signal doesn't discriminate well on short passages.**
Testing across 4 inputs ranging from clearly-AI to clearly-human, the
`style\_score` stayed clustered between 0.22 and 0.36 for *every* sample,
regardless of ground truth — it wasn't meaningfully separating AI from human
text at this passage length (3–4 sentences). This has an asymmetric effect:
it helpfully pulled a risky high LLM score down to "uncertain" for formal
human writing (avoiding a false "AI" accusation), but it also pulled a
genuinely AI-generated sample's score down from "likely AI" into "uncertain."
As a result, the "likely AI" label was never triggered organically during
manual testing (only verified via direct unit test of `compute\_label()`).
This ties directly to the signal's design: sentence-length variance and
type-token ratio need more text to produce a reliable statistic than a
few-sentence submission provides.

**2. Repetitive, simple-vocabulary human writing risks a false AI flag.**
A human writer using deliberate repetition (children's writing, minimalist
poetry) would score as more "AI-like" on the stylometric signal, since it
measures uniformity and low vocabulary diversity — properties that overlap
with a legitimate human stylistic choice.





## Spec Reflection

The spec's requirement to write out exact label text and confidence bands in
`planning.md` *before* touching code paid off directly — when the two
signals disagreed during Milestone 4 testing, I could check the actual
observed scores against pre-committed thresholds instead of retroactively
picking whatever boundary made the test pass.

Where the implementation diverged from the original plan: I initially
assumed I'd need to tune the 0.6/0.4 signal weighting once real testing
exposed the calibration issue above. I ended up keeping the original
weighting instead of adjusting it, because changing the weights would have
hidden the stylometric signal's real limitation on short text rather than
represented it honestly in the score — the mismatch was more informative
left visible than papered over.



## AI Usage

**Instance 1 (Milestone 3):** Directed the AI tool to generate the Flask app
skeleton, the `POST /submit` route, the SQLite audit log helper, and the
Groq-based `groq\_signal()` function, using the detection signals section of
`planning.md` and the architecture diagram as context. The AI tool's first
version of `app.py` called `load\_dotenv()` *after* importing `signals.py`,
which itself read `GROQ\_API\_KEY` from the environment at import time — this
caused a `GroqError` at startup because the key hadn't been loaded yet. I
caught this by reading the traceback and moved `load\_dotenv()` into
`signals.py` directly so it no longer depended on import order.

**Instance 2 (Milestone 4):** Directed the AI tool to generate the
stylometric signal function and the combined confidence-scoring logic,
using the uncertainty-representation section of the spec. I revised the
output by actually testing it against the assignment's 4 sample inputs
rather than accepting that the generated code "looked reasonable" — this is
what surfaced the style-score clustering issue documented in Known
Limitations. Rather than asking the AI tool to retune weights until the
"clearly AI" sample scored above 0.65, I made the deliberate choice to leave
the mismatch as documented, since silently tuning parameters to force a
specific test to pass would have hidden a genuine signal limitation rather
than fixed it.

