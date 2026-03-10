import os
import json
import logging
import subprocess
from pathlib import Path
from threading import Thread
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import gradio as gr

from phone_agent import PhoneAgent, parse_adb_devices_output, parse_wm_size_output
from ios_service import IOSBridgeService, IOSServiceError


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

STEP_STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "done": "已完成",
    "failed": "失败",
}

STEP_STATUS_PROGRESS = {
    "pending": "⬜",
    "running": "🟡",
    "done": "🟢",
    "failed": "🔴",
}

PRESET_TASK_LIBRARY = {
    "（不使用预设）": "",
    "打开设置检查网络": "打开设置，然后进入网络与互联网页面，确认 Wi‑Fi 或移动网络可用",
    "打开浏览器搜索天气": "打开浏览器，搜索上海天气，并停留在搜索结果页",
    "进入应用并停留": "打开指定应用，进入首页后停留 10 秒，确认页面稳定",
    "打开设置检查蓝牙": "打开设置，进入蓝牙页面，确认蓝牙开关状态",
}


def _normalize_step_status(raw_status: str) -> str:
    status = str(raw_status or "").strip().lower()
    if status in {"in_progress", "running"}:
        return "running"
    if status in {"done", "pending", "failed"}:
        return status
    return "pending"


def format_task_tree_markdown(
    plan: Optional[Dict[str, Any]],
    step_status: Optional[Dict[str, Any]],
    current_step_index: Optional[int],
) -> str:
    """Build markdown view for phase2 task tree.

    Args:
        plan: task plan from PhoneAgent.execute_task / context['task_plan']
        step_status: step status dictionary
        current_step_index: current active step index
    """
    if not plan or not isinstance(plan, dict):
        return "### 🌲 任务树 / 规划步骤\n\n当前尚无任务规划。点击“开始任务”后会自动生成。"

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return "### 🌲 任务树 / 规划步骤\n\n当前任务没有可展示的步骤。"

    try:
        active_index = int(current_step_index or 0)
    except Exception:
        active_index = 0

    lines: List[str] = ["### 🌲 任务树 / 规划步骤", ""]
    lines.append(f"- 总步骤数：{len(steps)}")
    lines.append(f"- 当前步骤索引：{active_index + 1}")
    lines.append("")

    status_map = step_status if isinstance(step_status, dict) else {}

    for idx, step in enumerate(steps):
        item = step if isinstance(step, dict) else {}
        step_name = str(item.get("step_name", f"Step {idx + 1}")).strip() or f"Step {idx + 1}"
        instruction = str(item.get("instruction", "")).strip() or "（无）"
        success = str(item.get("success_criteria", "")).strip() or "（无）"

        status = _normalize_step_status(status_map.get(str(idx), "pending"))
        if status == "pending" and idx == active_index:
            status = "running"
        status_label = STEP_STATUS_LABELS.get(status, "待执行")
        status_icon = STEP_STATUS_PROGRESS.get(status, "⬜")

        lines.append(f"{idx + 1}. {status_icon} **{step_name}**  `[{status_label}]`")
        lines.append(f"   - instruction: {instruction}")
        lines.append(f"   - success_criteria: {success}")

    return "\n".join(lines)


def apply_preset_task(preset_name: str, current_text: str) -> Tuple[str, str]:
    """Return task textbox content + status message for preset selection."""
    selected = str(preset_name or "").strip()
    if selected not in PRESET_TASK_LIBRARY:
        return current_text or "", "⚠️ 未识别的预设任务"

    preset_text = PRESET_TASK_LIBRARY[selected]
    if not preset_text:
        return current_text or "", "已取消预设，保留当前输入"

    return preset_text, f"✓ 已应用预设任务：{selected}"


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
        "use_remote_api": True,
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
        "exception_network_backoff_ms": 2000,
        "platform": "android",
        "ios_enabled": False,
        "ios_default_udid": "",
        "ios_go_ios_binary": "go-ios",
        "ios_wda_base_url": "http://127.0.0.1:8100",
        "ios_command_timeout": 20,
        "ios_auto_start_tunnel": True,
        "ios_auto_start_runwda": True,
        "ios_wda_ready_timeout": 40,
        "ios_wda_ready_interval": 1.5,
        "ios_health_check_ensure_session": True,
        "ios_logs_dir": "./logs",
        "ios_tunnel_command": "",
        "ios_runwda_command": ""
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

    waiting_tree = "### 🌲 任务树 / 规划步骤\n\n任务已启动，正在生成规划步骤..."

    if is_running:
        return (
            "⚠️ 当前已有任务在运行",
            None,
            gr.update(active=False),
            waiting_tree,
        )

    if not task.strip():
        return (
            "✗ 请输入任务描述",
            None,
            gr.update(active=False),
            format_task_tree_markdown(None, None, None),
        )

    try:
        config = json.loads(config_json)
        current_config = config
    except json.JSONDecodeError as e:
        return (
            f"✗ 配置 JSON 无效: {e}",
            None,
            gr.update(active=False),
            format_task_tree_markdown(None, None, None),
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
        gr.update(active=True),
        waiting_tree,
    )


def update_ui():
    """Update UI with latest screenshot, logs, and task tree."""
    global current_screenshot, log_handler, is_running, agent

    screenshot = None
    if current_screenshot and os.path.exists(current_screenshot):
        screenshot = current_screenshot

    logs = "\n".join(log_handler.logs) if log_handler else ""

    plan = None
    step_status = None
    current_step_index = None
    if agent is not None:
        try:
            plan = getattr(agent, "current_plan", None) or agent.context.get("task_plan")
            step_status = getattr(agent, "step_status", None)
            current_step_index = getattr(agent, "current_step_index", 0)
        except Exception as e:
            logging.error(f"刷新任务树失败: {e}")

    task_tree_markdown = format_task_tree_markdown(plan, step_status, current_step_index)
    timer_state = gr.update(active=is_running)

    return (screenshot, logs, timer_state, task_tree_markdown)


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


def _ios_service_from_config(cfg: Optional[Dict[str, Any]] = None) -> IOSBridgeService:
    return IOSBridgeService.from_config(cfg or current_config or load_config())


def ios_discover_devices_ui(config_json: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("discover", {})
        return json.dumps(result.get("devices", []), indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS 设备发现失败: {e}"


def ios_prepare_ui(config_json: str, udid: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("prepare", {"udid": udid or None, "ensure_session": True})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS 准备失败: {e}"


def ios_health_check_ui(config_json: str, udid: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("health", {"udid": udid or None})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS 健康检查失败: {e}"


def ios_screenshot_ui(config_json: str, udid: str):
    global current_screenshot
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("screenshot", {"udid": udid or None})
        shot = str(result.get("path") or "")
        if not shot:
            return None, "✗ iOS 截图失败: 服务未返回截图路径"
        current_screenshot = shot
        return shot, f"✓ iOS 截图成功: {shot}"
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return None, f"✗ iOS 截图失败: {e}"


def ios_tap_ui(config_json: str, udid: str, x: int, y: int):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("tap", {"udid": udid or None, "x": int(x), "y": int(y)})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS tap 失败: {e}"


def ios_swipe_ui(config_json: str, udid: str, x1: int, y1: int, x2: int, y2: int, duration: float):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call(
            "swipe",
            {
                "udid": udid or None,
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration": float(duration),
            },
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS swipe 失败: {e}"


def ios_type_ui(config_json: str, udid: str, text: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("type", {"udid": udid or None, "text": text})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS type 失败: {e}"


def ios_source_ui(config_json: str, udid: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("source", {"udid": udid or None})
        return result.get("source", "")
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS source 失败: {e}"


def ios_launch_app_ui(config_json: str, udid: str, bundle_id: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("launch", {"udid": udid or None, "bundle_id": bundle_id})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS launch 失败: {e}"


def ios_terminate_app_ui(config_json: str, udid: str, bundle_id: str):
    try:
        cfg = json.loads(config_json)
        service = _ios_service_from_config(cfg)
        result = service.call("terminate", {"udid": udid or None, "bundle_id": bundle_id})
        return json.dumps(result, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, IOSServiceError, Exception) as e:
        return f"✗ iOS terminate 失败: {e}"


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
                        preset_task = gr.Dropdown(
                            label="预设任务",
                            choices=list(PRESET_TASK_LIBRARY.keys()),
                            value="（不使用预设）",
                        )

                        task_input = gr.Textbox(
                            label="任务描述",
                            placeholder="例如：打开浏览器并搜索上海天气",
                            lines=3
                        )

                        preset_status = gr.Textbox(
                            label="预设状态",
                            value="可从上方下拉选择预设任务，一键填充任务描述。",
                            interactive=False,
                            lines=1,
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
                            height=520
                        )

                        task_tree_output = gr.Markdown(
                            value=format_task_tree_markdown(None, None, None),
                            elem_id="task-tree-panel",
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
            
            with gr.Tab("🍎 iOS Bridge"):
                gr.Markdown("### iOS 最小可用桥接（macOS + go-ios + WDA）")
                gr.Markdown("- 仅支持 macOS（Darwin）\n- 需要本机可执行 `go-ios`\n- 可自动拉起 tunnel / runwda，并等待 WDA readiness\n- 支持 session 自动创建与复用\n- Debug-first：环境未就绪会显式报错，不会假成功")

                with gr.Row():
                    ios_udid = gr.Textbox(
                        label="iOS UDID（可选，留空使用 config.ios_default_udid）",
                        value=current_config.get("ios_default_udid", ""),
                    )
                    ios_bundle_id = gr.Textbox(
                        label="Bundle ID（用于 launch/terminate）",
                        placeholder="例如：com.apple.Preferences",
                        value="",
                    )

                with gr.Row():
                    ios_discover_btn = gr.Button("🔎 发现设备 (go-ios list)")
                    ios_prepare_btn = gr.Button("🧰 一键准备 (tunnel+runwda+session)")
                    ios_health_btn = gr.Button("🩺 健康检查 (go-ios + WDA)")
                    ios_screenshot_btn = gr.Button("📸 iOS 截图")
                    ios_source_btn = gr.Button("📄 获取 UI Source")

                with gr.Row():
                    ios_tap_x = gr.Number(label="tap X", value=200)
                    ios_tap_y = gr.Number(label="tap Y", value=300)
                    ios_tap_btn = gr.Button("👆 Tap")

                with gr.Row():
                    ios_swipe_x1 = gr.Number(label="swipe X1", value=300)
                    ios_swipe_y1 = gr.Number(label="swipe Y1", value=1200)
                    ios_swipe_x2 = gr.Number(label="swipe X2", value=300)
                    ios_swipe_y2 = gr.Number(label="swipe Y2", value=400)
                    ios_swipe_duration = gr.Number(label="duration(s)", value=0.2)
                    ios_swipe_btn = gr.Button("↕️ Swipe")

                with gr.Row():
                    ios_type_text = gr.Textbox(label="输入文本", value="hello")
                    ios_type_btn = gr.Button("⌨️ Type")
                    ios_launch_btn = gr.Button("🚀 Launch App")
                    ios_terminate_btn = gr.Button("🛑 Terminate App")

                ios_image_output = gr.Image(label="iOS 当前截图", type="filepath", height=420)
                ios_result_output = gr.Textbox(label="iOS 输出", lines=14, max_lines=20, interactive=False)

            with gr.Tab("❓ 帮助"):
                gr.Markdown("""
## 快速开始

1. **连接设备**：开启 USB 调试，连接设备
2. **配置分辨率**：在“设置”页点击自动检测
3. **选择预设（可选）**：在“任务控制”页选择预设任务，一键填充输入框
4. **运行任务**：检查任务描述并点击“开始任务”

## 任务树如何理解
- 右侧“任务树 / 规划步骤”会展示 phase2 生成的步骤清单。
- 每个步骤包含：`step_name`（步骤名）、`instruction`（执行指令）、`success_criteria`（成功标准）。
- 状态说明：
  - ⬜ 待执行
  - 🟡 执行中
  - 🟢 已完成
  - 🔴 失败
- “当前步骤索引”表示当前正在执行（或最后一次停留）的步骤位置。

## 预设任务如何使用
- 在“预设任务”下拉选择一个场景，会自动填充“任务描述”。
- 若选择“（不使用预设）”，将保留当前输入内容。
- 建议先用预设验证环境连通，再改成自定义任务。

## 任务示例
- “打开浏览器并搜索上海天气”
- “打开设置检查网络连接”
- “进入应用并停留 10 秒”

## 故障排查
- **点击不准**：检查设置页的分辨率是否与真机一致
- **找不到设备**：在终端执行 `adb devices`
- **执行报错**：查看“执行日志”与“任务树”中的失败步骤
                """)
        
        timer = gr.Timer(value=3, active=False)
        
        # Event handlers
        preset_task.change(
            fn=apply_preset_task,
            inputs=[preset_task, task_input],
            outputs=[task_input, preset_status],
        )

        start_btn.click(
            fn=start_task,
            inputs=[task_input, max_cycles, config_editor],
            outputs=[status_text, image_output, timer, task_tree_output]
        )

        stop_btn.click(
            fn=stop_task,
            outputs=status_text
        )

        timer.tick(
            fn=update_ui,
            outputs=[image_output, log_output, timer, task_tree_output]
        )

        refresh_btn.click(
            fn=update_ui,
            outputs=[image_output, log_output, timer, task_tree_output]
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

        ios_discover_btn.click(
            fn=ios_discover_devices_ui,
            inputs=[config_editor],
            outputs=[ios_result_output],
        )

        ios_prepare_btn.click(
            fn=ios_prepare_ui,
            inputs=[config_editor, ios_udid],
            outputs=[ios_result_output],
        )

        ios_health_btn.click(
            fn=ios_health_check_ui,
            inputs=[config_editor, ios_udid],
            outputs=[ios_result_output],
        )

        ios_screenshot_btn.click(
            fn=ios_screenshot_ui,
            inputs=[config_editor, ios_udid],
            outputs=[ios_image_output, ios_result_output],
        )

        ios_source_btn.click(
            fn=ios_source_ui,
            inputs=[config_editor, ios_udid],
            outputs=[ios_result_output],
        )

        ios_tap_btn.click(
            fn=ios_tap_ui,
            inputs=[config_editor, ios_udid, ios_tap_x, ios_tap_y],
            outputs=[ios_result_output],
        )

        ios_swipe_btn.click(
            fn=ios_swipe_ui,
            inputs=[
                config_editor,
                ios_udid,
                ios_swipe_x1,
                ios_swipe_y1,
                ios_swipe_x2,
                ios_swipe_y2,
                ios_swipe_duration,
            ],
            outputs=[ios_result_output],
        )

        ios_type_btn.click(
            fn=ios_type_ui,
            inputs=[config_editor, ios_udid, ios_type_text],
            outputs=[ios_result_output],
        )

        ios_launch_btn.click(
            fn=ios_launch_app_ui,
            inputs=[config_editor, ios_udid, ios_bundle_id],
            outputs=[ios_result_output],
        )

        ios_terminate_btn.click(
            fn=ios_terminate_app_ui,
            inputs=[config_editor, ios_udid, ios_bundle_id],
            outputs=[ios_result_output],
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
