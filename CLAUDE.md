# CLAUDE.md

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"
- "Refactor X" -> "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Don't Assume -- If You Don't Know, Say So

**Never claim something works, exists, or behaves a certain way unless you've verified it. If you haven't, say "I don't know" or "I haven't tested that".**

Banned phrases when not verified:
- "this works"
- "verified"
- "should work"
- "this is wired up"
- "X is fine"

Required phrases when uncertain:
- "I don't know"
- "I haven't tested this"
- "I'm assuming X but haven't confirmed"
- "This is unverified"

Before reporting status, audit each claim:
- Did I run it end-to-end? -> can claim "works"
- Did I only check it parses / imports / starts? -> say "starts cleanly, full round-trip not tested"
- Did I infer from documentation, help text, or another agent's behavior? -> say "I'm assuming based on X, not verified"

A "PASS" in a test harness means PASS only for what the harness actually checked. If the harness checks "non-empty output", report that, not "works".

When the user asks "does X work?", the only honest answers are:
- "Yes -- verified by [specific test/command/output]"
- "No -- fails at [specific point], log/error attached"
- "I don't know -- haven't tested"

## 6. Agent Model Routing

**The main loop orchestrates. Agents do the work, each on the model that fits the kind of work, not its subject.**

Set the model explicitly on every agent call (Agent tool `model`, Workflow `agent(prompt, {model})`):

| Kind of work | Model |
|---|---|
| Working out a design; choosing between alternatives; finding the cause of a failure | fable |
| Writing down a design that is already decided | opus |
| Code, tests and build files, even when the change is small | opus |
| Difficult or complex coding | fable -- rare; say why when choosing it |
| Reports, and bringing documents in line with the repository | opus |
| Fully specified mechanical edits to documents and data: renaming, renumbering, correcting counts | sonnet |
| Analysis of existing code and documentation, including review and verification | sonnet |
| Web research | haiku for a single page, sonnet when several sources must be reconciled |

When a task fits two rows, the work decides, not the topic:
- If the brief already contains the decisions, it is writing, not design.
- If the agent must change source, tests or build files, it is coding, however small or mechanical.

Reasoning effort (Agent tool `effort`): set `low` for fully specified mechanical edits. Otherwise leave it unset.

The main loop does not code, except in very complex cases, which should happen rarely:
- Source, tests and build files go to an opus agent, even when the change is small.
- The main loop briefs agents, reads their results, and makes architecture decisions.

An omitted model inherits the session model, and named agent types carry their own default model. Neither replaces the explicit choice.
