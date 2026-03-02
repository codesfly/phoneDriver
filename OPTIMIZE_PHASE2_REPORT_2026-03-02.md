# PhoneDriver 第二阶段优化报告（任务分解 + Checkpoint 恢复）

日期：2026-03-02  
范围：`/home/jiumu/.openclaw/workspace/PhoneDriver`  
原则：最小改动、稳定性优先、禁止硬优化、失败显式暴露

---

## 1) 结论
本轮已完成第二阶段目标，并保持第一阶段能力不回退：

1. 新增 `TaskPlanner`，将长任务拆分为可执行 JSON 子步骤，结构包含 `step_name / instruction / success_criteria`。  
2. 主执行流程已切换为“按 step 推进”，每步独立重试，成功后进入下一步。  
3. 增加 checkpoint 持久化，保存：`current_step_index / step_status / last_action / last_screenshot / timestamp`。  
4. 增加恢复入口：开启配置且存在有效 checkpoint 时，从未完成 step 继续执行。  

---

## 2) 实施内容（最小改动）

### A. 新增任务分解器（`phone_agent.py`）
新增类：`TaskPlanner`
- `build_plan(user_request)`：根据请求切分步骤（中文/英文连接词 + 标点分段）。
- `validate_plan(plan)`：强校验步骤结构，缺字段直接抛错（显式失败）。
- 输出结构示例：

```json
{
  "planner_version": "phase2-v1",
  "task": "打开设置，然后打开蓝牙",
  "steps": [
    {
      "step_name": "Step 1: 打开设置",
      "instruction": "打开设置",
      "success_criteria": "已完成：打开设置，并且界面可继续下一步。"
    }
  ],
  "generated_at": "2026-03-02T19:xx:xx"
}
```

### B. 步骤执行接入（`phone_agent.py`）
新增/重构：
- `_build_step_prompt(...)`
- `_execute_step_cycles(...)`
- `execute_task(...)`（主流程改为 step-level orchestration）

行为变化：
- 任务开始先生成/恢复计划。
- 循环执行当前 step，沿用第一阶段的失败反馈闭环与动态重试预算。
- step 成功：`step_status[idx] = done`，`current_step_index += 1`。
- step 失败：`step_status[idx] = failed`，显式返回失败并保留 checkpoint。

### C. Checkpoint 持久化与恢复（`phone_agent.py`）
新增：
- `_task_fingerprint(...)`（基于任务文本生成稳定 key）
- `_get_checkpoint_path(...)`
- `_save_checkpoint(...)`（原子写入：`.tmp` + `os.replace`）
- `_load_checkpoint(...)`
- `_clear_checkpoint(...)`
- `_prepare_plan_and_recovery(...)`

持久化字段：
- `current_step_index`
- `step_status`
- `last_action`
- `last_screenshot`
- `timestamp`
- （附带）`task_fingerprint / task_request / plan / last_error`

恢复策略：
- 配置 `enable_checkpoint_recovery=true` 且 checkpoint 存在时尝试恢复。
- `task_fingerprint` 不匹配则丢弃旧 checkpoint（防止串任务污染）。
- checkpoint JSON 损坏时直接抛错，显式暴露问题。

### D. 配置最小补齐（`config.json` / `ui.py`）
新增 phase2 配置项：
- `enable_task_planner`
- `planner_max_steps`
- `enable_checkpoint_recovery`
- `checkpoint_dir`

---

## 3) 验证结果

### 3.1 编译检查（py_compile）
```bash
timeout 120 python3 -m py_compile phone_agent.py qwen_vl_agent.py ui.py tests/test_phase2_functions.py tests/test_phase2_smoke.py
```
结果：✅ 通过

### 3.2 函数级测试（>=2）
```bash
timeout 120 python3 -m unittest -v tests/test_phase2_functions.py
```
覆盖：
1. `test_task_planner_json_structure`（任务分解结构有效性）
2. `test_checkpoint_save_and_restore`（checkpoint 保存/恢复）

结果：✅ 2/2 通过

### 3.3 端到端 smoke（中断后恢复继续执行，带 timeout）
```bash
timeout 120 python3 -m unittest -v tests/test_phase2_smoke.py
```
覆盖：
- 第一次运行：step1 成功、step2 模拟中断失败并落 checkpoint
- 第二次运行：自动从 checkpoint 恢复，仅执行剩余 step2，最终完成

结果：✅ 通过  
说明：日志中出现 `✗ Step failed... interrupted` 为测试中模拟中断的预期现象，不是误报。

### 3.4 回归验证第一阶段
```bash
timeout 120 python3 -m unittest -v tests/test_phase1_functions.py tests/test_phase1_smoke.py tests/test_phase2_functions.py tests/test_phase2_smoke.py
```
结果：✅ 全部通过（phase1 未回退）

---

## 4) 变更文件
- `/home/jiumu/.openclaw/workspace/PhoneDriver/phone_agent.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/config.json`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/ui.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase2_functions.py`（新增）
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase2_smoke.py`（新增）

---

## 5) 核心 diff 点（摘要）
1. `TaskPlanner` 内聚到 `phone_agent.py`，不引入新依赖。  
2. `execute_task` 从“单层循环”升级为“任务计划 + 分步执行 + 分步状态机”。  
3. checkpoint 使用原子写，避免部分写入导致假恢复。  
4. 恢复前做 fingerprint 校验与结构校验，坏数据显式失败。  
5. 增加 `_ensure_phase2_runtime_state` 兼容无 `__init__` 的测试构造，避免回归旧测试。

---

## 6) 风险与边界
- 风险1：`TaskPlanner` 为规则拆分（启发式），复杂自然语言可能拆分粒度不完美。  
- 风险2：checkpoint 以“任务文本 hash”作为 key，同文案重复执行会复用同一路径（当前通过“完成即清理 checkpoint”降低影响）。  
- 风险3：恢复依赖计划结构一致性，若手工改 checkpoint 文件可能触发显式异常（符合 debug-first）。

---

## 7) 回滚命令
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver

# 回滚代码与配置
git checkout -- phone_agent.py config.json ui.py

# 删除 phase2 新增测试文件
git clean -f tests/test_phase2_functions.py tests/test_phase2_smoke.py
```

---

## 8) 后续建议（非本次必做）
1. 在真机跑 1 次长链路（3~5 step）回归，观察 `step_status` 与 checkpoint 文件生命周期。  
2. 后续可将 `TaskPlanner` 的拆分规则做可配置化（仅参数化，不引入模型规划依赖）。
