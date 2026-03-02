# PhoneDriver 第三阶段优化报告（异常处理模块）

日期：2026-03-02  
范围：`/home/jiumu/.openclaw/workspace/PhoneDriver`  
原则：最小改动、稳定性优先、禁止硬优化、失败显式暴露

---

## 1) 结论
已完成第三阶段目标，并保持第一/二阶段能力不回退：

1. 新增异常检测模块，识别五类常见 UI 异常：
   - 权限弹窗 `permission_popup`
   - 更新弹窗 `update_popup`
   - 登录引导 `login_guide`
   - 网络错误 `network_error`
   - 验证码/二次验证入口 `captcha_entry`
2. 主流程优先级已接入：
   - **异常处理优先**（安全/阻断弹窗） > 当前 step 主流程 > 普通等待
3. 对验证码/二次验证：
   - 触发 HITL，显式返回 `terminate+failure`，并停止自动盲点重试
4. 可观测性落地：
   - 记录 `exception_type`、`handler_action`、`hitl_triggered`

---

## 2) 实施内容（最小改动）

### A. 异常检测与分类（`phone_agent.py`）
新增：
- `EXCEPTION_KEYWORDS`：异常关键词字典（中英双语）
- `EXCEPTION_PRIORITY`：异常判定优先级（`captcha_entry` 最高）
- `_extract_text_tokens(screenshot_path)`：通过 `uiautomator dump` 拉取 UI XML，提取 `text/content-desc/resource-id` 文本 token（失败仅告警，不静默伪成功）
- `_detect_ui_exception(screenshot_path)`：基于关键词匹配异常类型

### B. 策略选择与执行（`phone_agent.py`）
新增：
- `_select_exception_strategy(exception_type)`：异常→策略映射
- `_handle_detected_exception(screenshot_path)`：异常优先执行入口

策略定义：
- `permission_popup` → `tap_allow_permission`
- `update_popup` → `tap_skip_update`
- `login_guide` → `tap_skip_login_guide`
- `network_error` → `wait_network_recover_<ms>`（可配置 backoff）
- `captcha_entry` → `trigger_hitl_captcha`（`terminate/failure`，显式人工接管）

### C. 主流程优先级接入（`phone_agent.py`）
在 `execute_cycle()` 首部接入：
1) 先截图  
2) 先执行 `_handle_detected_exception()`（若命中，直接 preempt）  
3) 未命中才走原有 `vl_agent.analyze_screenshot()` + `execute_action()`

并在 `_execute_step_cycles()` 中新增：
- 当 `hitl_triggered=True` 时，立即停止自动重试，保存 checkpoint，显式失败返回。

### D. 可观测性（`phone_agent.py`）
新增：
- `_record_exception_event(exception_type, handler_action, hitl_triggered)`
- `context.exception_events[]` 事件流
- `context.last_exception_type / last_handler_action / last_hitl_triggered`

### E. 配置补齐（`config.json` / `ui.py` / `phone_agent.py` 默认值）
新增配置项：
- `enable_exception_handler`（默认 true）
- `hitl_on_captcha`（默认 true）
- `exception_network_backoff_ms`（默认 2000）

---

## 3) 与第一/第二阶段兼容性
本次未回退以下能力：
- 第一阶段：失败反馈闭环（retry feedback loop）、动态重试预算
- 第二阶段：TaskPlanner 分步执行、checkpoint 保存/恢复

兼容措施：
- `_ensure_phase2_runtime_state()` 扩展了 phase3 字段默认初始化，兼容 `object.__new__` 风格单测构造。
- 现有 phase1/phase2 测试全量回归通过。

---

## 4) 验证结果

### 4.1 编译检查（py_compile）
```bash
python3 -m py_compile phone_agent.py qwen_vl_agent.py ui.py tests/test_phase3_functions.py tests/test_phase3_smoke.py
```
结果：✅ 通过

### 4.2 函数级测试（>=2）
```bash
python3 -m unittest -v tests/test_phase3_functions.py
```
覆盖：
1. 异常分类优先级（验证码优先于权限弹窗）
2. 策略选择（验证码进入 HITL / 权限弹窗自动处理）

结果：✅ 2/2 通过

### 4.3 端到端 smoke（带 timeout）
```bash
timeout 120 python3 -m unittest -v tests/test_phase3_smoke.py
```
覆盖路径：
- 第 1 轮命中权限弹窗并处理
- 第 2 轮命中验证码，触发 HITL，显式失败返回并停止盲点

结果：✅ 通过  
说明：日志中的 `HITL triggered...` 与 `Step failed...` 为预期行为（显式暴露，不是误报）。

### 4.4 全量回归（phase1+phase2+phase3）
```bash
timeout 120 python3 -m unittest -v \
  tests/test_phase1_functions.py tests/test_phase1_smoke.py \
  tests/test_phase2_functions.py tests/test_phase2_smoke.py \
  tests/test_phase3_functions.py tests/test_phase3_smoke.py
```
结果：✅ 全通过

---

## 5) 变更文件
- `/home/jiumu/.openclaw/workspace/PhoneDriver/phone_agent.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/config.json`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/ui.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase3_functions.py`（新增）
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_phase3_smoke.py`（新增）
- `/home/jiumu/.openclaw/workspace/PhoneDriver/OPTIMIZE_PHASE3_REPORT_2026-03-02.md`（新增）

---

## 6) 核心 diff 点（摘要）
1. 在 `execute_cycle` 前置异常处理，建立异常优先级拦截。
2. 引入标准化异常策略映射，避免异常分支散落主流程。
3. 对验证码强制 HITL + 显式失败，不继续 blind tap。
4. 增加异常事件可观测字段，便于后续审计和统计。
5. 保持 phase1/phase2 能力不回退，并通过全量回归验证。

---

## 7) 风险与边界
1. **关键词规则误判/漏判风险**：当前是 rule-based，适合常见弹窗；复杂文案可能需要后续补词库。
2. **坐标策略机型差异**：权限/更新/登录引导关闭点采用通用坐标，极端 UI 布局可能点不中；本轮以最小改动优先，不做重型 UI 定位。
3. **UI dump 可用性依赖**：部分 ROM 对 `uiautomator dump` 稳定性一般；当前已做显式告警，失败会退回主流程，不做静默伪成功。

---

## 8) 回滚命令
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver

# 回滚已修改文件
git checkout -- phone_agent.py config.json ui.py

# 删除 phase3 新增测试与报告
git clean -f tests/test_phase3_functions.py tests/test_phase3_smoke.py OPTIMIZE_PHASE3_REPORT_2026-03-02.md
```
