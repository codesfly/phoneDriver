# PhoneDriver

> 本仓库基于上游项目二次开发：<https://github.com/OminousIndustries/PhoneDriver>  
> 当前版本在上游基础上增加了稳定性、可观测性与中文化 Web 控制台能力。

一个基于 Python 的 Android/iOS 自动化 Agent：通过 **Qwen3-VL** 理解手机截图，再用 **ADB**（Android）或 **go-ios + WDA**（iOS）执行动作（tap/swipe/type/system）。

<p align="center">
  <img src="Images/PhoneDriver.png" width="600" alt="Phone Driver Demo">
</p>

---

## 最新更新 (Recent Updates)
- 🔥 **Vision + UI Tree 融合感知**: 提取原生系统 XML/控件树边界信息输入给 Qwen-VL，双重特征校验，彻底解决小图标和密集列表的点击漂移问题。
- ⏩ **智能回放引擎 (Smart Replay)**: 成功执行的任务自动缓存轨迹与页面哈希指纹，重复下发相同指令时完全绕过大模型推理（零 Token、极速点击），当界面发生偏离（如弹窗阻挡）时自动降级唤醒视觉思考。
- 🎨 **全新极客深色控制台**: 重构的左右分栏独立 Web 面板（基于 Gradio），提供极佳的任务可解释性与日志追踪体验，并可在面板直接配置高级功能开关。

---

## 功能特性

### 核心能力
- 🤖 **视觉驱动自动化** — 基于 Qwen3-VL 解析 UI 截图并决策
- 📱 **Android ADB 控制** — 点击、滑动、输入、系统按键
- 🍎 **iOS Bridge（macOS）** — go-ios + WDA 自动化（tunnel/session/截图/操作）
- 🌐 **iOS HTTP API** — 独立 REST 接口，供外部系统调用
- 🎯 **原生 UI 树定位增强** — 融合 UI Tree 数据，强力校对视觉幻觉，点击零偏差
- ⏪ **智能轨迹回放引擎** — 命中历史成功任务后绕过大模型（免 Token/极低延迟），仅在页面偏离时降级重规划

### 智能执行
- 🧭 **失败反馈闭环** — 失败后自动分类原因、重截图、请求修正
- 🌲 **任务规划与分解** — 自动拆分为步骤树，实时跟踪进度
- 🔄 **检查点恢复** — 中断后可从断点继续执行
- 🛡️ **异常自动处理** — 弹窗识别、网络异常重试、验证码人工介入
- 🔁 **连续任务支持** — 可配置最低轮次/时长，防止过早终止

### Web Dashboard（深色主题）
- 🖥️ **三栏 Dashboard** — 任务控制 | 任务规划+日志 | 设备屏幕预览
- 🎨 **深色主题** — 专业 SaaS 风格，teal 渐变主色调
- 📱 **多设备预留** — 顶栏设备选择器，一键刷新状态
- ⚡ **快速截屏** — 默认 `exec-out`，失败自动回退 legacy 路径
- 🩺 **启动健康检测** — 自动检测 ADB/设备/分辨率
- 🧩 **预设任务库** — 一键填充常用场景

---

## 环境要求

- Python 3.10+
- Android 设备（开启开发者模式与 USB/无线调试）
- ADB（Android Debug Bridge）
- 若使用本地模型：建议具备 GPU 显存

---

## 安装步骤

### 1) 安装 ADB

```bash
# Ubuntu
sudo apt update && sudo apt install -y adb

# macOS
brew install android-platform-tools
```

### 2) 克隆仓库并创建虚拟环境

```bash
git clone https://github.com/codesfly/phoneDriver.git
cd phoneDriver
python -m venv .venv
source .venv/bin/activate
```

### 3) 安装依赖

```bash
pip install git+https://github.com/huggingface/transformers
pip install pillow gradio qwen_vl_utils requests torch
```

---

## 快速启动

### Web Dashboard（推荐）

```bash
source .venv/bin/activate
python ui.py
```

打开：`http://localhost:7860`

界面分为三个主要区域：
- **左栏**：任务描述输入、预设选择、开始/停止
- **中栏**：任务规划步骤树 + 实时执行日志
- **右栏**：设备屏幕截图实时预览

底部折叠面板包含：设备与模型设置 | iOS Bridge | 帮助文档

### 命令行模式

```bash
source .venv/bin/activate
python phone_agent.py "打开浏览器并搜索上海天气"
```

### iOS Bridge（macOS）

> 不使用 Appium；仅使用 `go-ios + WDA`。自动 tunnel/runwda/WDA readiness/session 管理。  
> Debug-first：环境未就绪会显式报错，不会"假成功"。

1) 安装并验证 go-ios
```bash
go-ios list
```

2) 配置 `config.json`
```json
{
  "ios_enabled": true,
  "ios_default_udid": "<你的UDID>",
  "ios_go_ios_binary": "go-ios",
  "ios_wda_base_url": "http://127.0.0.1:8100",
  "ios_auto_start_tunnel": true,
  "ios_auto_start_runwda": true,
  "ios_wda_ready_timeout": 40
}
```

3) 启动 Web Dashboard，展开底部 `🍎 iOS Bridge` 面板操作

4) 可选：启动 HTTP API
```bash
python ios_http_api.py --host 127.0.0.1 --port 8787 --config config.json
```

---

## 设备连接

### USB 连接

```bash
adb devices
```

### 无线调试连接

```bash
adb connect <手机IP:端口>
adb devices -l
```

> 提示：无线调试端口会变化，若连接失败请在手机上刷新后使用新端口。

---

## 配置说明（`config.json`）

### 基础配置

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `device_id` | 设备 ID，为空自动探测 | `null` |
| `screen_width` / `screen_height` | 分辨率 | `1080` / `2400` |
| `step_delay` | 动作间隔（秒） | `1.5` |
| `max_retries` | 基础重试上限 | `3` |
| `use_fast_screencap` | 快速截图 | `true` |
| `adb_command_timeout` | ADB 超时秒数 | `15` |

### 远端 API 模式（推荐）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `use_remote_api` | 使用远端模型 | `true` |
| `api_base_url` | OpenAI 兼容接口地址 | `""` |
| `api_key` | API Key | `""` |
| `api_model` | 模型名称 | `qwen3.5-plus` |
| `api_timeout` | API 超时秒数 | `120` |

### 连续任务控制

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `ignore_terminate_for_continuous_tasks` | 忽略早停 | `true` |
| `continuous_min_cycles` | 最小轮次 | `20` |
| `continuous_min_minutes` | 最小时长（分） | `0` |

### 动态重试预算

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable_dynamic_retry_budget` | 启用复杂度重试 | `true` |
| `retry_budget_simple` / `medium` / `complex` | 各级预算 | `2` / `4` / `6` |
| `retry_budget_cap` | 预算上限 | `8` |

### iOS Bridge 配置（macOS）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `ios_enabled` | 启用 iOS bridge | `false` |
| `ios_default_udid` | 默认 UDID | `""` |
| `ios_go_ios_binary` | go-ios 路径 | `go-ios` |
| `ios_wda_base_url` | WDA 地址 | `http://127.0.0.1:8100` |
| `ios_auto_start_tunnel` | 自动拉起 tunnel | `true` |
| `ios_auto_start_runwda` | 自动拉起 runwda | `true` |
| `ios_wda_ready_timeout` | WDA 就绪超时 | `40` |

---

## 工作机制

```
截图 → Qwen3-VL 视觉分析 → 决策动作 → ADB/WDA 执行 → 检查结果 → 循环
                                  ↓ 失败
                          分类原因 → 修正策略 → 重试
```

1. **截图**：ADB/WDA 获取当前屏幕
2. **视觉分析**：Qwen3-VL 识别界面并给出动作
3. **执行动作**：tap / swipe / type / wait / system
4. **失败闭环**：自动分类原因并请求修正动作
5. **循环执行**：直到完成、到达预算、或用户停止

---

## 故障排查

| 问题 | 解决方案 |
|------|----------|
| 设备未连接 | `adb kill-server && adb start-server && adb devices -l` |
| 点击位置不准 | `adb shell wm size`，同步更新 config 分辨率 |
| 模型无动作返回 | 查看 `phone_agent_ui.log`，检查 API 连通性 |
| 任务过早结束 | 提高 `continuous_min_cycles` 或 `continuous_min_minutes` |

---

## 测试

```bash
source .venv/bin/activate
# 编译检查
python -m py_compile phone_agent.py qwen_vl_agent.py ui.py ios_bridge.py ios_service.py ios_http_api.py

# 运行全部测试
PYTHONPATH=. python -m pytest tests/ -v
```

---

## 项目结构

```
phoneDriver/
├── phone_agent.py       # 核心 Agent（ADB 控制、VLM 交互、任务执行）
├── qwen_vl_agent.py     # Qwen3-VL 模型接口（本地/远端 API）
├── ui.py                # Web Dashboard（深色主题 Gradio）
├── ios_bridge.py        # iOS go-ios 底层桥接
├── ios_service.py       # iOS 服务层（prepare/health/action）
├── ios_http_api.py      # iOS REST API 服务
├── config.json          # 运行时配置
├── tests/               # 单元测试与冒烟测试
└── Images/              # 文档图片
```

---

## 提交与文档约定

- 默认使用 **中文 commit message**
- 默认使用 **中文 README/文档**（必要技术术语保留英文）
- 变更提交前至少保证：`py_compile` 通过 + 测试通过

---

## 合规与风险提示

- 移动端自动化可能违反目标平台的 **Terms of Service (TOS)**。
- 本项目仅建议用于：
  - 个人自有设备测试
  - 合法授权场景
  - 合规研发验证
- 禁止用于：绕过风控/反作弊、设备伪装、未授权批量操作
- 若平台策略与本项目能力存在冲突，以平台条款和当地法律法规为准。

## License

Apache License 2.0（见 `LICENSE`）

## 致谢

- 上游项目：[OminousIndustries/PhoneDriver](https://github.com/OminousIndustries/PhoneDriver)
- [Qwen3-VL](https://github.com/QwenLM/Qwen-VL)
- [Gradio](https://gradio.app/)
