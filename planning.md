# Provenance Guard — Planning

## Architecture

### Submission flow

```
                POST /submit {text, creator\_id}
                          |
                          v
              +-----------------------+
              |   Flask /submit route  |
              +-----------------------+
                          |
          text  --------->|<--------- text
          |                                 |
          v                                 v
  +----------------+              +------------------------+
  | Signal 1: Groq  |              | Signal 2: Stylometrics |
  | LLM judgment    |              | (sentence-len variance,|
  | -> score 0-1    |              |  TTR, punctuation)     |
  +----------------+              | -> score 0-1           |
          |                        +------------------------+
          |     llm\_score                    |    style\_score
          +----------------+------------------+
                           v
                +-----------------------+
                | Confidence Scoring    |
                | combined = weighted   |
                | avg(llm, style)       |
                +-----------------------+
                           |
                           v
                +-----------------------+
                | Label Generator       |
                | maps confidence ->    |
                | AI / Uncertain / Human|
                +-----------------------+
                           |
                +----------+-----------+
                |                      |
                v                      v
        +---------------+     +----------------+
        | Audit Log      |     | Response JSON  |
        | (SQLite/JSON)  |     | content\_id,     |
        | writes entry   |     | attribution,    |
        +---------------+     | confidence,     |
                               | label           |
                               +----------------+
```

### Appeal flow

```
        POST /appeal {content\_id, creator\_reasoning}
                          |
                          v
              +-----------------------+
              |   Flask /appeal route  |
              +-----------------------+
                          |
                          v
              +-----------------------+
              | Look up content\_id in |
              | storage                |
              +-----------------------+
                          |
                          v
              +-----------------------+
              | status -> "under\_review"|
              +-----------------------+
                          |
                          v
              +-----------------------+
              | Audit Log: append     |
              | appeal entry alongside|
              | original decision     |
              +-----------------------+
                          |
                          v
              +-----------------------+
              | Response: confirmation|
              +-----------------------+
```

Narrative: a submission flows through two independent detection signals (an LLM
judgment and a stylometric analysis), which are combined into a single confidence
score, mapped to a transparency label, and logged. An appeal looks up the original
content by `content\_id`, flips its status to `under\_review`, and appends the
creator's reasoning to the same audit log, without triggering automated
re-classification.

## Detection Signals

1. **Groq LLM judgment** (`llm\_score`, 0-1): sends the raw text to
`llama-3.3-70b-versatile` with a prompt asking it to rate how likely the text
is AI-generated. Captures holistic semantic/stylistic coherence — generic
transitions, hedging language, uniform tone. Blind spot: can be fooled by
lightly-edited AI text, and may over-flag formal human writing (e.g.
non-native English speakers, academic writers).
2. **Stylometric heuristics** (`style\_score`, 0-1): pure-Python computation of
sentence-length variance, type-token ratio (vocabulary diversity), and
punctuation density, normalized and combined into one score. Captures
structural uniformity — AI text tends toward more consistent sentence
length and lower vocabulary variance. Blind spot: unreliable on very short
samples, and can't detect content-level AI tells; a human writer with a
flat, repetitive style (children's writing, minimalist poetry) may score
as "AI-like" for reasons unrelated to authorship.

Combination: `confidence = 0.6 \* llm\_score + 0.4 \* style\_score` (LLM judgment
weighted higher since it's the more holistic signal; weights are tunable).

## Uncertainty Representation

* `confidence < 0.35` → **likely human**
* `0.35 <= confidence <= 0.65` → **uncertain**
* `confidence > 0.65` → **likely AI**

A 0.51 score falls in the "uncertain" band and gets the uncertain label, not a
binary flip at 0.5 — the band is intentionally wide around the midpoint to
avoid overconfident claims from a single borderline score. Given that false
positives (flagging a human as AI) are worse than false negatives on a
creative-writing platform, the "likely AI" threshold (0.65) is set higher than
the "likely human" threshold (0.35) is low — i.e. the bands aren't symmetric;
it takes stronger evidence to call something AI than to call it human.

## Transparency Label Variants

|Confidence band|Label text|
|-|-|
|Likely AI (>0.65)|"This content shows strong indicators of AI generation. Confidence: {score}."|
|Uncertain (0.35-0.65)|"We can't confidently determine whether this content is AI-generated or human-written. Confidence: {score}."|
|Likely human (<0.35)|"This content shows strong indicators of human authorship. Confidence: {score}."|

## Appeals Workflow

* Any creator whose content received a "likely AI" or "uncertain" label may
submit an appeal via `POST /appeal` with `content\_id` and `creator\_reasoning`
(free text explaining why they believe the classification is wrong).
* On receipt: system looks up the original submission by `content\_id`, sets its
`status` to `"under\_review"`, and appends a new audit log entry containing
the original decision (signals, confidence, label) plus the appeal reasoning
and timestamp.
* A human reviewer opening the appeal queue (`GET /log` filtered to
`status == "under\_review"`) would see: the original text, both signal
scores, the combined confidence, the label that was shown, and the
creator's stated reasoning — everything needed to make a manual call.
No automated re-classification occurs.

## Anticipated Edge Cases

1. **Very short submissions** (a haiku, a one-line caption) — stylometric
heuristics need enough text to compute meaningful variance; on a 10-word
sample, sentence-length variance and TTR are close to meaningless, which
could pull the combined score toward whatever the LLM signal alone says,
effectively collapsing to single-signal detection despite looking
multi-signal.
2. **Repetitive, simple-vocabulary human writing** (children's stories,
minimalist poetry intentionally using repetition) — stylometrics may score
this as AI-like (low vocabulary diversity, uniform sentence length) even
though a human wrote it deliberately in that style.

### Observed calibration issue (from Milestone 4 testing)

Testing with the assignment's 4 sample inputs (clearly AI, clearly human, two
borderline cases) surfaced a real tension between the two signals on
short (3-4 sentence) passages:

|Sample|llm\_score|style\_score|combined (0.6/0.4)|label|
|-|-|-|-|-|
|Clearly AI-generated|0.80|0.281|0.592|uncertain|
|Clearly human|0.00|0.247|0.099|likely human|
|Formal human (borderline)|0.80|0.355|0.622|uncertain|
|Lightly-edited AI (borderline)|0.20|0.225|0.210|likely human|

`llm\_score` clearly separates human (0.0-0.2) from AI (0.8) text, but
`style\_score` clustered tightly (0.22-0.36) across *all four* samples,
regardless of ground truth — the stylometric signal isn't discriminating
much at this passage length. This has an asymmetric effect: it correctly
pulls a risky 0.8 LLM score down to "uncertain" for the formal-human case
(avoiding a false-positive AI label, which the spec treats as the worse
error), but it also pulls a genuinely AI-generated sample's 0.8 LLM score
down to "uncertain" instead of "likely AI."

**Decision:** kept the 0.6/0.4 weighting as originally specified rather than
increasing the LLM weight to compensate. Rationale: the stylometric signal's
weak discrimination on short text is itself the finding — artificially
inflating the LLM's weight would mask a real limitation of the signal rather
than represent it honestly in the confidence score. If this were a
production system, the fix would be to make the stylometric signal
length-aware (e.g. down-weight it dynamically for passages under \~50 words)
rather than just changing static weights.

**Follow-up:** even a second, more strongly AI-stereotyped sample ("In
today's rapidly evolving digital landscape...") landed at 0.639 -- just
under the 0.65 "likely AI" threshold, for the same reason: style\_score stayed
in the 0.28-0.36 range regardless of how AI-typical the phrasing was. The
"likely AI" label path is implemented and reachable in principle (verified
by unit-testing `compute\_label()` directly with a synthetic confidence value
above 0.65), but was not triggered organically by any real submission during
manual testing. This is documented here rather than papered over by tuning
thresholds to force a pass.

## AI Tool Plan

* **M3 (submission endpoint + signal 1):** provide the "Detection Signals"
section above + the submission-flow diagram. Ask for a Flask app skeleton
with a `POST /submit` stub, plus the Groq signal function. Verify by calling
the signal function directly on 2-3 test strings before wiring into the route.
* **M4 (signal 2 + confidence scoring):** provide "Detection Signals" +
"Uncertainty Representation" + the diagram. Ask for the stylometric signal
function and the scoring function that combines both per the weights/bands
above. Verify: run the 4 test inputs from the assignment and confirm scores
land in the expected bands, not just that they "look reasonable."
* **M5 (production layer):** provide "Transparency Label Variants" +
"Appeals Workflow" + the diagram. Ask for the label-generation function and
the `/appeal` endpoint. Verify: hit `/submit` with inputs designed to land
in each of the 3 bands and confirm the exact label text matches the table
above; hit `/appeal` and confirm `/log` shows `status: under\_review` with
`appeal\_reasoning` populated.

