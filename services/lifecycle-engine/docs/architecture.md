# Lifecycle Engine — Architecture

## Data flow

```
RepositoryTarget[] ──► discovery (CodebaseSnapshot: parsed trees + ScanResult)
                          │
FlagConfiguration ──►  analyzers.CodeIntelligence ──► FlagAnalysisReport
(from Member 1 /          │
 mock data)               ▼
ConsumerCoverage ──►  policies.StaleFlagDetector ──► StaleFlagFinding
FlagLifecycle ────►       │
                          ├──► state_machine.evaluate_lifecycle ──► LifecycleEvaluation (suggestion)
                          ▼
                     cleanup.PatchGenerator ──► CleanupPatch (diffs, never applied to repos)
                          │
                     agents.CleanupPlanner (+ optional LLM, validated) ──► CleanupPlan
                          │
                     cleanup.CleanupVerifier (temp copy, tests, re-scan) ──► VerificationResult
```

The engine is **stateless**. Lifecycle records, findings, plans and verification results are
returned to the backend to persist (Member 1).

## Module 1 — Discovery

* Tree-sitter grammars: `typescript`, `tsx`, `javascript` (`discovery/languages.py` registry;
  new languages register a grammar + scanner family).
* Verified Flagsmith APIs only (`discovery/sdk_patterns.py`):
  JS client `hasFeature`, `getValue`, `getTrait`; React `useFlags([...])` + `flags.x.enabled/.value`;
  Node server `getEnvironmentFlags()/getIdentityFlags()` → `isFeatureEnabled`, `getFeatureValue`, `getFlag`.
* A receiver is **CONFIRMED** only when traced to a Flagsmith binding: default/named/namespace
  import, `require`, `new Flagsmith()`, `createFlagsmithInstance()`, `useFlagsmith()`, a
  parameter/field typed `IFlagsmith`/`Flags`, or an export of any of these from another file
  (resolved to a fixed point across the repository).
* Same method on an untraceable receiver → **POTENTIAL** (low confidence), and only when there is
  contextual evidence (Flagsmith import in the file, or receiver named like a flag client).
  Without context it is ignored (e.g. `form.getValue("email")`).
* Flag names: literals, substitution-free templates, local/imported `const` strings, `const`
  objects (`FLAGS.X`) and TS string enums. Anything else is **DYNAMIC** (reported separately with
  the raw argument expression).
* Stable ids: `ref_<sha256(repo, path, line, col, api, name)>`.

## Module 2 — Code intelligence

Purely syntactic, per reference:

* Polarity normalisation through `!`, `=== true/false`, parentheses, `await`.
* Constructs: if/else, if, guard clause (early exit), ternary, `&&`/`||` short-circuit (JSX
  included), one level of alias (`const on = flags.hasFeature(...)`). Compound conditions are
  reported as indeterminate rather than guessed.
* Fallbacks: SDK `{ fallback }` option, `||`/`??` defaults, `typeof v === ... ? v : default`.
* Related symbols: enclosing function/component, functions/components used in each path,
  whether they are **exclusively** reachable through that path (repository-wide usage index).
* Related tests: test files that mention the flag name, import a flag-controlled module, or use
  related symbols.
* Findings: redundant nested checks, identical branches, potentially unreachable branches
  (hypothesis, needs configuration), flag value escaping its function, unanalyzed usage.

## Module 3 — Lifecycle

States: `created → in_development → testing → rolling_out → fully_released → ready_for_cleanup →
cleanup_in_progress → retired`, plus explicit rollback edges. Each transition rule lists required
evidence, any-of evidence and approvals (`state_machine/machine.py::TRANSITION_RULES`).

* `rolling_out → fully_released` needs 100% exposure **and** stable config **and** owner approval.
* `cleanup_in_progress → retired` needs `cleanup_verified` (source `verification`, with id),
  `cleanup_merged` (source `github`, with PR reference), `no_remaining_references`, plus owner
  **and** reviewer approvals.
* Overrides are audited (`is_manual_override`, justification, approver) and **cannot** bypass the
  requirements of `cleanup_in_progress` or `retired`.
* `evaluate_lifecycle` only suggests (`is_recommendation: true`), never infers
  `cleanup_in_progress`/`retired`.

## Module 4 — Stale detection

Weighted evidence (age, uniform production state, environment agreement, config stability,
owner confirmation, references, activity, lifecycle) yields a score; **blockers** cap the status;
**missing evidence** lists what prevents a stronger status. Statuses:
`not_stale`, `insufficient_evidence`, `potentially_stale`, `ready_for_cleanup`, `safe_to_retire`.

Hard rules: active targeting, long-lived purpose/tags, owner "still needed" or a young flag →
`not_stale`. Dynamic references that *may* match, unconfirmed references, known external
consumers, evaluations without scanned references, and recent production changes cap at
`potentially_stale`. `ready_for_cleanup` requires owner confirmation, stable config and
(for zero-reference flags) confirmed coverage plus activity data. `safe_to_retire` additionally
requires a lifecycle history with verified + merged cleanup and no remaining references.

Dynamic references can be cleared by a human `DynamicReferenceReview` (recorded as evidence).
Weights and thresholds live in `StalePolicy` and are configurable per evaluation.

## Module 5 — Planning and patches

* `PatchGenerator` substitutes the post-cleanup flag value and partially evaluates boolean logic
  (`flag && x → x`, `if (true) A else B → A`, `{flag ? <A/> : <B/>} → <A/>`, guard clauses with
  unreachable tails), iterating to a fixed point; then removes emptied `useFlags` bindings and
  imports made unused by the patch. Unsupported constructs and potential references become
  `manual_steps`; newly-unused symbols/files/Flagsmith calls become `follow_ups` (never deleted
  automatically). Line endings are preserved.
* `CleanupPlanner` derives the action, affected files, proposed changes, risks, approvals and
  verification requirements deterministically. An `LLMProvider` may only return
  `LLMPlanNarrative` (summary, typed statements, risks, patch rationale). The validator rejects it
  if it cites unknown evidence ids, has uncited facts, mentions files/lines not in the evidence,
  or claims safety/test results/merge/archive. On rejection or outage the deterministic narrative
  is used and `plan.llm` records why.
* Providers: `MockLLMProvider` (tests/dev); `ClaudeCodeProvider` (Claude Code CLI sub-process:
  `claude -p --output-format json --json-schema … --tools "" --strict-mcp-config
  --no-session-persistence --system-prompt …`, evidence packet on stdin, empty temporary cwd,
  `--max-budget-usd` cap; on Windows the npm `.cmd` shim is bypassed for the native `claude.exe`
  so cmd.exe never re-parses the JSON arguments); `AnthropicProvider` (official SDK, structured
  outputs with JSON schema, server-side refusal fallback; optional dependency).
* Replacing Claude Code's default system prompt and tool set matters: with them a call loads
  ~56K tokens of context (about $0.45 even for a trivial prompt); with `--system-prompt` and
  `--tools ""` a trivial call costs about $0.01.
* Validation quotes the offending sentence in every rejection. Claims are only exempt when a
  negating, conditional or prescriptive word ("not", "until", "only once", "must"…) appears in the
  same sentence, so "do not archive until it is merged" passes and "Not yet. It has been
  archived." does not.

## Module 6 — Verification

Copies repositories to a temp dir, checks each file's SHA-256 against the patch (stale patches
are refused), refuses paths that escape the repository, writes patched files to the copy, re-parses
changed files, runs configured or auto-detected (`npm test`) commands, and re-scans for remaining
and possibly-matching dynamic references. `VerificationResult` has a model validator that makes a
"ready" result with failed/errored checks impossible to construct. Commands that cannot run are
`error`, never `passed`.

## Known limitations

* Bindings are tracked per file by name, not lexical scope (shadowing can cause false positives).
* Values flowing through function returns / props / context are not followed (reported as
  `flag_value_escapes_scope`).
* `getValue()` on a flag removed by boolean cleanup is a manual step unless a value is inlined.
* Only TypeScript/JavaScript; other Flagsmith SDKs (Python, Go, mobile) need new scanners.
* Patch output is not formatted with the project's formatter; verification runs tests but only
  runs type checkers/linters when configured as commands.
* Verification copies repositories without `.git`; very large repositories or `node_modules`
  make this slow — configure `copy_ignore`/commands accordingly.
* The stale score weights are heuristics, documented and configurable, not calibrated on data.
