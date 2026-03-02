import os
import json
import logging
import subprocess
from pathlib import Path
from threading import Thread
import gradio as gr

from phone_agent import PhoneAgent


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
        "checkpoint_dir": "./checkpoints"
    }


def save_config(config, config_path="config.json"):
    """Save configuration to file."""
    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logging.error(f"Failed to save config: {e}")
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


def detect_device_resolution():
    """Try to detect connected device resolution via ADB."""
    try:
        result = subprocess.run(
            ["adb", "shell", "wm", "size"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0 and "Physical size:" in result.stdout:
            size_str = result.stdout.split("Physical size:")[1].strip()
            width, height = map(int, size_str.split('x'))
            return width, height, f"✓ Detected: {width} x {height}"
        else:
            return None, None, "⚠️ No device detected"
            
    except Exception as e:
        return None, None, f"✗ Error: {str(e)}"


def execute_task_thread(task, max_cycles, config):
    """Run task in background thread."""
    global current_screenshot, is_running, agent
    
    if log_handler:
        log_handler.logs.clear()
    
    is_running = True
    
    try:
        logging.info(f"Starting task: '{task}'")
        
        # Only create agent if it doesn't exist
        if agent is None:
            logging.info("Initializing Phone Agent (first time)...")
            agent = PhoneAgent(config)
        else:
            logging.info("Reusing existing agent...")
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
            logging.info(f"✓ Task completed in {result['cycles']} cycles")
        else:
            logging.info(f"⚠️ Task incomplete after {result['cycles']} cycles")
            
    except KeyboardInterrupt:
        logging.info("Task interrupted by user")
    except Exception as e:
        logging.error(f"Task execution error: {e}", exc_info=True)
    finally:
        is_running = False


def start_task(task, max_cycles, config_json):
    """Start a task execution."""
    global is_running, current_config
    
    if is_running:
        return (
            "⚠️ A task is already running",
            None,
            gr.update(active=False)
        )
    
    if not task.strip():
        return (
            "✗ Please enter a task",
            None,
            gr.update(active=False)
        )
    
    try:
        config = json.loads(config_json)
        current_config = config
    except json.JSONDecodeError as e:
        return (
            f"✗ Invalid config JSON: {e}",
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
        "✓ Task started...",
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
        logging.warning("Task stop requested by user")
        try:
            if agent is not None:
                agent.context['stop_requested'] = True
        except Exception:
            pass
        is_running = False
        return "⚠️ Stopping task..."
    return "No task running"


def apply_settings(
    screen_width,
    screen_height,
    temp,
    max_tok,
    step_delay,
    use_fa2,
    visual_debug,
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

        config['ignore_terminate_for_continuous_tasks'] = bool(ignore_terminate_continuous)
        config['continuous_min_cycles'] = max(1, int(continuous_min_cycles))
        config['continuous_min_minutes'] = max(0.0, float(continuous_min_minutes))
        
        if save_config(config):
            current_config = config
            return "✓ Settings saved", json.dumps(config, indent=2)
        else:
            return "✗ Failed to save settings", json.dumps(config, indent=2)
            
    except ValueError as e:
        return f"✗ Invalid value: {e}", json.dumps(current_config or {}, indent=2)


def auto_detect_resolution():
    """Auto-detect device resolution."""
    width, height, message = detect_device_resolution()
    
    if width and height:
        return width, height, message
    else:
        return 1080, 2340, message


def clear_logs_fn():
    """Clear the log display."""
    if log_handler:
        log_handler.logs.clear()
    return ""


def create_ui():
    """Create the Gradio interface."""
    global current_config
    current_config = load_config()
    
    Path(current_config['screenshot_dir']).mkdir(parents=True, exist_ok=True)
    
    with gr.Blocks(title="Phone Agent Control Panel", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 📱 Phone Agent Control Panel")
        gr.Markdown("*Powered by Qwen3-VL-30B for mobile GUI automation*")
        
        with gr.Tabs():
            with gr.Tab("🎯 Task Control"):
                with gr.Row():
                    with gr.Column(scale=2):
                        task_input = gr.Textbox(
                            label="Task Description",
                            placeholder="e.g., 'Open Chrome and search for weather in New York'",
                            lines=3
                        )
                        
                        with gr.Row():
                            max_cycles = gr.Number(
                                label="Max Cycles",
                                value=15,
                                minimum=1,
                                maximum=50
                            )
                            start_btn = gr.Button("▶️ Start Task", variant="primary", scale=2)
                            stop_btn = gr.Button("⏹️ Stop", variant="stop", scale=1)
                        
                        status_text = gr.Textbox(label="Status", lines=2, interactive=False)
                    
                    with gr.Column(scale=3):
                        image_output = gr.Image(
                            label="Current Screen",
                            type="filepath",
                            height=600
                        )
                
                log_output = gr.Textbox(
                    label="📋 Execution Log",
                    lines=15,
                    max_lines=20,
                    interactive=False,
                    show_copy_button=True
                )
                
                with gr.Row():
                    refresh_btn = gr.Button("🔄 Refresh Display")
                    clear_logs_btn = gr.Button("🗑️ Clear Logs")
            
            with gr.Tab("⚙️ Settings"):
                gr.Markdown("### Device Configuration")
                
                with gr.Row():
                    with gr.Column():
                        detect_btn = gr.Button("🔍 Auto-Detect Device Resolution")
                        detect_status = gr.Textbox(label="Detection Status", interactive=False)
                    
                    with gr.Column():
                        screen_width = gr.Number(
                            label="Screen Width (pixels)",
                            value=current_config['screen_width']
                        )
                        screen_height = gr.Number(
                            label="Screen Height (pixels)",
                            value=current_config['screen_height']
                        )
                
                gr.Markdown("### Model Parameters")
                
                with gr.Row():
                    temperature = gr.Slider(
                        label="Temperature",
                        minimum=0.0,
                        maximum=1.0,
                        value=current_config['temperature'],
                        step=0.05
                    )
                    max_tokens = gr.Number(
                        label="Max Tokens",
                        value=current_config['max_tokens'],
                        minimum=128,
                        maximum=2048
                    )
                
                with gr.Row():
                    step_delay = gr.Slider(
                        label="Step Delay (seconds)",
                        minimum=0.5,
                        maximum=5.0,
                        value=current_config['step_delay'],
                        step=0.1
                    )
                
                gr.Markdown("### Advanced Options")
                
                with gr.Row():
                    use_flash_attn = gr.Checkbox(
                        label="Use Flash Attention 2",
                        value=current_config.get('use_flash_attention', False)
                    )
                    visual_debug = gr.Checkbox(
                        label="Enable Visual Debug",
                        value=current_config.get('enable_visual_debug', False)
                    )

                gr.Markdown("### Continuous Task (刷一会/持续任务)")
                with gr.Row():
                    ignore_terminate_continuous = gr.Checkbox(
                        label="Ignore early terminate for continuous tasks",
                        value=current_config.get('ignore_terminate_for_continuous_tasks', True)
                    )
                    continuous_min_cycles = gr.Number(
                        label="Continuous Min Cycles",
                        value=current_config.get('continuous_min_cycles', 20),
                        minimum=1,
                        maximum=500
                    )
                    continuous_min_minutes = gr.Number(
                        label="Continuous Min Minutes",
                        value=current_config.get('continuous_min_minutes', 0),
                        minimum=0,
                        maximum=600
                    )
                
                apply_btn = gr.Button("💾 Save Settings", variant="primary")
                settings_status = gr.Textbox(label="Settings Status", interactive=False)
                
                gr.Markdown("### Configuration JSON")
                config_editor = gr.Code(
                    label="Current Configuration",
                    language="json",
                    value=json.dumps(current_config, indent=2),
                    lines=15
                )
            
            with gr.Tab("❓ Help"):
                gr.Markdown("""
## Quick Start

1. **Connect Device**: USB debugging enabled, device connected
2. **Configure Resolution**: Use auto-detect in Settings tab
3. **Run Task**: Enter task description and click Start

## Task Examples
- "Open Chrome"
- "Search for weather on Google"
- "Open Settings and enable WiFi"

## Troubleshooting
- **Wrong taps**: Check screen resolution in Settings
- **No device**: Run `adb devices` in terminal
- **Errors**: Check the Execution Log tab
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
                ignore_terminate_continuous,
                continuous_min_cycles,
                continuous_min_minutes,
            ],
            outputs=[settings_status, config_editor]
        )
    
    return demo


def main():
    """Main entry point for the UI."""
    print("Phone Agent UI Starting...")
    print("Setting up logging...")
    setup_logging()
    
    print("Creating interface...")
    demo = create_ui()
    
    print("Starting server on http://localhost:7860")
    print("Press Ctrl+C to stop")
    
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        show_error=True
    )


if __name__ == "__main__":
    main()
