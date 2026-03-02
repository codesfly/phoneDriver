# PhoneDriver 第一阶段优化报告（多轮反馈闭环）

日期：2026-03-02  
范围：`/home/jiumu/.openclaw/workspace/PhoneDriver`  
原则：最小改动、禁止功能扩张、保持连续任务逻辑不回退

---

## 1) 结论
本轮已完成第一阶段目标：
1. **失败后反馈循环**：动作失败后执行“重新截图 -> 失败分类 -> 请求模型修正动作（附失败原因）”，并避免盲重试同动作。  
2. **动态重试预算**：按任务复杂度动态设置连续失败上限（simple=2 / medium=4 / complex=6，cap=8，可配置）。  
3. **可观测性**：记录 `retry_reason`（坐标偏差/页面未加载/弹窗阻断/未知）与每轮修正决策（`retry_decisions`）。

验证通过：`py_compile` + 2个函数级测试 + 1个E2E smoke（均带 timeout）。

---

## 2) 实施内容（核心改动）

### A. 失败反馈闭环（phone_agent.py）
新增并接入：
- `PhoneAgent._classify_retry_reason(...)`
- `PhoneAgent._run_failure_feedback_loop(...)`
- `PhoneAgent._actions_equivalent(...)`

执行路径（失败时）：
1. 失败分类（`retry_reason`）
2. 重新截图
3. 调用 `vl_agent.analyze_screenshot(..., retry_feedback=...)` 请求修正动作
4. 若修正动作与失败动作等价，强制再请求一次替代动作
5. 记录决策到 `context['retry_decisions']`

关键日志点：
- `Retry loop triggered ... reason=...`
- `Retry correction decision: reason=..., action=..., success=...`

### B. 动态重试预算（phone_agent.py）
新增：
- `PhoneAgent._estimate_task_complexity(...)`
- `PhoneAgent._resolve_retry_budget(...)`

规则：
- simple=2
- medium=4
- complex=6
- 上限 cap=8
- 通过 config 可调，默认开启 `enable_dynamic_retry_budget=true`

任务启动时输出：
- `Retry budget resolved: level=..., budget=..., cap=...`

### C. 模型修正提示增强（qwen_vl_agent.py）
- `analyze_screenshot` 新增可选参数：`retry_feedback`
- 在 user query 里附加失败原因分类、失败动作、错误文本、轮次，明确要求“输出修正动作，避免重复失败动作”。

### D. 配置补齐（config/ui）
在 `config.json`、`ui.py` 默认配置补充：
- `enable_dynamic_retry_budget`
- `retry_budget_simple`
- `retry_budget_medium`
- `retry_budget_complex`
- `retry_budget_cap`

---

## 3) 验证结果（含 timeout）

### 3.1 编译检查
```bash
timeout 60 ./.venv/bin/python -m py_compile phone_agent.py qwen_vl_agent.py ui.py tests/test_phase1_functions.py tests/test_phase1_smoke.py
```
结果：✅ 通过

### 3.2 函数级测试（2项）
```bash
timeout 60 ./.venv/bin/python -m unittest -v tests/test_phase1_functions.py
```
覆盖：
- `test_classify_retry_reason`（失败分类）
- `test_dynamic_retry_budget`（动态预算）

结果：✅ 2/2 通过

### 3.3 端到端 smoke（1项）
```bash
timeout 60 ./.venv/bin/python -m unittest -v tests/test_phase1_smoke.py
```
覆盖：
- 首次动作失败 -> 触发反馈闭环 -> 修正动作成功 -> 完成检查通过

结果：✅ 通过

---

## 4) 失败显式报错与最短修复路径
本轮执行中出现过两处测试失败，已显式暴露并修复：

1. **失败分类误判**（`Permission dialog blocked interaction` 被误判成“坐标偏差”）  
- 根因：分类关键字中 `miss` 过于宽泛（会命中 permission）  
- 最短修复：从坐标类关键字移除 `miss`

2. **cap 测试样例不充分**（未触发 complex 档位导致断言错误）  
- 根因：测试输入复杂度不足，预算落在 medium=4  
- 最短修复：提高测试任务复杂度并设置 `retry_budget_complex=7` + `cap=5`，验证封顶行为

若后续失败：
- 优先看日志关键字：`Retry loop triggered` / `correction_repeated_same_action` / `Max retries exceeded`  
- 最短修复路径：
  1) 调整 `_classify_retry_reason` 关键词；
  2) 调整复杂度规则 `_estimate_task_complexity`；
  3) 调整配置预算参数（simple/medium/complex/cap）。

---

## 5) 变更文件
- `/home/jiumu/.openclaw/workspace/PhoneDriver/phone_agent.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/qwen_vl_agent.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/config.json`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/ui.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase1_functions.py`（新增）
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase1_smoke.py`（新增）

---

## 6) 核心 diff 点（摘要）
- `phone_agent.py`
  - 新增失败分类、复杂度估计、预算解析、反馈闭环执行
  - `execute_task` 在失败路径接入反馈闭环，并使用动态预算判定连续失败耗尽
  - `context` 中新增可观测字段：`retry_round` / `last_retry_reason` / `retry_decisions`
- `qwen_vl_agent.py`
  - `analyze_screenshot(..., retry_feedback=None)`
  - 模型请求中注入失败回馈约束，推动动作修正而非原样重试
- `config.json` / `ui.py`
  - 增加动态重试预算配置项
- tests
  - 新增函数测试 + smoke 测试，覆盖阶段目标

---

## 7) 风险评估
低风险，主要是失败分支增强与重试策略精细化。潜在风险：
- 复杂度判定仍是启发式，边界任务可能在 medium/complex 之间波动；
- 若模型在纠错时持续输出同动作，当前会在二次约束后显式失败，不会静默兜底。

---

## 8) 回滚命令
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver
git checkout -- phone_agent.py qwen_vl_agent.py config.json ui.py
git clean -f tests/test_phase1_functions.py tests/test_phase1_smoke.py
```
