# AGENTS.md

## Repo state
- This is a spec/framework repo with local Runtime MVP tooling. The clean tracked base is small (`.gitignore`, `README.md`, selected `docs/**`); local/untracked work may include `AGENTS.md`, `docs/agent_context/**`, `docs/framework_roadmap.md`, and `scripts/*.py`.
- The `src/`, `config/`, and `tests/` tree in `README.md` is planned/TODO, not present code. `docs/validate_gate/` and `docs/release_governance/` are placeholders if present.
- Treat `config/routing_map_v1.json` as the machine-readable source of truth for L0-L4 routing. Keep `docs/intake_layer/active_intake_protocol.md`, `docs/intake_layer/routing_map_analysis.md`, and `docs/architecture/architecture_evaluation_summary.md` consistent with it when changing routing/workflow semantics.
- `docs/framework_roadmap.md` is the roadmap/spec reference when present. Current local roadmap status: v0.1-v3.0 completed; Runtime MVP, Self-Pilot, Governance Audit, Runtime Hardening, Runtime Test Polish, and Observability & Monitoring MVP are complete. Next candidates are v3.1 ML/Hybrid Classifier evaluation, external product repo pilot proposal (Human-approved only), optional observability persistence/dashboard (separate scope), and docs sync follow-ups.

## Workflow rules to preserve
- Routing confidence is defined as `0.65 * margin_component + 0.35 * ratio_component`; thresholds are `>=0.85` direct, `>=0.55` guarded, `<0.55` ask clarification.
- Cross-layer dominance is highest risk first: `L4_release > L3_refactor > L2_bug_fix > L1_feature_dev > L0_config_housekeeping`.
- Validate Gate loop is Developer -> QA, PASS advances, FAIL increments `retry_count`; retry limit is 3 before escalating to the user.
- L4 release/deploy/tag/merge/prod tasks must be delegated to `agent-releaser`; Architect must not run `git merge` to `mr/main`, `git push origin mr/main`, `gh pr merge`, `git tag`, `gh release create`, or force-push.
- Release governance has four guards: tag format `^v[0-9]+\.[0-9]+\.[0-9]+$`, `release.json` tag consistency, commit ancestry in `origin/main`, and explicit human confirmation.
- Roadmap refinements are not yet canonical routing rules until reflected in `routing_map_v1.json` and the intake/architecture docs.

## Validation for changes
- For doc/spec changes, verify by reading the affected docs plus `config/routing_map_v1.json` and checking they agree.
- For JSON edits: `python3 -m json.tool config/routing_map_v1.json >/dev/null`.
- Runtime MVP validation commands (local `scripts/*.py` tooling):
  ```bash
  python3 -m json.tool config/routing_map_v1.json >/dev/null
  python3 -m py_compile scripts/intake_classify.py scripts/pool.py scripts/lane_select.py scripts/observability_report.py
  python3 -m unittest discover -s tests -p 'test_*.py'
  python3 scripts/intake_classify.py "fix threshold bug"
  python3 scripts/intake_classify.py "release prod tag v1.2.3"
  python3 scripts/intake_classify.py "please help"
  python3 scripts/lane_select.py --sample L0_Fast_Track
  python3 scripts/lane_select.py --sample L2_QuickFix
  python3 scripts/lane_select.py --sample L4_RELEASE
  python3 scripts/pool.py --help
  python3 scripts/pool.py add --help
  python3 scripts/observability_report.py --format json
  python3 scripts/observability_report.py --format markdown
  ```
- `scripts/pool.py` writes local queue state under `docs/agent_context/pool/` and `pool.yaml`; do not confuse generated pool items with canonical routing specs.

## Local OpenCode/runtime gotchas
- `AGENTS.md`, `opencode.json`, `.opencode/`, `.opencode-runtime/`, and `docs/framework_roadmap.md` may be untracked local files. Do not assume they will be present on a clean clone unless committed later.
- `opencode.json` can contain live MCP/API secrets. Do not quote its secret values and do not stage it unless explicitly requested.
- Ignore `.opencode-runtime/node_modules/`, `scripts/__pycache__/`, and pool-generated item files unless the task is specifically about those runtime artifacts.
- If asked to edit repo-local OpenCode configuration (`opencode.json*`, `.opencode/`, agents, skills, plugins, or MCP config), use the `customize-opencode` skill first.
