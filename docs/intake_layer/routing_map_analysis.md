# Routing Map Analysis

從 git log（150+ commits）與實際開發模式萃取的 L0–L4 分類與 routing 規則。

> **分類基準**: 開發任務類型（dev_task_type），非程式內部資料流。
> 資料來源: `git log --oneline --all -150` + session history + src/ 變更模式統計。

---

## L0 — 配置 & 雜務（Config & Housekeeping）

**對應 git types**: `chore` / `docs` / `ci`  
**風險**: 🟢 LOW  
**觸發情境**: 改 config 檔案、bump 版本號、更新文件、調 CI 腳本、改 dependencies

**典型任務範例**:
| 實際 commit | 說明 |
|:------------|:------|
| `8785d27 chore: 版本升至 v0.16.1` | 版本號更新 |
| `5b937d3 chore: trigger CI re-run` | CI 觸發 |
| `c08609b fix: F541 f-strings` | lint 修復 |

**影響範圍**: 限於 `config/`、`release.json`、`CHANGELOG.md`、`ruff.toml`、`pyproject.toml`、`requirements.txt`、`.github/workflows/`

**測試要求**: 不需新測試，既有測試必須通過。

**HITL 模式**: 🟢 LOW — auto-approve（prod config 變更例外）

---

## L1 — 功能開發（Feature Development）

**對應 git types**: `feat` / `Phase`（結構化開發階段）  
**風險**: 🟡 MEDIUM  
**觸發情境**: 新增 processor、quality check、search strategy、storage backend、CI job、prompt template

**典型任務範例**:
| 實際 commit | 說明 |
|:------------|:------|
| `591ddfe feat: Phase C 空值欄位防護` | 新功能 — 空值防護 |
| `00a09fb feat: Phase D 英文段落保留` | 新功能 — 英文保留 |
| `8e7b590 feat: diversity_guide 語氣強化 + aspect 順序隨機化` | 新功能 — 模板多樣化 |

**影響範圍**: `src/services/`、`src/processors/`、`src/quality/`、`src/schemas/`、`src/storage/`、`src/langgraph_state/`、`src/functions/utils/`

**測試要求**: 必須為新功能新增測試，既有測試必須通過。

**HITL 模式**: 🟡 MEDIUM — architect 抽審 Validate Report

---

## L2 — 除錯修復（Bug Fixing）

**對應 git types**: `fix` / `Fix-NNN`（系列化修復）  
**風險**: 🟡 MEDIUM  
**觸發情境**: 修 bug、調 quality threshold、改 error handler、解 path error、修 lint error

**典型任務範例**:
| 實際 commit | 說明 |
|:------------|:------|
| `3131cfc fix-012: relax no_audience_drift phrase list` | Quality threshold 調鬆 |
| `825a5ca Fix-012: QualityGate audience drift relax` | 系列化修復 |
| `2f67c24 Fix-013: Error Handler abstraction layer refactoring` | 系列化修復 |
| `aec6cee fix: break-rule-list.md 未納入 Lambda 部署映像` | 部署路徑修復 |
| `e489ba1 fix(terms): align Taiwan terminology mappings` | 對應修正 |

**影響範圍**: 任何 `src/` 下的程式碼、`config/*.json`、`src/quality/break-rule-list.md`

**測試要求**: 盡可能補 regression test，既有測試必須通過。

**HITL 模式**: 🟡 MEDIUM — architect 抽審 Validate Report

---

## L3 — 重構架構（Refactoring）

**對應 git types**: `refactor`  
**風險**: 🔴 HIGH — 影響範圍廣  
**觸發情境**: import 路徑統一、abstract layer 抽離、目錄重組、package 結構改造

**典型任務範例**:
| 實際 commit | 說明 |
|:------------|:------|
| `20565e1 Phase 12: pyproject.toml 套件化 + 統一 import 路徑` | 套件架構重構 |
| `c866a1f Phase 12e: langgraph_state import 修正` | Import 路徑修正 |
| `a5848df Phase 12d: src/langchain/tools/ 移除 sys.path.insert` | sys.path 清理 |
| `9a321f7 Phase 12b: src/functions/ import 路徑統一` | Import 統一 |
| `c8d6186 Phase 10a+10b: PromptAssembler 抽象層統一` | 抽象層抽取 |

**影響範圍**: `src/` 全域（import 變更會波及多個檔案）、`pyproject.toml`

**測試要求**: 所有既有測試必須通過，若 import 路徑變更必須更新測試 import。

**HITL 模式**: 🔴 HIGH — Pre-approval 逐條審查

---

## L4 — 發布部署（Release & Deployment）

**對應 git types**: `Release`  
**風險**: 🔴 HIGH — 必須委派 agent-releaser  
**觸發情境**: 版本發布、prod deploy、PR merge（MR）、release governance、CI/CD 監控

**典型任務範例**:
| 實際 commit | 說明 |
|:------------|:------|
| `1faea08 Release v0.17.0: Fix-009 recruitment audience alignment` | 正式 Release |
| `ab9da2f Release v0.16.3: Fix-007 URL punctuation + Fix-008 empty paragraph cleanup` | 正式 Release |
| `69ecc8d Release v0.16.2: Main Release Governance` | 正式 Release |
| `d066ced ci: deploy production from release tags` | CI/CD 部署 |
| `4399dc4 MR → dev: Phase D 英文段落保留` | MR 合併 |

**影響範圍**: `release.json`、`CHANGELOG.md`、`README.md`、`serverless.yml`、`.github/workflows/deploy-*.yml`

**測試要求**: Healthcheck 必須通過，所有 CI checks 必須通過。

**HITL 模式**: 🔴 HIGH — Pre-approval + Releaser delegation（Architect 禁止自行執行）

---

## Routing 規則摘要

```
任務描述關鍵字                                       → 路由到
──────────────────────────────────────────────────────────────────
config, version, bump, changelog, ci, env, dependency → L0 配置&雜務
new, add, feature, processor, strategy, provider      → L1 功能開發
fix, bug, broken, error, wrong, crash, exception      → L2 除錯修復
refactor, restructure, rename, unify, abstract        → L3 重構架構
release, deploy, prod, production, tag, merge all     → L4 發布部署

Confidence < 0.55: 問清楚「這是 config/feature/bug fix/refactor/release？」
跨層匹配: 以最高風險層為主（L4 > L3 > L2 > L1 > L0）
```

---

## Phase 0 / Lane Selector / Task Pool Extension

`routing_map_v1.json` 仍是 L0-L4 canonical source of truth；以下 extension 不改變 L0-L4 keywords、confidence formula、thresholds、cross-layer dominance 或 L4 mandatory delegation。

### Phase 0 Clarifier

當原始需求模糊、缺 success criteria、缺 validation plan，或 `confidence < 0.55` 的淺層澄清不足時，先進入 Phase 0 Clarifier，產出 Execution Contract 後再回到 Intake。

Execution Contract 8 欄位：

| 欄位 | 用途 |
|---|---|
| `clarified_spec` | Intake / Architect 接手的明確需求 |
| `scope_boundary` | in-scope / out-of-scope 防止 scope creep |
| `success_criteria` | 驗收標準 |
| `validation_plan` | QA / 測試策略 |
| `risk_level` | HITL 深度 |
| `recommended_layer` | Clarifier hint；不是 final routing decision |
| `next_step` | 下一步建議 |
| `residual_ambiguity` | 若非空，阻止自動 handoff |

### Lane Selector（v0.4）

Lane Selector 消費 final L0-L4 result，不重新分類：

| Layer | Lane decision | Guardrail |
|---|---|---|
| L0 | Fast Track / Escalated | prod / release-adjacent / runtime / new tests 必須 escalation |
| L1 | Standard | Developer → QA → Architect 抽審 |
| L2 | Quick Fix / Investigate | Quick Fix 不跳過 QA；Investigate 保留 Debugger |
| L3 | High Risk | Human pre-approval |
| L4 | Releaser | `agent-releaser` mandatory；Architect 不得執行 release ops |

### Task Pool + Auto Pilot（v1.0）

Task Pool 是 file-based queue，保存 `execution_contract`、`classifier_result`、`lane_decision`、`retry_count`、`validate_history`、`hitl_state`、dependency / lock / audit fields。

Auto Pilot 邊界：

- L0 safe Fast Track 可產生 diff report / audit log。
- L1/L2/L3 必須保留 QA / HITL。
- L4 必須 Releaser + Human HITL；auto-release paths = 0。
- Validate Gate retry limit 保持 3，`retry_count >= 3` 升級給 User。

> **⚠️ Pool Artifact Boundary**: Pool items in `docs/agent_context/pool/` are **local generated runtime state**, not canonical routing specs. They must not be confused with `routing_map_v1.json` or the intake/architecture docs. Pool artifacts are subject to local file operations (`pool.py`) and are not part of the canonical routing rules.

## 對應的測試覆蓋範圍

| Layer | 類型 | CI 執行 | 手動執行 |
|:------|:-----|:--------|:---------|
| L0 | config/doc/ci | — | — |
| L1 | feat/Phase | 取決於受影響的模組 | 需新增測試 |
| L2 | fix/Fix-NNN | 既有測試 + 回歸測試 | 視需要 |
| L3 | refactor | 所有既有測試 | — |
| L4 | Release | Healthcheck + CI passes | deployment_plan.md + release_log.md |

## 與舊版分類的對照

> 第一版 v1 把分類誤植為程式內部資料流（search → summarize → generate → post-process → error）。  
> 第二版 v2（本文件）更正為開發任務類型，與實際 git log 分類一致。

```
舊 (v1)            新 (v2)
──────────────────────────────
L0 Ingestion  →    L0 配置&雜務
L1 Summary    →    L1 功能開發
L2 Generation →    L2 除錯修復
L3 Post-Proc  →    L3 重構架構
L4 Quality/Err→    L4 發布部署
```
