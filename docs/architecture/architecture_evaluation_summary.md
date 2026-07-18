# Architecture Evaluation Summary

Agent Workflow Framework — 架構評估與總結

> 產出日期: 2026-06-13
> 來源: company-profile-optimizer 專案真實開發流程的萃取與泛化
> 目標: 建立通用的 Agent 協作流程框架，涵蓋任務分類 → 路由 → 開發驗證 → 發布部署

---

## 1. 核心問題

### 1.1 問題陳述

多 Agent 協作開發時，缺乏標準化的：
1. **任務分類標準** — 不同類型的開發任務該走什麼流程？
2. **信心評分機制** — 如何判斷分類的可信度？
3. **品質閘門** — 開發完成後怎麼驗證？反饋迴圈怎麼跑？
4. **風險分級與 HITL** — 什麼情況需要 Human 介入？介入多深？
5. **發布治理** — 誰能執行 release？流程是什麼？
6. **跨 session 任務延續** — clarified tasks 如何排隊、恢復、避免遺失 context？

### 1.2 設計目標

| 目標 | 說明 |
|:-----|:------|
| 模組化 | 每層可獨立替換，不耦合 |
| 風險透明 | Human 永遠知道當前的風險等級 |
| 可追蹤 | 每個決策都有記錄（retry_count、validate_history） |
| 安全邊界 | L4 強制委派，Architect 不得操作 Release |
| 配置驅動 | 分類 keywords、threshold 等可外部配置 |

---

## 2. 整體架構

### 2.1 流程圖

```
User Request (自然語言 / fuzzy idea)
       │
       ▼
┌──────────────────────────────────────┐
│ Phase 0 Clarifier                    │
│ thinking_log + Execution Contract     │
└─────────────┬────────────────────────┘
              │
              ▼
┌──────────────────────────────────────┐
│ Intake Layer + Router                │
│ L0-L4 keyword routing + confidence    │
│ formula / thresholds / dominance      │
└─────────────┬────────────────────────┘
              │
              ▼
┌──────────────────────────────────────┐
│ Lane Selector                         │
│ L0 Fast Track / L2 QuickFix/INV       │
│ L3 HIGH / L4 Releaser guard           │
└─────────────┬────────────────────────┘
              │
              ▼
┌──────────────────────────────────────┐
│ Task Pool + Auto Pilot                │
│ queue / resume / audit / guarded auto │
└─────────────┬────────────────────────┘
              │
              ▼
┌──────────────────────────────────────┐
│ Validate Gate + HITL                  │
│ Developer → QA → retry ≤3 → HITL      │
└─────────────┬────────────────────────┘
              │
              ▼
┌──────────────────────────────────────┐
│ Release Governance                    │
│ 4 guards + Releaser mandatory         │
└──────────────────────────────────────┘
```

### 2.2 Agent 角色矩陣

| Agent | 職責 | 啟動方式 | 禁止事項 |
|:------|:------|:---------|:---------|
| **Architect** | 架構規劃、任務協調、風險評估 | 預設 agent | 執行 release/git push 到 main |
| **Clarifier** | Phase 0 需求澄清、thinking_log、Execution Contract | Architect preprocessing mode | 直接實作、取代 Router final decision |
| **Developer** | 功能實作、測試撰寫 | `task(agent-developer)` | 自行定義不規範的架構 |
| **QA** | Validate Gate 驗證 | `task(agent-qa)` | 修改實作程式碼 |
| **Debugger** | 根因分析、問題定位 | `task(agent-debugger)` | 跳過 Hypothesis loop |
| **Releaser** | 發布部署、CI/CD 監控 | `task(agent-releaser)` | 無 Human 批准前執行 |
| **Task Pool / Auto Pilot** | 跨 session queue、resume、audit、guarded auto policy | Spec-time policy / future runtime | L4 auto-release、跳過 Validate Gate |

### 2.3 協作流程（三階段）

```
Phase 1: 規劃 (Architect)
  ├── 需求分析 + 架構掃描
  ├── 測試類別覆蓋矩陣定義
  ├── 風險分級 (L0–L4)
  └── 產出 TaskPlan.md + DeveloperPrompt.md

Phase 2: 開發 + Validate 反饋迴圈 (Architect 協調)
  ├── task(agent-developer, DeveloperPrompt)
  │   └── Developer 實作 → 回傳完成通知
  ├── task(agent-qa, ValidateRequest)
  │   └── QA 產出 Validate Report
  ├── Validate PASS ? → Phase 3
  │   Validate FAIL ? → retry_count < max_retry ?
  │     ├── Yes → 帶 Report 重啟 Developer
  │     └── No  → 升級給 User 判斷
  └── 記錄 retry_count + validate_history

Phase 3: HITL (Architect 依風險分級執行)
  ├── 🟢 LOW → Auto-approve
  ├── 🟡 MEDIUM → 抽審 Validate Report
  └── 🔴 HIGH → Pre-approval 逐條審查
```

---

## 3. 各層詳細設計

### 3.1 L0 — 配置 & 雜務 (Config & Housekeeping)

| 屬性 | 值 |
|:-----|:----|
| Git types | chore, docs, ci |
| 風險 | 🟢 LOW |
| HITL | Auto-approve（prod config 例外） |
| 測試要求 | 不需新測試 |
| 路由 Agent | Developer（輕量）/ Architect（需審查） |

**典型任務**: 改 `config/*.json`、bump 版本號、更新 README、調 CI workflow、改 dependency

**設計理由**: 配置變更通常是確定性的、可逆的、影響範圍限定的。不應消耗 Validate Gate 資源。

### 3.2 L1 — 功能開發 (Feature Development)

| 屬性 | 值 |
|:-----|:----|
| Git types | feat, Phase |
| 風險 | 🟡 MEDIUM |
| HITL | 抽審 Validate Report |
| 測試要求 | 新功能必須新增測試 |
| 路由 Agent | Architect → Developer → QA |

**典型任務**: 新增 processor、quality check、search strategy、storage backend

**設計理由**: 新功能需要完整的 Validate Gate（Developer + QA 反饋迴圈），但不需要 Pre-approval。Architect 抽審 Validate Report 確保品質。

### 3.3 L2 — 除錯修復 (Bug Fixing)

| 屬性 | 值 |
|:-----|:----|
| Git types | fix, Fix-NNN |
| 風險 | 🟡 MEDIUM |
| HITL | 抽審 Validate Report |
| 測試要求 | 盡可能補 regression test |
| 路由 Agent | Debugger → Developer → QA |

**典型任務**: 調 quality threshold、修 path error、改 error handler、解 lint error

**設計理由**: Bug fix 需要 Debugger 先定位 root cause（形成 ≥3 hypotheses），再由 Developer 修復。比 L1 多一個 Debugger 前置步驟。

### 3.4 L3 — 重構架構 (Refactoring)

| 屬性 | 值 |
|:-----|:----|
| Git types | refactor |
| 風險 | 🔴 HIGH |
| HITL | Pre-approval 逐條審查 |
| 測試要求 | 所有既有測試通過 |
| 路由 Agent | Architect → Developer → QA |

**典型任務**: import 路徑統一、abstract layer 抽離、目錄重組、sys.path 清理

**設計理由**: 重構影響範圍廣，可能波及整個 codebase。需要 Architect 先產出架構設計文檔 + 影響範圍分析，Human Pre-approval 後才能執行。

### 3.5 L4 — 發布部署 (Release & Deployment)

| 屬性 | 值 |
|:-----|:----|
| Git types | Release |
| 風險 | 🔴 HIGH |
| HITL | Pre-approval + Releaser 強制委派 |
| 測試要求 | Healthcheck + 所有 CI 通過 |
| 路由 Agent | Releaser（Architect 不得執行） |

**典型任務**: 版本發布、prod deploy、MR merge、release governance

**設計理由**: Release 是最高風險操作。Architect 必須禁止執行 git push/merge/tag/release 操作，由專屬 Releaser agent 接管。每一步都需 Human 批准。

---

## 4. Intake Layer：分類與信心評分

### 4.1 通用模型

```
input → classifier → confidence scorer → router → workflow lane
```

### 4.2 信心評分公式

```
confidence = 0.65 * margin_component + 0.35 * ratio_component

margin_component = (top_score - second_score) / top_score
ratio_component  = top_score / total_keywords_in_layer
```

**公式設計理由**:
- `margin_component` (權重 0.65) — 衡量分類的區辨力。top 與 second 差距越大，信心越高。
- `ratio_component` (權重 0.35) — 衡量匹配的密度。匹配數佔該層 keywords 比例越高，信心越高。
- 權重傾斜 margin，因為區辨力比匹配數量更能反映分類正確性。

### 4.2.1 Phase 0 Execution Contract handoff

若 request 模糊或 `confidence < 0.55` 且淺層澄清不足，先由 Phase 0 Clarifier 產出 Execution Contract，再回到 Intake。

`recommended_layer` 是 hint，不是 final decision。Final routing 仍遵守 `routing_map_v1.json` 的 formula / thresholds / dominance。

> **Governance Audit Note**: Phase 0, Lane Selector, and Task Pool extensions are workflow extensions around the canonical router. Governance audit verifies that these extensions do not modify canonical routing invariants: confidence formula, thresholds, cross-layer dominance order, L4 mandatory delegation, or Validate Gate semantics. Generated pool artifacts in `docs/agent_context/pool/` are local runtime state and are not canonical routing specs.

### 4.3 路由閾值

| 信心值 | 模式 | 行為 |
|:------|:-----|:------|
| ≥ 0.85 | strict | 直接路由，不探索 |
| 0.55–0.84 | guarded | 路由 + 允許先讀取 affected files |
| < 0.55 | standard | 退回澄清問題 |

### 4.4 跨層壓制規則

當一句話涵蓋多層 keywords 時，以最高風險層為主：

```
L4 發布部署 > L3 重構架構 > L2 除錯修復 > L1 功能開發 > L0 配置&雜務
```

---

## 5. Validate Gate（品質閘門）

### 5.1 設計目標

- Developer 和 QA 都知道閘門在哪裡
- 反饋迴圈有上限（max 3次），避免無窮迴圈
- 驗收標準必須可量化

### 5.2 測試類別覆蓋矩陣

對每個新增或修改的輸出欄位，必須定義：

| 測試類別 | 檢查問題 | 測試案例 | 通過標準 |
|:---------|:---------|:---------|:---------|
| 🟢 正面測試 | 正確情境值正確？ | ... | ... |
| 🔴 負面測試 | 錯誤值不該出現？ | ... | ... |
| 📏 範圍測試 | 值在合理範圍？ | ... | ... |
| 🎯 正確性測試 | 值與真實狀態一致？ | ... | ... |
| 🔲 邊界測試 | 邊界值行為正確？ | ... | ... |

### 5.3 反饋迴圈規則

```
retry_count = 0 (起始)

Developer 實作 → QA Validate
  ├── PASS → 進入 Phase 3 HITL
  └── FAIL → retry_count += 1
              ├── retry_count < max_retry (3)
              │   └── 帶 Validate Report 重啟 Developer
              └── retry_count >= 3
                  └── 升級給 User 判斷
```

**Validate Report 內容**: QA 產出，包含具體問題位置與修正建議。

---

## 5.4 Lane Selector 與 Task Pool

Lane Selector 不重新分類 L0-L4，只根據 `classifier_result.final_layer` 和 Execution Contract 決定 execution lane：

| Layer | Lane | Guardrail |
|---|---|---|
| L0 | Fast Track / Escalated | prod/release-adjacent/runtime/new tests 必須 escalation |
| L1 | Standard | Developer → QA → Architect review |
| L2 | Quick Fix / Investigate | Quick Fix 不跳過 QA；Investigate 保留 Debugger |
| L3 | High Risk | Human pre-approval |
| L4 | Releaser | `agent-releaser` mandatory；auto-release path = 0 |

Task Pool + Auto Pilot 保存 clarified tasks 的 queue state、retry_count、validate_history、HITL state、dependency/lock/audit fields。Auto Pilot 僅在 safe L0 lane 產生 diff report / audit log；L1/L2/L3/L4 不可繞過 Validate Gate / HITL。

---

## 6. HITL 模式

### 6.1 三級審查

| 等級 | 風險 | 模式 | 說明 |
|:----:|:----:|:-----|:------|
| 🟢 LOW | 配置、雜務 | Auto-approve | CI 通過即視為核准 |
| 🟡 MEDIUM | 功能、修復 | 抽審 | Architect 抽查 Validate Report |
| 🔴 HIGH | 重構、發布 | Pre-approval | Human 逐條審查後才能執行 |

### 6.2 Pre-approval 審查項目 (🔴 HIGH)

1. TaskPlan.md 完整性（scope、risks、test strategy）
2. Impact analysis（受影響的檔案清單）
3. Rollback plan（如果出錯怎麼回退）
4. Timeline & milestones
5. ✅ Human 簽核

---

## 7. Release Governance（發布治理）

### 7.1 四道關卡

```
Guard 1: 格式驗證
  確認 tag 格式 ^v[0-9]+\.[0-9]+\.[0-9]+$

Guard 2: release.json 一致性
  確認 release.json 的 release_tag 等於 git tag

Guard 3: 祖先鏈檢查
  確認 commit 包含在 origin/main 中

Guard 4: Human 確認
  由 Human 輸入確認字串 (DEPLOY_PROD) 啟動部署
```

### 7.2 Releaser 強制委派

Architect 收到 release/deploy 相關請求時：

1. **告知**：「此任務屬於 Releaser 職責，我將委派 agent-releaser。」
2. **傳遞上下文**：
   - 使用者原始指令
   - 目前 stage/version/commit
   - 已完成的測試結果與風險摘要
3. **透過 task tool 啟動 agent-releaser**
4. **禁止自行執行**：
   - ❌ `git merge` 到 `mr` / `main`
   - ❌ `git push origin mr/main`
   - ❌ `gh pr merge`
   - ❌ `git tag` / `gh release create`
   - ❌ `git push --force`

### 7.3 Releaser 工作流

```
Audit → Branch → Commit (conventional commits) → PR/MR → CI/CD Monitor
```

每一步需要建立文件：
- `docs/agent_context/release_{version}/deployment_plan.md`
- `docs/agent_context/release_{version}/release_log.md`

---

## 8. 關鍵設計決策

### 8.1 為什麼是 L0–L4 而不是 L1–L5？

L0 的存在理由是：配置變更和雜務不應該走完整的開發流程。它們不需要 Validate Gate、不需要 QA 驗證、可以 auto-approve。把「不做完整流程」也定義為一層，避免流程的強制路徑。

### 8.2 為什麼 Intake Layer 用 keyword matching 而不是 LLM 分類？

| 方案 | 優點 | 缺點 | 結論 |
|:-----|:------|:-----|:------|
| LLM 分類 | 語意理解強 | 延遲高、成本高、不確定性 | ❌ 不適合 routing |
| Keyword matching | 即時、確定性、可調試 | 語意理解弱 | ✅ 適合 routing |
| Hybrid | 兩者優點 | 更複雜 | ⏳ V2 可以考慮 |

**決策**: 第一版用 keyword matching。確定性對 routing 至關重要 — 同一個 input 必須永遠得到同一個 routing 結果。

### 8.3 為什麼 confidence 公式用 margin + ratio 非 machine learning？

ML 方案（分類器）需要 labeled dataset，這在專案初期不存在。Margin + ratio 是零樣本方案，只需要 keywords list。隨著專案成熟，可以收集 training data 後升級為 ML 方案。

### 8.4 為什麼 Agent 用 task tool 而非 delegation tool？

- `task` tool 是 OpenCode native，支援 subagent_type 指定 agent
- `delegate_tool` 沒有保證的 agent 切換行為
- 硬規則寫在 AGENTS.md 中，確保 Validate Gate 流程的一致性

### 8.5 為什麼測試類別覆蓋矩陣要五種？

| 類別 | 如果沒測會怎樣 |
|:-----|:--------------|
| 正面 | 正常情境壞了也不知道 |
| 負面 | 錯誤值不會被擋 |
| 範圍 | 超出邊界沒人管 |
| 正確性 | 值對了但語意錯了 |
| 邊界 | 極端值直接 crash |

五種缺一不可，完整覆蓋才能通過 Validate Gate。

---

## 9. 風險評估

### 9.1 已知風險

| 風險 | 影響 | 緩解措施 |
|:-----|:------|:---------|
| Keyword matching 可能誤分類 | 路由到錯誤的 workflow | Cross-layer dominance + fallback to ask |
| retry 上限可能不夠 | 真實 bug 需要 >3 次迭代 | Upgrade to Human 機制 |
| Architect 可能誤執行 release | 未經 governance 的 prod deploy | AGENTS.md 硬規則 + Pre-approval |
| QA 可能遺漏 critical bug | Production regression | 測試類別覆蓋矩陣 + HITL 抽審 |
| L3 重構影響範圍被低估 | 未預期的 side effect | Pre-approval 逐條審查 + 所有測試通過 |

### 9.2 假設

1. Agent 能夠正確遵循 AGENTS.md 中的硬規則
2. Human 在 🔴 HIGH 等級時會即時介入
3. Keywords list 能涵蓋 80%+ 的常見任務類型
4. Validate Report 的品質足夠讓 Developer 修正

---

## 10. 與 company-profile-optimizer 的關係

這個 framework 是從 company-profile-optimizer 的實際開發流程中萃取出來的通用模型。

| 項目 | company-profile-optimizer | agent-workflow-framework (通用) |
|:-----|:-------------------------|:------------------------------|
| 領域 | 公司簡介生成 API | 通用 Agent 協作 |
| Agent 數量 | 6 (architect/developer/qa/debugger/expert/releaser) | 5 (同上，expert optional) |
| Validate Gate | 已在 AGENTS.md 中定義 | 從 spec 提煉為通用流程 |
| Release 流程 | `docs/agent_context/release_*/` | 同上，路徑可配置 |
| L0–L4 分類 | 第一版誤植為內部資料流 | 修正為開發任務類型 |
| 配置 | config/*.json | framework 層用 JSON schema |

---

## 11. 長程規劃（Future backlog）

> 注意：本節是 Phase v2.0 之後的長程 backlog，不是 Phase v2.0 Graphify Optional Enhancement。Phase v2.0 僅規劃 Graphify 作為 optional context enrichment；以下項目需另開 phase 才能實作。

- [ ] **中文 keywords 支援** — keywords 擴充中英文並列
- [ ] **自動分類腳本** — `src/intake/classifier.py`，可執行 confidence 計算
- [ ] **ML 分類器** — 收集足夠 labeled data 後升級
- [ ] **Validate Gate 自動化** — 自動執行測試類別覆蓋矩陣檢查
- [ ] **Dashboard** — 可視化每個 task 的 retry_count、validate_history
- [ ] **Adapter Pattern** — 讓 framework 可以接入其他 agent platform（不僅限 OpenCode）
