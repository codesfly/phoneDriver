import os
import json
import logging
import subprocess
from pathlib import Path
from threading import Thread
from datetime import datetime
from typing import Any, Dict, Optional
import gradio as gr

from phone_agent import PhoneAgent, parse_adb_devices_output, parse_wm_size_output


class UILogHandler(logging.Handler):
    """Custom logging handler that stores logs for UI display."""
    
    def __init__(self):
        super().__init__()
        self.logs = []
    
    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]


# Global state
current_screenshot = None
log_handler = None
is_running = False
agent = None
current_config = None
last_health_result = None

TOS_NOTICE_TEXT = (
    "⚠️ 合规提醒：移动端自动化可能违反平台 TOS，仅限个人设备测试与合规场景。"
)


def load_config(config_path="config.json"):
    """Load configuration from file."""
    if not os.path.exists(config_path):
        return get_default_config()
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        default = get_default_config()
        for key, value in default.items():
            if key not in config:
                config[key] = value
        return config
    except json.JSONDecodeError:
        return get_default_config()


def get_default_config():
    """Get default configuration."""
    return {
        "device_id": None,
        "screen_width": 1080,
        "screen_height": 2340,
        "screenshot_dir": "./screenshots",
        "max_retries": 3,
        "use_fast_screencap": True,
        "runtime_config_path": "config.json",
        "model_name": "Qwen/Qwen3-VL-30B-A3B-Instruct",
        "use_flash_attention": False,
        "temperature": 0.1,
        "max_tokens": 512,
        "step_delay": 1.5,
        "enable_visual_debug": False,
        "use_remote_api": False,
        "api_base_url": "",
        "api_key": "",
        "api_model": "qwen3.5-plus",
        "api_timeout": 120,
        "adb_command_timeout": 15,
        "ignore_terminate_for_continuous_tasks": True,
        "continuous_min_cycles": 20,
        "continuous_min_minutes": 0,
        "enable_dynamic_retry_budget": True,
        "retry_budget_simple": 2,
        "retry_budget_medium": 4,
        "retry_budget_complex": 6,
        "retry_budget_cap": 8,
        "enable_task_planner": True,
        "planner_max_steps": 8,
        "enable_checkpoint_recovery": True,
        "checkpoint_dir": "./checkpoints",
        "enable_exception_handler": True,
        "hitl_on_captcha": True,
        "exception_network_backoff_ms": 2000
    }


def save_config(config, config_path="config.json"):
    """Save configuration to file."""
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"保存配置失败: {e}")
        return False


def setup_logging():
    """Configure logging for the UI."""
    global log_handler
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    log_handler = UILogHandler()
    log_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    log_handler.setFormatter(formatter)
    root_logger.addHandler(log_handler)

    file_handler = logging.FileHandler("phone_agent_ui.log")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def persist_runtime_resolution(width: int, height: int, config_path: str = "config.json") -> bool:
    """Persist only runtime resolution fields to config file (avoid sensitive field writes)."""
    payload = {}
    try:
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                payload = json.load(f)
    except Exception as e:
        logging.error(f"写入分辨率前读取配置失败: {e}")
        return False

    payload['screen_width'] = int(width)
    payload['screen_height'] = int(height)

    try:
        with open(config_path, 'w') as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"写入运行时分辨率失败: {e}")
        return False


def run_health_check(config: Optional[Dict[str, Any]] = None, persist_runtime_config: bool = True):
    """Run ADB/device/resolution health check for UI startup and manual refresh."""
    global current_config, last_health_result

    cfg = dict(config or current_config or load_config())
    timeout_s = max(3, int(cfg.get("adb_command_timeout", 15)))

    result: Dict[str, Any] = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "adb_available": False,
        "device_connected": False,
        "device_id": None,
        "screen_width": None,
        "screen_height": None,
        "status": "failed",
        "errors": [],
    }

    try:
        version = subprocess.run(
            ["adb", "version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if version.stdout.strip() or version.stderr.strip():
            result["adb_available"] = True
    except Exception as e:
        result["errors"].append(f"ADB 不可用: {e}")
        last_health_result = result
        return result

    try:
        devices_raw = subprocess.run(
            ["adb", "devices"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        devices = parse_adb_devices_output(devices_raw.stdout)
    except Exception as e:
        result["errors"].append(f"获取设备列表失败: {e}")
        last_health_result = result
        return result

    requested_device = str(cfg.get("device_id") or "").strip()
    selected_device = None

    if requested_device:
        for dev_id, state in devices:
            if dev_id == requested_device and state == "device":
                selected_device = dev_id
                break
        if not selected_device:
            result["errors"].append(f"配置的设备未就绪: {requested_device}")
    else:
        for dev_id, state in devices:
            if state == "device":
                selected_device = dev_id
                break

    if not selected_device:
        if not result["errors"]:
            result["errors"].append("未检测到已授权设备")
        last_health_result = result
        return result

    result["device_connected"] = True
    result["device_id"] = selected_device

    size_cmd = ["adb", "-s", selected_device, "shell", "wm", "size"]
    try:
        size_raw = subprocess.run(
            size_cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        parsed = parse_wm_size_output(size_raw.stdout)
        if not parsed:
            result["errors"].append(f"无法解析分辨率输出: {size_raw.stdout.strip()}")
        else:
            width, height = parsed
            result["screen_width"] = width
            result["screen_height"] = height
            cfg["screen_width"] = width
            cfg["screen_height"] = height
            cfg["device_id"] = selected_device
            if persist_runtime_config:
                persist_runtime_resolution(
                    width,
                    height,
                    cfg.get("runtime_config_path", "config.json"),
                )
    except Exception as e:
        result["errors"].append(f"分辨率探测失败: {e}")

    result["status"] = "ok" if (
        result["adb_available"]
        and result["device_connected"]
        and result.get("screen_width")
        and result.get("screen_height")
        and not result["errors"]
    ) else "failed"

    current_config = cfg
    last_health_result = result
    return result


def format_health_result(result: Dict[str, Any]) -> str:
    if not result:
        return "⚠️ 健康检查尚未执行"
    if result.get("status") == "ok":
        return (
            f"✓ ADB 正常 | 设备: {result.get('device_id')} | "
            f"分辨率: {result.get('screen_width')} x {result.get('screen_height')}"
        )
    return f"✗ 健康检查失败: {'; '.join(result.get('errors', []))}"


def detect_device_resolution():
    """Compatibility wrapper for existing UI event binding."""
    result = run_health_check(current_config, persist_runtime_config=True)
    if result.get("status") == "ok":
        return result.get("screen_width"), result.get("screen_height"), format_health_result(result)
    return None, None, format_health_result(result)


def execute_task_thread(task, max_cycles, config):
    """Run task in background thread."""
    global current_screenshot, is_running, agent
    
    if log_handler:
        log_handler.logs.clear()
    
    is_running = True
    
    try:
        logging.info(f"开始执行任务: {task}")
        
        # Only create agent if it doesn't exist
        if agent is None:
            logging.info("首次初始化 PhoneAgent...")
            agent = PhoneAgent(config)
        else:
            logging.info("复用已有 Agent 实例...")
            # Reset context for new task
            from datetime import datetime
            agent.context['previous_actions'] = []
            agent.context['task_request'] = task
            agent.context['session_id'] = datetime.now().strftime("%Y%m%d_%H%M%S")
            agent.context['screenshots'] = []
        
        # Monkey-patch to capture screenshots
        original_capture = agent.capture_screenshot
        def capture_with_tracking():
            path = original_capture()
            global current_screenshot
            current_screenshot = path
            return path
        
        agent.capture_screenshot = capture_with_tracking
        
        # Execute task
        result = agent.execute_task(task, max_cycles=max_cycles)
        
        if result['success']:
            logging.info(f"✓ 任务完成，总轮次 {result['cycles']}")
        else:
            logging.info(f"⚠️ 任务未完成，已执行 {result['cycles']} 轮")
            
    except KeyboardInterrupt:
        logging.info("任务被用户中断")
    except Exception as e:
        logging.error(f"任务执行异常: {e}", exc_info=True)
    finally:
        is_running = False


def start_task(task, max_cycles, config_json):
    """Start a task execution."""
    global is_running, current_config
    
    if is_running:
        return (
            "⚠️ 当前已有任务在运行",
            None,
            gr.update(active=False)
        )
    
    if not task.strip():
        return (
            "✗ 请输入任务描述",
            None,
            gr.update(active=False)
        )
    
    try:
        config = json.loads(config_json)
        current_config = config
    except json.JSONDecodeError as e:
        return (
            f"✗ 配置 JSON 无效: {e}",
            None,
            gr.update(active=False)
        )
    
    try:
        max_cycles = int(max_cycles)
        if max_cycles < 1:
            max_cycles = 15
    except ValueError:
        max_cycles = 15
    
    thread = Thread(target=execute_task_thread, args=(task, max_cycles, config))
    thread.daemon = True
    thread.start()
    
    return (
        "✓ 任务已启动...",
        None,
        gr.update(active=True)
    )


def update_ui():
    """Update UI with latest screenshot and logs."""
    global current_screenshot, log_handler, is_running
    
    screenshot = None
    if current_screenshot and os.path.exists(current_screenshot):
        screenshot = current_screenshot
    
    logs = "\n".join(log_handler.logs) if log_handler else ""
    
    timer_state = gr.update(active=is_running)
    
    return (screenshot, logs, timer_state)


def stop_task():
    """Stop the currently running task."""
    global is_running, agent
    if is_running:
        logging.warning("收到用户停止任务请求")
        try:
            if agent is not None:
                agent.context['stop_requested'] = True
        except Exception:
            pass
        is_running = False
        return "⚠️ 正在停止任务..."
    return "当前没有运行中的任务"


def apply_settings(
    screen_width,
    screen_height,
    temp,
    max_tok,
    step_delay,
    use_fa2,
    visual_debug,
    use_fast_screencap,
    ignore_terminate_continuous,
    continuous_min_cycles,
    continuous_min_minutes,
):
    """Apply settings changes to config."""
    global current_config
    
    try:
        config = current_config or load_config()
        
        config['screen_width'] = int(screen_width)
        config['screen_height'] = int(screen_height)
        config['temperature'] = float(temp)
        config['max_tokens'] = int(max_tok)
        config['step_delay'] = float(step_delay)
        config['use_flash_attention'] = use_fa2
        config['enable_visual_debug'] = visual_debug
        config['use_fast_screencap'] = bool(use_fast_screencap)

        config['ignore_terminate_for_continuous_tasks'] = bool(ignore_terminate_continuous)
        config['continuous_min_cycles'] = max(1, int(continuous_min_cycles))
        config['continuous_min_minutes'] = max(0.0, float(continuous_min_minutes))
        
        if save_config(config, config.get("runtime_config_path", "config.json")):
            current_config = config
            return "✓ 设置已保存", json.dumps(config, indent=2, ensure_ascii=False)
        else:
            return "✗ 保存设置失败", json.dumps(config, indent=2, ensure_ascii=False)
            
    except ValueError as e:
        return f"✗ 参数值无效: {e}", json.dumps(current_config or {}, indent=2, ensure_ascii=False)


def auto_detect_resolution():
    """Auto-detect device resolution."""
    width, height, message = detect_device_resolution()

    if width and height:
        return width, height, message
    else:
        fallback_cfg = current_config or get_default_config()
        return fallback_cfg.get("screen_width", 1080), fallback_cfg.get("screen_height", 2340), message


def refresh_health_status_ui():
    """Refresh startup health status and sync detected resolution to config/UI."""
    result = run_health_check(current_config, persist_runtime_config=True)
    cfg = current_config or get_default_config()
    width = cfg.get("screen_width", 1080)
    height = cfg.get("screen_height", 2340)
    return (
        format_health_result(result),
        width,
        height,
        json.dumps(cfg, indent=2, ensure_ascii=False),
    )


def clear_logs_fn():
    """Clear the log display."""
    if log_handler:
        log_handler.logs.clear()
    return ""


def create_ui():
    """Create the Gradio interface."""
    global current_config, last_health_result
    current_config = load_config()

    startup_health = run_health_check(current_config, persist_runtime_config=True)
    last_health_result = startup_health
    current_config = current_config or load_config()

    Path(current_config['screenshot_dir']).mkdir(parents=True, exist_ok=True)

    with gr.Blocks(title="PhoneDriver 控制台", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 📱 PhoneDriver 控制台")
        gr.Markdown("*基于 Qwen3-VL 的移动端自动化（Web 控制台）*")
        gr.Markdown(f"**{TOS_NOTICE_TEXT}**")
        
        with gr.Tabs():
            with gr.Tab("🎯 任务控制"):
                with gr.Row():
                    with gr.Column(scale=2):
                        task_input = gr.Textbox(
                            label="任务描述",
                            placeholder="例如：打开浏览器并搜索上海天气",
                            lines=3
                        )
                        
                        with gr.Row():
                            max_cycles = gr.Number(
                                label="最大轮次",
                                value=15,
                                minimum=1,
                                maximum=50
                            )
                            start_btn = gr.Button("▶️ 开始任务", variant="primary", scale=2)
                            stop_btn = gr.Button("⏹️ 停止", variant="stop", scale=1)
                        
                        status_text = gr.Textbox(label="状态", lines=2, interactive=False)
                    
                    with gr.Column(scale=3):
                        image_output = gr.Image(
                            label="当前屏幕",
                            type="filepath",
                            height=600
                        )
                
                log_output = gr.Textbox(
                    label="📋 执行日志",
                    lines=15,
                    max_lines=20,
                    interactive=False,
                    show_copy_button=True
                )
                
                with gr.Row():
                    refresh_btn = gr.Button("🔄 刷新显示")
                    clear_logs_btn = gr.Button("🗑️ 清空日志")
            
            with gr.Tab("⚙️ 设置"):
                gr.Markdown("### 设备配置")

                health_status = gr.Textbox(
                    label="启动健康检查（ADB + 设备 + 分辨率）",
                    value=format_health_result(startup_health),
                    interactive=False,
                )

                with gr.Row():
                    with gr.Column():
                        detect_btn = gr.Button("🔍 自动检测设备分辨率")
                        refresh_health_btn = gr.Button("♻️ 刷新健康检查")
                        detect_status = gr.Textbox(label="检测状态", interactive=False)

                    with gr.Column():
                        screen_width = gr.Number(
                            label="屏幕宽度（像素）",
                            value=current_config['screen_width']
                        )
                        screen_height = gr.Number(
                            label="屏幕高度（像素）",
                            value=current_config['screen_height']
                        )
                
                gr.Markdown("### 模型参数")
                
                with gr.Row():
                    temperature = gr.Slider(
                        label="温度（Temperature）",
                        minimum=0.0,
                        maximum=1.0,
                        value=current_config['temperature'],
                        step=0.05
                    )
                    max_tokens = gr.Number(
                        label="最大 Tokens",
                        value=current_config['max_tokens'],
                        minimum=128,
                        maximum=2048
                    )
                
                with gr.Row():
                    step_delay = gr.Slider(
                        label="动作间隔（秒）",
                        minimum=0.5,
                        maximum=5.0,
                        value=current_config['step_delay'],
                        step=0.1
                    )
                
                gr.Markdown("### 高级选项")
                
                with gr.Row():
                    use_flash_attn = gr.Checkbox(
                        label="启用 Flash Attention 2（可选）",
                        value=current_config.get('use_flash_attention', False)
                    )
                    visual_debug = gr.Checkbox(
                        label="启用可视化调试",
                        value=current_config.get('enable_visual_debug', False)
                    )
                    use_fast_screencap = gr.Checkbox(
                        label="启用快速截图（adb exec-out）",
                        value=current_config.get('use_fast_screencap', True)
                    )

                gr.Markdown("### 连续任务（刷一会/持续任务）")
                with gr.Row():
                    ignore_terminate_continuous = gr.Checkbox(
                        label="连续任务忽略过早 terminate",
                        value=current_config.get('ignore_terminate_for_continuous_tasks', True)
                    )
                    continuous_min_cycles = gr.Number(
                        label="连续任务最小轮次",
                        value=current_config.get('continuous_min_cycles', 20),
                        minimum=1,
                        maximum=500
                    )
                    continuous_min_minutes = gr.Number(
                        label="连续任务最小时长（分钟）",
                        value=current_config.get('continuous_min_minutes', 0),
                        minimum=0,
                        maximum=600
                    )
                
                apply_btn = gr.Button("💾 保存设置", variant="primary")
                settings_status = gr.Textbox(label="设置状态", interactive=False)
                
                gr.Markdown("### 配置 JSON")
                config_editor = gr.Code(
                    label="当前配置",
                    language="json",
                    value=json.dumps(current_config, indent=2, ensure_ascii=False),
                    lines=15
                )
            
            with gr.Tab("❓ 帮助"):
                gr.Markdown("""
## 快速开始

1. **连接设备**：开启 USB 调试，连接设备
2. **配置分辨率**：在“设置”页点击自动检测
3. **运行任务**：输入任务描述并点击“开始任务”

## 任务示例
- “打开浏览器”
- “搜索上海天气”
- “打开设置并启用无线网络”

## 故障排查
- **点击不准**：检查设置里的分辨率
- **找不到设备**：在终端执行 `adb devices`
- **执行报错**：查看“执行日志”
                """)
        
        timer = gr.Timer(value=3, active=False)
        
        # Event handlers
        start_btn.click(
            fn=start_task,
            inputs=[task_input, max_cycles, config_editor],
            outputs=[status_text, image_output, timer]
        )
        
        stop_btn.click(
            fn=stop_task,
            outputs=status_text
        )
        
        timer.tick(
            fn=update_ui,
            outputs=[image_output, log_output, timer]
        )
        
        refresh_btn.click(
            fn=update_ui,
            outputs=[image_output, log_output, timer]
        )
        
        clear_logs_btn.click(
            fn=clear_logs_fn,
            outputs=log_output
        )
        
        detect_btn.click(
            fn=auto_detect_resolution,
            outputs=[screen_width, screen_height, detect_status]
        )
        
        refresh_health_btn.click(
            fn=refresh_health_status_ui,
            outputs=[health_status, screen_width, screen_height, config_editor]
        )

        apply_btn.click(
            fn=apply_settings,
            inputs=[
                screen_width,
                screen_height,
                temperature,
                max_tokens,
                step_delay,
                use_flash_attn,
                visual_debug,
                use_fast_screencap,
                ignore_terminate_continuous,
                continuous_min_cycles,
                continuous_min_minutes,
            ],
            outputs=[settings_status, config_editor]
        )
    
    return demo


def main():
    """Main entry point for the UI."""
    print("PhoneDriver UI 启动中...")
    print(TOS_NOTICE_TEXT)
    print("正在初始化日志...")
    setup_logging()
    logging.warning(TOS_NOTICE_TEXT)

    print("正在创建界面...")
    demo = create_ui()
    
    print("服务已启动：http://localhost:7860")
    print("按 Ctrl+C 停止")
    
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True
    )


if __name__ == "__main__":
    main()
