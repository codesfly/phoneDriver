# PhoneDriver P1 优化报告（2026-03-02）

## 结论
已按“稳定性优先、最小改动、禁止硬优化”完成 P1 范围内优化：

1. Gradio 新增**任务树 / 规划步骤**可视化面板，展示 phase2 steps（`step_name/instruction/success_criteria`）与实时状态。  
2. Gradio 新增**预设任务**下拉（中文），支持一键填充任务输入框。  
3. 帮助页与 README 完成中文一致化补充，新增“任务树如何理解 / 预设任务如何使用”。

未引入实时视频流，未新增重依赖，未改动 phase1/2/3/P0 的执行核心语义。

---

## 变更文件
- `/home/jiumu/.openclaw/workspace/PhoneDriver/ui.py`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/README.md`
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_p1_functions.py`（新增）
- `/home/jiumu/.openclaw/workspace/PhoneDriver/tests/test_p1_ui_smoke.py`（新增）

---

## 核心 Diff 点

### 1) 任务树可视化（UI + 实时刷新）
在 `ui.py` 新增：
- `format_task_tree_markdown(plan, step_status, current_step_index)`：将 phase2 计划与状态渲染为 Markdown。
- `_normalize_step_status()`：兼容 `in_progress -> running` 映射，状态统一为 `pending/running/done/failed`。
- `task_tree_output` 组件（`elem_id="task-tree-panel"`）挂到任务页右侧。
- `update_ui()` 输出扩展为 4 项：截图、日志、timer、任务树文本；执行过程中按 `agent.current_step_index / agent.step_status` 实时刷新。
- `start_task()` 启动后立即显示“任务已启动，正在生成规划步骤...”，失败场景回退到空任务树提示，错误显式暴露。

### 2) 预设任务库（中文）
在 `ui.py` 新增：
- `PRESET_TASK_LIBRARY`（中文合规通用预设）
- `apply_preset_task(preset_name, current_text)`：下拉变更后直接填充任务输入框，并返回状态文案。
- 新增 UI 组件：`预设任务` 下拉 + `预设状态` 文本框。

预设示例：
- 打开设置检查网络
- 打开浏览器搜索天气
- 进入应用并停留
- 打开设置检查蓝牙

### 3) 文案与帮助页
- `ui.py` 帮助 Tab 新增：
  - 任务树如何理解（字段含义 + 状态图例）
  - 预设任务如何使用（填充行为说明）
- `README.md` 新增 P1 特性描述与专门章节，测试命令补充 P1 测试项。

---

## 验证记录

### A. 编译检查
```bash
timeout 120 .venv/bin/python -m py_compile phone_agent.py qwen_vl_agent.py ui.py
```
结果：通过。

### B. 函数级测试（至少 2 个）
```bash
timeout 90 env PYTHONPATH=. .venv/bin/python tests/test_p1_functions.py
```
结果：通过（2/2）
- `test_task_tree_markdown_format`
- `test_apply_preset_task_fill`

### C. UI smoke（包含任务树组件，带 timeout）
```bash
timeout 90 env PYTHONPATH=. .venv/bin/python tests/test_p1_ui_smoke.py
```
结果：通过（1/1）
- 校验 `task-tree-panel` 存在
- 校验“预设任务”下拉存在

### D. 回归抽样
```bash
timeout 180 env PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```
结果：17 项通过（phase1/2/3 + P0 + P1）。

注：存在 Gradio/asyncio `ResourceWarning: unclosed event loop`（既有测试环境现象，不影响断言通过）。

---

## 风险评估
1. **状态展示与执行状态短暂错位**：UI 轮询周期（3s）内，任务树显示可能滞后 1 个 tick；不影响执行语义。  
2. **Markdown 展示长度增长**：步骤较多时文本较长；当前 `planner_max_steps` 默认 8，风险可控。  
3. **预设覆盖输入行为**：选择预设会覆盖输入框文本；已通过“（不使用预设）保留当前输入”降低误操作风险。

---

## 回滚命令
在仓库目录执行：

```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver
git checkout -- ui.py README.md
git clean -f tests/test_p1_functions.py tests/test_p1_ui_smoke.py
```

如已提交，亦可使用：
```bash
git revert <P1提交的commit_sha>
```

---

## 约束符合性说明
- ✅ 未做实时视频流（P2 范围外）
- ✅ 未引入重依赖
- ✅ 未改执行核心语义（phase1/2/3/P0 不回退）
- ✅ 失败显式暴露（输入/配置异常、未知预设、步骤失败状态均可见）
