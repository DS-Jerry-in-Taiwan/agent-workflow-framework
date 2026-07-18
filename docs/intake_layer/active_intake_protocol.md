# Active Intake Protocol

定義 Intake Layer（預選擇層）如何將使用者原始需求分類、評分、路由到對應的工作流程。

---

## 流程概覽

```
User request (原始自然語言)
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 0: Phase 0 Clarifier          │
  │  若需求模糊 / 缺 completion 標準，   │
  │  先產出 Execution Contract           │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 1: Keyword Detection          │
  │  掃描 keywords，算每個 layer 的匹配  │
  │  分數                                 │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 2: Confidence Scoring         │
  │  confidence = 0.65*margin + 0.35*  │
  │  ratio                               │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 3: Routing Decision           │
  │  ≥0.85 → direct (name the agent)    │
  │  ≥0.55 → guarded (name + explore)   │
  │  <0.55 → clarify / Phase 0 fallback │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 4: Cross-Layer Dominance      │
  │  如果命中多層，取最高風險層          │
  │  L4 > L3 > L2 > L1 > L0            │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 5: Layer-Specific Workflow    │
  │  (見下方各層細部流程)                │
  └─────────────────────────────────────┘
       │
       ▼
  ┌─────────────────────────────────────┐
  │  Step 6: Lane Selector + Task Pool  │
  │  v0.4 lane decision → v1.0 pool     │
  │  Auto Pilot 僅在 guardrails 內運作  │
  └─────────────────────────────────────┘
```

---

## Step 0 — Phase 0 Clarifier（需求澄清入口）

當原始需求不具備足夠開發條件時，先進入 Phase 0 Clarifier，再回到 Intake。

**觸發條件**：
- 使用者需求模糊，無明確 scope / success criteria / validation method。
- `confidence < 0.55` 且淺層分類問題不足以釐清。
- 任務描述包含多種可能路徑，需要先定義不做什麼。
- 需要跨 session resume 的 thinking context。

**Phase 0 產出**：

```yaml
thinking_log: docs/agent_context/thinking/{topic}_thinking.md
execution_contract:
  clarified_spec: string
  scope_boundary:
    in_scope: list[string]
    out_of_scope: list[string]
  success_criteria: list[string]
  validation_plan: list[string]
  risk_level: LOW | MEDIUM | HIGH
  recommended_layer: L0 | L1 | L2 | L3 | L4   # hint only
  next_step: string
  residual_ambiguity: list[string]
```

`recommended_layer` 只是 Clarifier hint，final routing 仍由 Intake / Router 根據 canonical rules 決定。

---

## Step 1 — Keyword Detection

對使用者輸入掃描 `routing_map_v1.json` 中每個 layer 定義的 keywords。

> **注意**：`routing_map_v1.json` 仍是 L0-L4 canonical source of truth。Phase 0 Clarifier、Lane Selector、Task Pool / Auto Pilot 是 Intake 前後的 workflow extension，不取代 routing JSON 的公式、thresholds、dominance order 或 L4 governance。

**L0/L1 boundary note**: Certain inputs containing both L0 keywords (e.g., `docs`, `config`) and L1 keywords (e.g., `new`, `add`) may route to L1. This is by design: ambiguous docs/config wording defaults to the higher-risk workflow instead of L0 auto-approval. For docs-only tasks that happen to contain feature-like words, Human may override through Phase 0 Clarifier or explicit Architect instruction. This note does **not** modify `routing_map_v1.json` keywords or dominance order.

**計分方式**:
- 每個 layer 獨立計算匹配分數
- `match_count(layer) = number of keywords matched in user input`
- 只記錄 `match_count > 0` 的 layer

---

## Step 2 — Confidence Scoring

對 top 2 匹配的 layer 計算 confidence：

```
confidence = 0.65 * margin_component + 0.35 * ratio_component

margin_component = (top_score - second_score) / top_score
ratio_component  = top_score / total_keywords_in_layer
```

**範例**:
- User says: "修一下 quality threshold，no_audience_drift 太敏感了"
- L2 匹配: {fix, threshold, quality, audience_drift} = 4
- L1 匹配: {quality} = 1
- top=4, second=1, L2 total_keywords=24
- margin = (4-1)/4 = 0.75
- ratio = 4/24 = 0.167
- confidence = 0.65*0.75 + 0.35*0.167 = 0.487 + 0.058 = 0.545
- **結果**: < 0.55 → 問清楚「這是除錯修復還是功能調整？」

---

## Step 3 — Routing Decision

| Threshold | Mode | 行為 |
|:----------|:-----|:------|
| ≥ 0.85 | strict | 直接路由到對應 agent，不探索 |
| 0.55–0.84 | guarded | 路由 + 允許先讀取 affected files |
| < 0.55 | standard | 退回澄清問題 |

**澄清問題（fallback）**:
> 「你說的這個需求，是屬於哪一類？
> - 改 config / 版本號 / CI（L0）
> - 新功能開發（L1）
> - 修 bug / 調 threshold（L2）
> - 重構架構（L3）
> - 發布上線（L4）」

若上述淺層問題仍不足以產生完整 Execution Contract，則升級到 **Step 0 Phase 0 Clarifier**，以 one-question-at-a-time 方式補齊 contract 後再回到 Intake。

---

## Step 4 — Cross-Layer Dominance

當使用者一句話涵蓋多層時（例如「修完 bug 後順便發布」），以最高風險層為主：

```
L4 發布部署 (🔴 HIGH)    → 壓制所有其他層
L3 重構架構 (🔴 HIGH)    → 壓制 L0/L1/L2
L2 除錯修復 (🟡 MEDIUM)  → 壓制 L0/L1
L1 功能開發 (🟡 MEDIUM)  → 壓制 L0
L0 配置&雜務 (🟢 LOW)    → 無壓制能力
```

> **Note on `dominance_applied` flag**: Runtime classifier output keeps `dominance_applied` as a boolean diagnostic field. The canonical decision remains the highest-risk matched layer according to `DOMINANCE_ORDER`; diagnostic wording must not be treated as a separate routing source of truth.

---

## Step 5 — Layer-Specific Workflow

### L0: 配置 & 雜務

```
Route to: developer (輕量變更) / architect (需審查)
HITL: 🟢 LOW

工作流:
1. Lane Selector 檢查是否符合 L0 Fast Track eligibility
2. Safe L0 → Developer 產出 diff report + audit log（不需要 QA）
3. 任一 escalation trigger → 轉 L1/L2 標準 Validate Gate
4. 若涉及 prod / release-adjacent config → 禁止 Fast Track
5. 若涉及 release/deploy/tag/prod → L4 Releaser mandatory
```

### L1: 功能開發

```
Route to: architect → developer → qa (Validate Gate)
HITL: 🟡 MEDIUM

工作流:
1. Architect 產出 TaskPlan.md + DeveloperPrompt.md
2. Developer 根據 DeveloperPrompt 實作 + 寫測試
3. QA 執行 Validate Gate（test coverage matrix）
4. retry_count 上限 3 次
5. Architect 抽審 Validate Report
6. Human 確認後 PR merge
```

### L2: 除錯修復

```
Route to: debugger (定位) → developer (修復) → qa (驗證)
HITL: 🟡 MEDIUM

工作流:
1. Lane Selector 判斷 Quick Fix 或 Investigate
2. Quick Fix：root cause known + regression validation available → Developer → QA → Architect 抽審 lane decision
3. Investigate：root cause unknown / cross-module / hypotheses required → Debugger 3 hypotheses → Developer → QA → Architect
4. retry_count 上限 3 次
5. Architect 抽審 Validate Report
```

### L3: 重構架構

```
Route to: architect → developer → qa
HITL: 🔴 HIGH (pre-approval)

工作流:
1. Architect 產出架構設計文檔 + 影響範圍分析
2. Pre-approval 逐條審查（Human 簽核）
3. Developer 逐步實作（Phase多階段）
4. 每階段跑所有既有測試
5. QA 驗證無回歸
```

### L4: 發布部署

```
Route to: releaser（強制委派，architect 不得執行）
HITL: 🔴 HIGH (mandatory pre-approval)

工作流:
1. Architect 告知「此任務屬於 Releaser 職責」
2. Architect 透過 task tool 啟動 agent-releaser
3. 傳遞上下文：
   - 使用者原始指令
   - 目前 stage/version/commit
   - 已完成的測試結果與風險摘要
4. Releaser 建立 deployment_plan.md + release_log.md
5. 每一步（branch / commit / PR / merge / tag / deploy）前取得 Human 批准

Architect 禁止執行:
  ❌ git merge 到 mr/main
  ❌ git push origin mr/main
  ❌ gh pr merge
  ❌ git tag / gh release create
  ❌ git push --force
```

---

## Step 6 — Lane Selector + Task Pool / Auto Pilot

完成 L0-L4 routing 後，v0.4 Lane Selector 會根據 `classifier_result` 與 Execution Contract 產生 `lane_decision`。v1.0 Task Pool / Auto Pilot 只消費這些結果，不重新分類任務。

### 6.1 Lane Selector input / output

```yaml
classifier_result:
  final_layer: L0 | L1 | L2 | L3 | L4
  confidence: number
  conflict_status: aligned | conflict_reviewed | scorer_dominance

lane_decision:
  lane: L0_Fast_Track | L1_Standard | L2_QuickFix | L2_Investigate | L3_HighRisk | L4_Releaser
  required_agents: list[string]
  qa_required: boolean
  hitl_required: boolean
  hitl_mode: auto_approve | review | pre_approval
```

### 6.2 Task Pool / Auto Pilot guardrails

| Lane | Auto Pilot 行為 | 不可繞過的 guard |
|---|---|---|
| L0_Fast_Track | 可產生 diff report / audit log | 僅限 safe L0；prod/release-adjacent 必須 escalation |
| L1_Standard | queued workflow | Developer → QA → Architect 抽審 |
| L2_QuickFix | queued workflow | QA regression + Architect lane review |
| L2_Investigate | queued workflow | Debugger → Developer → QA → Architect |
| L3_HighRisk | blocked until approval | Human pre-approval |
| L4_Releaser | blocked until Releaser + HITL | `agent-releaser` mandatory；auto-release path = 0 |

Task Pool item 必須記錄 `retry_count`、`validate_history`、`hitl_state`、`depends_on` / `blocked_by` 與 audit log。Validate Gate FAIL 時 `retry_count + 1`；`retry_count >= 3` 必須升級給 User。

---

## 實際案例

### 案例 1: 「新增一個 processor 做括號清洗」

```
Step 1 keyword detection:
  L1: new(✓), add(✓), processor(✓) → 3 matches
Step 2 confidence:
  top=3, second=0 (其他層0 match)
  margin = (3-0)/3 = 1.0
  ratio = 3/8 (L1 has 8 keywords...) → wait, let me count L1 keywords
  L1 keywords ≈ 14, ratio = 3/14 = 0.214
  confidence = 0.65*1.0 + 0.35*0.214 = 0.65 + 0.075 = 0.725
Step 3: 0.725 ≥ 0.55 → guarded routing
Step 4: 只有 L1 命中 → 無需跨層
Step 5: L1 workflow

✅ 路由正確 → 功能開發
```

### 案例 2: 「改一下 search_config，provider 換成 tavily」

```
Step 1 keyword detection:
  L0: config(✓), search_config(✓) → 2 matches
Step 2 confidence:
  top=2, second=0
  margin = 1.0
  ratio = 2/24 = 0.083
  confidence = 0.65*1.0 + 0.35*0.083 = 0.65 + 0.029 = 0.679
Step 3: 0.679 ≥ 0.55 → guarded
Step 4: 只有 L0
Step 5: L0 workflow

✅ 路由正確 → 配置變更
```

### 案例 3: 「發版 v0.17.0，deploy prod」

```
Step 1 keyword detection:
  L4: release(✓), v0.(✓), deploy(✓), prod(✓), version(✓) → 5 matches
  L0: version(✓) → 1 match (但 L4 壓制)
Step 2 confidence:
  L4 top=5, L0 second=1
  margin = (5-1)/5 = 0.80
  ratio = 5/20 = 0.25
  confidence = 0.65*0.80 + 0.35*0.25 = 0.52 + 0.0875 = 0.6075
Step 3: 0.6075 ≥ 0.55 → guarded
Step 4: L4 > L0 → 發布部署
Step 5: L4 workflow → 委派 releaser

✅ 路由正確 → 發布部署（releaser 接管）
```

### 案例 4: 「幫我看看為什麼 quality gate 一直 false positive」

```
Step 1 keyword detection:
  L2: fix(?), quality(✓), false positive(✓), gate(?) → gate not in L2 keywords
  L2: quality not directly... let me check.
  Actually: "fix" is in L2 keywords, "quality" is in L1
  Hmm, "false positive" is an L2 keyword
  L1: quality check → but "quality" alone... let's check.
  L1 keywords includes "quality check" as a phrase
  
  This is fuzzy matching territory. The user didn't say "fix" explicitly.
  Keywords matched: quality(L1), false positive(L2), gate(not in any list)
  
  L2 match: false positive(✓) = 1
  L1 match: quality(✓) = 1
  Both low.

Step 2: confidence < 0.55
Step 3: fallback → ask clarifying question

問: 「你說的 quality gate 問題，是要修 bug（L2）還是想加新檢查（L1）？」
```

---

## 邊界案例處理

| 情境 | 處理方式 |
|:-----|:---------|
| 使用者只說「幫我看一下」 | confidence = 0 → 直接問「你要改 config/加功能/修 bug/重構/發布？」 |
| 任務描述模糊但包含檔案路徑 | 用 file path 反推 typical_files 屬於哪層 |
| 跨層任務（修 bug 後順便發布） | 最高風險層主導，但不忽略其他層的工作項目 |
| 一句話包含多層 keywords | 列出所有命中的層，Human 確認優先順序 |
| 使用非英文描述（中文需求） | keywords 目前以英文為主；中文描述 → confidence 下降 → 退回澄清 |

---

## 長期開發方向（Future backlog）

> 注意：以下是長期 backlog，不是 Phase v2.0 Graphify Optional Enhancement。Phase v2.0 僅處理 Graphify optional context enrichment。

- [ ] 中文 keywords 支援（中英並列）
- [ ] 自動計算 confidence 的腳本（`scripts/intake_classify.py`）
- [ ] 整合到 OpenCode 角色 prompt 開頭，讓每個 session 自動分類
- [ ] 建立 Layer 切換流程：如果開發過程中發現需要跨層，該怎麼處理
