# Release Governance Rules

> Formal release governance policy for AWF. Four mandatory guards prevent unauthorized or incorrect releases.
> Last updated: 2026-07-22 (v3.8)

---

## 1. Purpose

Release governance ensures that every production deployment through AWF passes four independent guards: tag format validation, release.json consistency, commit ancestry verification, and explicit human confirmation. No release proceeds without all four guards passing.

Release governance is enforced for every L4_release lane task. The Releaser agent owns execution; Architect must not run release operations directly.

---

## 2. Four Guards

### Guard 1 — Tag Format

**Rule**: Every Git tag must match the strict semver-like format:

```
^v[0-9]+\.[0-9]+\.[0-9]+$
```

**Examples**:
- ✅ `v1.0.0`, `v2.3.15`, `v10.20.30`
- ❌ `v1.0`, `v1.0.0-beta`, `release-1.0.0`, `1.0.0`, `VV1.0.0`

**Enforcement**: The Releaser agent validates tag format before any release operation. Tags not matching this pattern are rejected with a clear error.

---

### Guard 2 — release.json Consistency

**Rule**: The tag being released must match the `version` field in the `release.json` file at that commit.

```
tag = v1.2.3  →  release.json version field must be "1.2.3"
```

**Examples**:
- ✅ Tag `v1.2.3`, release.json has `"version": "1.2.3"` → proceed
- ❌ Tag `v1.2.3`, release.json has `"version": "1.2.0"` → reject with mismatch error

**Enforcement**: The Releaser agent reads `release.json` at the tagged commit and compares versions before proceeding.

---

### Guard 3 — Commit Ancestry in origin/main

**Rule**: The tag must point to a commit that exists in the `origin/main` branch history.

```
git merge-base --is-ancestor <tagged_commit> origin/main
```

**Rationale**: Tags on orphaned commits (outside main history) indicate a release from a feature branch, which bypasses required CI/CD gates and peer review.

**Examples**:
- ✅ Tag on commit that is a direct ancestor of origin/main → proceed
- ❌ Tag on commit from a feature branch never merged to main → reject

**Enforcement**: The Releaser agent runs `git merge-base --is-ancestor` before any release operation.

---

### Guard 4 — Explicit Human Confirmation

**Rule**: A human must explicitly confirm before any production deployment.

**Format**: The Releaser agent prompts for confirmation with a summary:
```
Release: v1.2.3
Commit: abc1234
Changes: 5 files changed, 3 features
Proceed with production deploy? (yes/no):
```

**Escalation path**: If confirmation is not received within a configured timeout (default: 24 hours), the release is cancelled and Architect is notified.

**Enforcement**: The Releaser agent blocks deployment until explicit confirmation is received. Architect must not bypass this guard.

---

## 3. Releaser Mandatory Delegation

### Rule

Architect must **never** execute L4 release operations directly. This includes:
- `git tag`
- `git push origin <tag>`
- `gh release create`
- `git push origin main`
- `gh pr merge`
- Any production deployment command

### Rationale

Separation of duties: Architect designs and reviews; Releaser executes and audits. Architect running releases creates a single point of failure and bypasses the governance audit trail.

### Violation Handling

If a governance bypass is detected:
1. Incident is logged in the release audit trail
2. Architect is notified immediately
3. Immediate rollback is initiated if deployment has occurred
4. Incident is reported at next governance review

### Delegation Steps (from routing_map_v1.json)

```
Audit → branch → commit (conventional commits) → PR/MR → CI/CD monitor
```

---

## 4. Release Log Format

Every release must produce two documents under `docs/agent_context/release_{version}/`:

### 4.1 deployment_plan.md

Pre-release planning document. Created before the release begins.

```markdown
# Release v{version} Deployment Plan

## Release Summary
- Version: v{version}
- Date: {YYYY-MM-DD}
- Author: {Architect name}

## Changes
- {List of changes}

## Guard Validation
- [ ] Guard 1: Tag format validated
- [ ] Guard 2: release.json consistency checked
- [ ] Guard 3: Commit ancestry verified
- [ ] Guard 4: Human confirmation pending

## Rollback Plan
- {How to roll back if deployment fails}

## Post-Deploy Checklist
- [ ] Healthcheck passes
- [ ] CI/CD pipeline complete
- [ ] Release notes published
```

### 4.2 release_log.md

Post-release audit document. Created after the release completes.

```markdown
# Release v{version} Log

## Release Summary
- Version: v{version}
- Deployed at: {ISO 8601}
- Duration: {N minutes}
- Deployed by: agent-releaser

## Guard Results
- Guard 1 (Tag Format): PASS — v{version}
- Guard 2 (release.json): PASS
- Guard 3 (Ancestry): PASS
- Guard 4 (Human Confirm): PASS

## Deployment Details
- Commit: {sha}
- Files changed: {N}
- Artifacts: {list}

## Post-Deploy Verification
- Healthcheck: PASS/FAIL
- CI/CD: PASS/FAIL
- Notes published: yes/no

## Incidents
- {Any incidents or deviations from plan}
```

---

## 5. References

- `docs/architecture/development_stage_framework.md` — AWF governance loop architecture
- `config/routing_map_v1.json` — L4_release routing, mandatory_delegation, delegation_steps
- `docs/architecture/architecture_evaluation_summary.md` — Release governance evaluation
- `scripts/lane_select.py` — L4 lane with Releaser mandatory delegation
- `scripts/omo_dispatch_adapter.py` — OmO adapter rejects L4 tasks (stays in Releaser governance)
