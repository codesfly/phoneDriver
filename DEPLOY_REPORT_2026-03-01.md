# PhoneDriver 部署与验证报告（2026-03-01）

## 结论
- 仓库已成功部署到 `/home/jiumu/.openclaw/workspace/PhoneDriver`，并创建/使用 `.venv`。
- README 建议依赖已安装（含 `transformers` git 源、`pillow`、`gradio`、`qwen_vl_utils`、`requests`），并尽量做了可复现固定。
- 最小可运行验证（模块导入 + CLI 入口行为）已完成。
- 当前主机 **未安装 adb（二进制不存在）**，因此无法进入真机控制流程；该项判定为 **环境前置未满足**，非代码部署失败。

## 已执行命令（关键）
```bash
# 1) 获取代码
cd /home/jiumu/.openclaw/workspace
git clone https://github.com/OminousIndustries/PhoneDriver.git PhoneDriver

# 2) 创建 venv 并安装依赖
cd /home/jiumu/.openclaw/workspace/PhoneDriver
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel

# transformers 固定到当时 HEAD commit
TF_SHA=$(git ls-remote https://github.com/huggingface/transformers.git HEAD | awk '{print $1}')
python -m pip install "git+https://github.com/huggingface/transformers.git@${TF_SHA}"

# README 建议依赖（固定版本）
python -m pip install "pillow==10.4.0" "gradio==5.49.1" "qwen_vl_utils==0.0.14" "requests==2.32.5"

# 运行时关键依赖（项目代码直接 import torch）
python -m pip install "torch==2.8.0"

# 可复现依赖清单
python -m pip freeze > requirements.lock.txt
python -m pip check
```

```bash
# 3) 最小运行验证
source .venv/bin/activate
python - <<'PY'
import phone_agent
import qwen_vl_agent
import ui
print('PROJECT_IMPORT_OK')
PY

# CLI 参数入口检查（无参数时输出 usage）
python phone_agent.py

# CLI 启动烟雾测试（不长时间阻塞）
timeout 25s python phone_agent.py "Open settings"
```

```bash
# 4) adb 检查
command -v adb
adb version
adb devices
```

## 关键版本
- python: `3.12.3`
- pip: `26.0.1`
- transformers: `5.3.0.dev0`
  - direct_url commit: `11b1906d5c0dae39c13270e47cc02c4cde70e548`
- gradio: `5.49.1`
- requests: `2.32.5`
- torch: `2.8.0+cu128`

## 验证结果
1. 模块导入验证：**通过**（`PROJECT_IMPORT_OK`）
2. CLI 入口检查：**通过**（无参数返回 `Usage: python phone_agent.py 'your task here'`）
3. CLI 烟雾测试：**受环境前置限制**
   - 现象：`FileNotFoundError: [Errno 2] No such file or directory: 'adb'`
   - 结论：不是项目安装失败；是宿主机缺少 ADB 工具链。
4. ADB 可用性：**不通过（前置未满足）**
   - `adb_path=NOT_FOUND`

## 产物路径
- 项目目录：`/home/jiumu/.openclaw/workspace/PhoneDriver`
- 锁定依赖：`/home/jiumu/.openclaw/workspace/PhoneDriver/requirements.lock.txt`
- 本报告：`/home/jiumu/.openclaw/workspace/PhoneDriver/DEPLOY_REPORT_2026-03-01.md`
- CLI 烟雾日志：`/tmp/phonedriver_cli_smoke.log`

## 最短修复路径（针对当前阻塞）
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y adb

# 设备连接并授权后验证
adb kill-server && adb start-server
adb devices
```
> 预期 `adb devices` 至少出现一台 `device` 状态的 Android 设备。

## 下一步启动命令
```bash
cd /home/jiumu/.openclaw/workspace/PhoneDriver
source .venv/bin/activate

# Web UI（推荐）
python ui.py
# 然后访问 http://localhost:7860

# CLI
python phone_agent.py "Open the camera app"
```
