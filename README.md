# Agent Workflow Framework

Agent 工作流程編排框架 — Intake Layer（任務分類） → Routing（路由） → Validate Gate（品質閘門） → Release Governance（發布治理）。

從 OpenCode Validate Gate 流程的實際開發經驗中萃取，作為可重複使用的 Agent 協作框架。

---

## 核心理念

將開發任務分為五層（L0–L4），每層有不同的風險等級、路由規則、HITL 模式：

| 層級 | 名稱 | 風險 | HITL |
|:----:|:-----|:----:|:-----|
| L0 | 配置 & 雜務 | 🟢 LOW | auto-approve |
| L1 | 功能開發 | 🟡 MEDIUM | 抽審 |
| L2 | 除錯修復 | 🟡 MEDIUM | 抽審 |
| L3 | 重構架構 | 🔴 HIGH | pre-approval |
| L4 | 發布部署 | 🔴 HIGH | 強制委派 Releaser |

## 流程架構

```
User Request (自然語言)
       │
       ▼
┌─────────────────────┐
│  Intake Layer       │  分類 + 信心評分
│  (keyword detection │
│   + confidence)     │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Router             │  路由到對應 Agent / Workflow
│  (L0–L4 dispatch)   │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Workflow Lane      │  Architect → Developer → QA
│  (Validate Gate)    │  (max 3 retries)
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  HITL Gate          │  LOW:auto / MEDIUM:抽審 / HIGH:pre-approval
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Release Governance │  4 guards: format→release.json→ancestry→confirm
│  (Releaser)         │
└─────────────────────┘
```

## 目錄結構

```
agent-workflow-framework/
├── config/
│   └── routing_map_v1.json      # 機讀分類資料
├── docs/
│   ├── intake_layer/          # 任務分類與信心評分
│   │   ├── routing_map_analysis.md  # 分類分析
│   │   └── active_intake_protocol.md # 運作流程
│   ├── validate_gate/         # QA 驗證閘門（TODO）
│   ├── release_governance/    # 發布治理（TODO）
│   └── architecture/          # 架構設計（TODO）
├── src/
│   ├── intake/                # 分類器、信心評分（TODO）
│   ├── router/                # 路由邏輯（TODO）
│   ├── validate/              # Validate Gate（TODO）
│   └── release/               # Release Governance（TODO）
├── tests/                     # 測試（TODO）
└── README.md
```

## Quick Start

（尚未實作 — 當前為規格定義階段。）

## License

Private / Internal.
