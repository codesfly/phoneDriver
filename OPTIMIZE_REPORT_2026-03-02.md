# PhoneDriver 稳定性/可维护性优化报告（2026-03-02）

## 0) 结论
本轮按“最小改动、禁止功能扩张”完成了 3 个高影响稳定性问题修复，重点覆盖：
- 错误可观测性（ADB 超时与空输入错误显式化）
- 连续任务行为一致性（重试口径改为“连续失败”）
- 远端 API 兼容性（优先解析 `tool_calls`，兼容字符串 arguments）

所有改动已通过：`py_compile` + 函数级测试 + 流程级 smoke（含 timeout）。

---

## 1) 最影响稳定性的 3 个问题（含证据）

### 问题 A：远端 API 返回 `content=""` 时动作丢失，任务直接失败
**影响**：模型其实给了可执行信息（`reasoning_content`/`tool_call`），但 agent 当成空响应，导致循环失败。

**日志证据**（`phone_agent_ui.log`）：
- `line 29/36/43`：`Remote API empty content`，但 `reasoning_content` 内含 `<tool_call>{...}</tool_call>`。
- `line 31/38/45`：随后 `Cycle error: Failed to get action from model`。

**根因**：远端响应兼容性不全，对不同 OpenAI-compatible provider 的 message 结构处理不够稳。

---

### 问题 B：`type` 空文本会触发 ADB 非法参数，且错误语义不清晰
**影响**：`shell input text ""` 直接报错，任务中断；此前错误主要由 ADB 抛出，不够前置可诊断。

**日志证据**（`phone_agent_ui.log`）：
- `line 278`：`Typing:`（空）
- `line 279-283`：`ADB command failed: shell input text ""` + `IllegalArgumentException: Argument expected after "text"`
- `line 297`：`Max retries exceeded`

**根因**：`_execute_type()` 未拦截空白文本，异常在外层才暴露。

---

### 问题 C：重试策略按“总 cycle 数”截断，不按“连续失败”判定
**影响**：连续任务中即便中间成功过，只要运行到较后 cycle 出现一次失败，也可能被错误判定“超重试上限”提前退出，行为不一致。

**日志证据**（`phone_agent_ui.log`）：
- `line 272` 显示已到 `Cycle 8/15`
- `line 296-297`：一次 action fail 后立刻 `Max retries exceeded`

**根因**：`execute_task()` 使用 `if cycles >= max_retries` 判定重试耗尽（按总步数），而非按连续失败次数。

---

## 2) 最小改动优化方案与实施

> 仅做稳定性/可维护性修复，不改业务目标。

### 改动 1：远端 API 兼容性增强（`qwen_vl_agent.py`）
- **位置**：
  - `_generate_action_remote()`（约 `line 368-394`）
  - `_parse_action()`（约 `line 429-438`）
- **内容**：
  1. 优先读取 `message.tool_calls`（结构化函数调用）生成 `<tool_call>`，避免仅依赖 `content`。
  2. `content` 为空时再回退 `reasoning_content`。
  3. 兼容 `tool_call.arguments` 为 JSON 字符串的 provider 形态（先 `json.loads`）。
- **收益**：跨 OpenAI-compatible 网关时动作解析更稳，不再因字段差异直接掉动作。

### 改动 2：错误可观测性与前置校验（`phone_agent.py`）
- **位置**：
  - `_run_adb_command()`（约 `line 191-221`）
  - `_execute_type()`（约 `line 396-419`）
  - 配置默认与清洗（约 `line 32-72`）
- **内容**：
  1. 新增 `adb_command_timeout`（默认 15s），ADB 调用增加 `timeout`，超时抛 `TimeoutError` 并记录 stderr。
  2. ADB 失败时同时记录 stderr/stdout，便于定位。
  3. `type` 动作新增空文本保护：空白直接 `ValueError("Type action has empty text")`，阻断无效 ADB 命令。
  4. 对 `step_delay/max_retries/adb_command_timeout` 做基础 sanitize，防止配置异常导致运行不稳。
- **收益**：失败更早、更清晰、更可审计；避免隐式卡住。

### 改动 3：连续任务重试一致性（`phone_agent.py`）
- **位置**：`execute_task()`（约 `line 528-570`）
- **内容**：
  1. 引入 `consecutive_failures`。
  2. 重试耗尽判定由“总 cycle”改为“连续失败次数 >= max_retries”。
  3. 成功 cycle 会重置 `consecutive_failures`。
- **收益**：连续任务中容错更符合直觉，避免“跑到后面一次失败就提前终止”。

### 配置同步（最小）
- `ui.py` 默认配置新增 `adb_command_timeout`。
- `config.json` 新增 `adb_command_timeout` 字段（便于 UI/运行时一致）。

---

## 3) 可验证测试（含 timeout）

## 3.1 语法编译检查（py_compile）
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver
./.venv/bin/python -m py_compile phone_agent.py qwen_vl_agent.py ui.py
```
**结果**：通过（无输出，无异常）。

## 3.2 本地函数级测试

### Test-1：`type` 空文本拦截 + 文本转义
```bash
./.venv/bin/python - <<'PY'
from phone_agent import PhoneAgent
class NoInitPhoneAgent(PhoneAgent):
    def __init__(self): pass
agent = NoInitPhoneAgent(); agent.config={'screen_width':1080,'screen_height':2400,'step_delay':0,'max_retries':3}; agent.context={'previous_actions':[]}
captured={}; agent._run_adb_command=lambda cmd: captured.setdefault('cmd',cmd)
try:
    agent._execute_type({'action':'type','text':''})
    print('FAIL')
except ValueError as e:
    print('OK_EMPTY', e)
agent._execute_type({'action':'type','text':'TikTok Search'})
print('CMD', captured['cmd'])
PY
```
**结果摘要**：
- `OK_EMPTY Type action has empty text`
- `CMD shell input text "TikTok%sSearch"`

### Test-2：ADB 超时显式暴露
```bash
./.venv/bin/python - <<'PY'
import subprocess, phone_agent
agent = object.__new__(phone_agent.PhoneAgent)
agent.config = {'device_id':'test-device','adb_command_timeout':5}
orig = subprocess.run
subprocess.run = lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd='adb', timeout=5, stderr='mock timeout stderr'))
try:
    try:
        agent._run_adb_command('shell getprop ro.build.version.release')
        print('FAIL')
    except TimeoutError as e:
        print('OK_TIMEOUT', e)
finally:
    subprocess.run = orig
PY
```
**结果摘要**：
- 日志出现 `ADB command timeout (5s)` 与 `Timeout stderr: mock timeout stderr`
- 输出 `OK_TIMEOUT ADB command timed out after 5s: ...`

### Test-3：远端 `tool_calls` + 字符串 arguments 兼容
```bash
./.venv/bin/python - <<'PY'
import requests
from qwen_vl_agent import QwenVLAgent
agent = object.__new__(QwenVLAgent)
agent.api_base_url='https://example.test/v1'; agent.api_key='dummy'; agent.api_model='qwen3.5-plus'; agent.temperature=0.1; agent.max_tokens=128; agent.api_timeout=10
class R:
    status_code=200
    def json(self):
        return {'choices':[{'message':{'content':'','tool_calls':[{'function':{'name':'mobile_use','arguments':'{"action":"system","text":"home"}'}}]}}]}
orig = requests.post; requests.post=lambda *a,**k:R()
try:
    print('ACTION', agent._generate_action_remote([{'role':'system','content':[{'type':'text','text':'x'}]},{'role':'user','content':[{'type':'text','text':'y'}]}]))
finally:
    requests.post=orig
PY
```
**结果摘要**：
- `ACTION {'action': 'system', 'text': 'home'}`

## 3.3 端到端 smoke（流程级，含 timeout）
```bash
timeout 40 ./.venv/bin/python - <<'PY'
from phone_agent import PhoneAgent
agent = object.__new__(PhoneAgent)
agent.config={'max_retries':2,'continuous_min_cycles':4,'continuous_min_minutes':0,'ignore_terminate_for_continuous_tasks':True,'step_delay':0}
agent.context={'previous_actions':[],'current_app':'Home','task_request':'','continuous_task':False,'task_started_at':None,'session_id':'smoke','screenshots':[]}
seq=[{'success':False,'error':'transient-1'},{'success':True,'task_complete':False},{'success':False,'error':'transient-2'},{'success':True,'task_complete':True}]
idx={'i':0}
agent.execute_cycle=lambda req: seq.__getitem__(idx.__setitem__('i',idx['i']+1) or idx['i']-1)
res = PhoneAgent.execute_task(agent, '打开 tiktok 持续刷视频', max_cycles=2)
print(res['success'], res['cycles'], res['task_complete'])
PY
```
**结果**：`True 4 True`（说明连续任务最小 cycle 生效、且“非连续失败”不会误触发重试耗尽）。

---

## 4) 变更清单（文件 / 核心 diff 点 / 风险 / 回滚）

### 文件
1. `/home/jiumu/.openclaw/workspace/PhoneDriver/phone_agent.py`
2. `/home/jiumu/.openclaw/workspace/PhoneDriver/qwen_vl_agent.py`
3. `/home/jiumu/.openclaw/workspace/PhoneDriver/ui.py`
4. `/home/jiumu/.openclaw/workspace/PhoneDriver/config.json`

### 核心 diff 点
- `phone_agent.py`
  - 新增配置清洗（`step_delay/max_retries/adb_command_timeout`）
  - `_run_adb_command` 增加 timeout + stderr/stdout observability
  - `_execute_type` 增加空文本防御
  - `execute_task` 重试改为 `consecutive_failures`
- `qwen_vl_agent.py`
  - `_generate_action_remote`：`tool_calls` 优先，`content/reasoning_content` 回退
  - `_parse_action`：兼容 `arguments` 为 JSON 字符串
- `ui.py` / `config.json`
  - 补齐 `adb_command_timeout` 默认项

### 风险评估
- **低风险**：均为边界处理与失败路径可观测性增强，不改变业务目标与主流程语义。
- **可见行为变化**：
  - 空文本 type 将“更早失败”（从 ADB 层失败前移到参数校验层）。
  - 重试策略从“总步数阈值”改为“连续失败阈值”，可能使任务更耐抖动。

### 回滚方法
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver
git checkout -- phone_agent.py qwen_vl_agent.py ui.py config.json
```

---

## 5) 未掩盖失败与最短修复路径
本轮测试未发现新增失败。若线上仍出现“拿不到动作”：
1. 先看 `phone_agent_ui.log` 是否出现 `Remote API empty content` / `No <tool_call>`。
2. 抓取一次真实响应体（首个 `choices[0].message`）对照 `_generate_action_remote` 分支。
3. 若 provider 返回新字段（如 `output`/`response`），按同样“结构化优先、文本回退”追加最小兼容分支。
