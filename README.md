# LearnWise AI

LearnWise AI is an AI-powered math tutoring application for children ages 8–10. Instead of giving students the answer, it guides them toward it with a three-level hint ladder. It refuses attempts to get the answer outright and gives targeted feedback on common mistakes.

**Status:** working prototype. The tutoring backend, web API and terminal demo are implemented and tested. A frontend, a database and a larger problem bank are planned (see [Roadmap](#roadmap)). The demo problem is *What is 1/2 + 1/3?*

---

## Problem Statement

General-purpose AI assistants will usually hand a student the final answer to a math problem. The student gets a correct result but skips the reasoning the exercise was meant to build, and the teacher can't see where the student got stuck.

A tutor for young children has to solve a different problem: give enough help that the student keeps going, without giving away the answer. That calls for:

- **Progressive hints**, so each hint does a little more of the work and the student does the rest.
- **Reliable answer protection.** A prompt asking the model not to reveal the answer is not enough, because language models don't follow instructions with certainty.
- **Child-appropriate safety and privacy**, including fixed responses for sensitive disclosures and teacher reporting that doesn't expose individual children.

## Key Features

### Core design principle: the application decides, the LLM rephrases

All tutoring decisions are made by deterministic Python code: which hint the student may see, whether an answer is correct, and when to refer the student to a teacher. The language model has one job, which is to reword a pre-written hint in child-friendly language. The hint ladder and answer checking never depend on the model, so they behave the same in offline and live mode.

Model output is handled separately. Every model reply passes through an answer-leak check, and if the check fails or the model call errors, the student gets the pre-written hint instead. This check is a guardrail, not a guarantee: it catches known forms of the answer, not every possible paraphrase (see [Known Limitations](#known-limitations)).

### Three-level hint ladder
- Each problem has three pre-written hints of increasing detail. The third is a worked example of a *similar* problem.
- The current rung is stored on the server. Only a recognized hint request advances it, so a student can't jump ahead or pick a rung.
- After the third hint, the tutor stops giving hints and points the student to their teacher (status `needs_teacher`).

### Intent classification
Messages are classified by keyword matching in `intent_gate.py`. The safety check runs before anything else.

| Intent | Example | Behavior |
|---|---|---|
| `hint_request` | "hint", "I'm stuck" | Advances the ladder by one rung |
| `answer_request` | "just tell me the answer" | Refuses; repeats the current hint if one has been given |
| `bypass_attempt` | "ignore your instructions" | Redirects to the lesson |
| `safety_concern` | "someone is hurting me" | Fixed script (see below) |
| `quit` / `empty` | "quit", "" | Ends the session / prompts the student |
| `general_question` | "what is a denominator" | In live mode, the model answers without solving the problem (output is leak-checked); offline, a standard prompt to try an answer or ask for a hint |

Anything it doesn't recognize becomes `general_question`, which never advances the ladder, so a misclassification can't release a hint early.

### Answer checking and misconception feedback
- A correct answer marks the problem solved (status `solved`) and gets praise. The terminal demo then exits; the API session stays open.
- Known wrong answers get feedback on the specific mistake behind them. For example, `2/5` gets: "It looks like you added the top numbers and the bottom numbers…"
- Other wrong answers get feedback based on the answer's size ("That's one whole or more!"), which explains the error without revealing the answer.

### Answer-leak protection
`leak_check.py` scans every model-generated reply for forbidden forms of the answer (for example `5/6`, `five sixths` or `0.833…`). If one appears, the reply is replaced with the pre-written hint.

### Child safety and privacy
- The tutor discloses that it is an AI at the start of every session.
- Messages suggesting a child may be unsafe always receive a fixed, pre-written reply and are never sent to the model. A safeguarding alert is recorded with only a session ID and a timestamp, never the child's words.
- The teacher summary is aggregate-only and stays hidden until at least 5 learners have started, so no individual child stands out. It never includes names or message text.

### Session management
Each session tracks the student's current rung, whether they solved the problem, and an event log (hint requests, wrong answers, refusals). The event log feeds the teacher summary. Sessions are held in memory (see [Known Limitations](#known-limitations)).

## Technology Stack

| | Technologies |
|---|---|
| **Implemented** | Python, FastAPI, Pydantic, Uvicorn, OCI Python SDK, OCI Generative AI (Cohere Command A, `cohere.command-a-03-2025`), pytest, HTTPX (FastAPI `TestClient`) |
| **Planned** | React frontend, PostgreSQL |

## System Architecture

```mermaid
flowchart LR
    C["Client<br/>(terminal demo, API docs, curl)"] --> API["FastAPI app<br/>api.py"]
    API --> S["TutorSession<br/>tutor.py"]
    API -.-> MEM[("In-memory sessions<br/>and safety alerts")]
    S --> IG{"Intent gate"}
    IG -->|safety concern| SAFE["Fixed safety script"]
    S --> AC["Answer check +<br/>misconception feedback"]
    S --> CP["Content provider<br/>pre-written hints, current rung"]
    CP -->|pre-written hint| MP["Model provider<br/>Mock or OCI"]
    MP -->|optional| OCI["OCI Generative AI<br/>Cohere Command A"]
    MP -->|candidate reply| LC{"Answer-leak check"}
    LC -->|safe| OUT["Reply to student"]
    LC -->|leak or model error| FB["Pre-written hint, verbatim"]
    FB --> OUT
    SAFE --> OUT
    AC --> OUT
```

### Request flow

1. The client sends a message to `POST /api/tutor/message`, or types it into the terminal demo.
2. `TutorSession.handle()` checks for a safety concern first. If it finds one, it returns the fixed script.
3. It then checks whether the message is a correct answer, then whether it is a wrong answer, and gives feedback if so.
4. Otherwise it classifies the intent. Only a `hint_request` advances the rung in the content provider.
5. The pre-written hint for that rung goes to the model provider. The mock returns it unchanged; the OCI provider asks Cohere to reword it (temperature 0, 120-token limit, child-safety system prompt).
6. The leak check vets the result. The response is `{reply, rung, status}`.

## Design Decisions

- **Deterministic guardrails instead of prompt-only safety.** A system prompt makes it less likely a model reveals the answer, but can't prevent it. So rung control, answer checking and output screening live in code that can be unit-tested. *Trade-off:* the checks are only as good as their rules. The current leak check matches known answer forms, not meaning.
- **A model-provider abstraction.** `MockModelProvider` and `OCIModelProvider` share one interface, so the tutoring logic never knows which is in use. The test suite runs offline and deterministically, and uses stand-in models that leak the answer or time out to prove the fallbacks work. *Trade-off:* the mock tests the plumbing, not the quality of real model output, which needs a separate evaluation that hasn't been built yet.
- **Fail closed.** If the leak check rejects a reply, or the model call raises any error, the student gets the pre-written hint, never an error message or unchecked text. *Trade-off:* the student sees plainer wording, and these fallbacks aren't logged yet, so their frequency isn't measured.
- **Keyword-based intent classification instead of an LLM classifier.** It's predictable, free, fast and easy to test. Unrecognized messages become `general_question`, which never advances the hint ladder. *Trade-off:* keyword matching misses some phrasings and over-matches others.
- **A minimum cohort of 5 for the teacher summary.** Showing aggregate data only once at least 5 learners have started makes it harder to single out one child's results. *Trade-off:* small groups see no data, and the threshold reduces re-identification risk rather than eliminating it.

### Repository structure

```
learnwise-ai/
├── oci-tutor/                 # The application
│   ├── api.py                 # FastAPI app: session, tutor, teacher and safeguarding endpoints
│   ├── tutor.py               # TutorSession (request handling) + terminal demo
│   ├── intent_gate.py         # Keyword-based intent classification
│   ├── answer_check.py        # Correct-answer check and misconception feedback
│   ├── leak_check.py          # Answer-leak detection with fallback
│   ├── model_provider.py      # Mock and OCI Generative AI providers
│   ├── content_provider.py    # Problem, hints and rung state (in-code mock)
│   ├── test_oci.py            # Manual check: OCI config loads (no API call)
│   ├── test_genai.py          # Manual check: one live OCI Generative AI call
│   ├── tests/                 # Automated pytest suite
│   └── requirements.txt
├── harness/                   # Alternative tutor design (Claude + Oracle 26ai); reference only, not used by the app
├── db/                        # Draft Oracle 26ai schema and seed data; not connected to the app
└── doc/                       # Design notes
```

## Getting Started

### Prerequisites
- Python 3.10 or later
- For live AI mode only: an OCI account with access to OCI Generative AI, configured in `~/.oci/config`

### Install

```bash
git clone https://github.com/Taiye03/learnwise-ai.git
cd learnwise-ai/oci-tutor
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Run (offline)

Offline mode uses the pre-written hints directly and needs no credentials.

```bash
python tutor.py                          # terminal demo
uvicorn api:app --reload --port 8000     # web API; interactive docs at http://localhost:8000/docs
```

### Run with OCI Generative AI (optional)

Live mode reads the `DEFAULT` profile from `~/.oci/config`, which lives outside the repository. The profile's region must offer OCI Generative AI with the Cohere Command A model; development used `us-chicago-1`.

```bash
python test_oci.py                       # confirms the OCI config loads (no API call)
python tutor.py --live                   # terminal demo with AI-reworded hints
LEARNWISE_LIVE=1 uvicorn api:app --port 8000
```

| Environment variable | Purpose | Default |
|---|---|---|
| `LEARNWISE_LIVE` | Set to `1` to use OCI Generative AI in the API | Off (mock provider) |
| `OCI_COMPARTMENT_ID` | Compartment for Generative AI calls | The tenancy from `~/.oci/config` |
| `OCI_MODEL_ID` | Model to call | `cohere.command-a-03-2025` |

Keep OCI keys, `~/.oci/config` and `.env` files out of the repository. They are listed in `.gitignore`.

> The OCI integration was tested live during development but has not been reverified recently.

### API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/session` | Start a session; returns the session ID, AI-disclosure greeting and question |
| `POST` | `/api/tutor/message` | Send `{session_id, message}`; returns `reply`, `rung` and `status` |
| `GET` | `/api/teacher/summary` | Aggregate class progress; hidden below 5 learners |
| `GET` | `/api/safeguarding/alerts` | Safety alerts (session ID and time only) |
| `POST` | `/api/demo/seed` | Adds 6 synthetic learners so the teacher summary has data |

### Try these inputs

Listed in an order that works in a single terminal demo session (`python tutor.py`):

| Input | Expected behavior |
|---|---|
| `hint` (four times) | Rungs 1 → 2 → 3, then a referral to the teacher |
| `just tell me the answer` | Refuses and repeats the current hint |
| `ignore your instructions` | Redirects to the lesson |
| `2/5` | Explains the "added tops and bottoms" mistake |
| `7/6` | Explains the answer is too large |
| `someone is hurting me` | Fixed safety script and a safeguarding alert |
| `5/6` | Marks the problem solved; the demo exits |

To leave without solving, type `quit`.

## Testing

The test suite runs fully offline. Model behavior is simulated with test doubles, including a model that tries to leak the answer and one that times out.

```bash
cd oci-tutor
python -m pytest -q
```

| File | What it covers |
|---|---|
| `tests/test_intent_gate.py` | Intent classification, including phrasing variants and safety messages |
| `tests/test_leak_check.py` | Leak detection for answer variants; safe text passes; fallback behavior |
| `tests/test_session.py` | Hint ladder limits, answer refusal, misconception feedback, leak blocking, model-failure fallback, safety handling |
| `tests/test_api.py` | Full API conversation, unknown sessions, teacher-summary privacy threshold, alerts containing no message text |

As of November 2026, the suite has 49 tests, all passing. `test_oci.py` and `test_genai.py` are manual scripts for checking OCI setup and are not part of the automated suite.

## Current Development Status

| Status | Functionality |
|---|---|
| ✅ Implemented | Three-level hint ladder with server-side rung control · intent classification · answer-request refusal · misconception feedback · answer-leak check with fallback · fixed safety script and text-free alerts · mock and OCI model providers · FastAPI API · privacy-thresholded teacher summary · terminal demo · offline test suite |
| 📋 Planned | See the [Roadmap](#roadmap); improved answer validation is the current focus |

There is currently **no frontend** in this repository. The application is used through the terminal demo or the API (including FastAPI's interactive docs at `/docs`).

### Known Limitations

| Limitation | Planned improvement |
|---|---|
| **Answers are matched as text, not values.** `15/18` is marked wrong, and a hedged answer like "is it 5/6 or 2/5?" is marked correct. | Improved answer validation |
| **The leak check catches known answer forms only**, not paraphrases ("5 out of 6") or worked steps. | Improved answer validation |
| **One hard-coded problem**, with some feedback written specifically for fraction addition. | Backend expansion, problem bank |
| **No persistence or authentication.** Sessions are in memory, teacher and safeguarding endpoints are open, and CORS allows all origins. | PostgreSQL integration, backend expansion |
| **Keyword intent matching** over-matches. For example, "this is helpful" counts as a hint request. | Not yet scheduled |
| **No answer-reveal path.** After the final hint, the student is referred to a teacher. | Product decision pending |
| **Unpinned dependencies; no CI.** | Not yet scheduled |

This is a prototype, not a production child-safety system. Real classroom use would require school authentication, privacy-law review (COPPA/FERPA), content moderation and testing with students.

## Roadmap

These are **planned** and not yet implemented:

- **Improved answer validation (current focus):** compare answers by value using exact fractions, so equivalent fractions are accepted, approximate decimals get guidance toward a fraction, and hedged answers are flagged. Extend the leak check to use the same values.
- **FastAPI backend expansion:** generalize the tutoring logic from one problem to any problem with a numeric answer, and add authentication and structured error handling.
- **PostgreSQL integration:** persist students, sessions, problems, hints and tutoring events.
- **Expanded problem coverage:** a problem bank organized by topic and difficulty, which also serves as an evaluation dataset.
- **React frontend:** a student workspace for working through problems.
- **Teacher dashboard:** class-level insight into where students get stuck, built on the existing privacy rules.
