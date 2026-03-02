import os
import re
import json
import time
import math
import hashlib
import logging
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

from qwen_vl_agent import QwenVLAgent


class TaskPlanner:
    """Lightweight rule-based task planner for phase-2 step decomposition."""

    REQUIRED_KEYS = ("step_name", "instruction", "success_criteria")

    def __init__(self, max_steps: int = 8):
        self.max_steps = max(1, int(max_steps))

    def build_plan(self, user_request: str) -> Dict[str, Any]:
        request = (user_request or "").strip()
        if not request:
            raise ValueError("TaskPlanner requires a non-empty user request")

        raw_steps = self._split_request(request)
        if not raw_steps:
            raw_steps = [request]

        steps: List[Dict[str, str]] = []
        for idx, fragment in enumerate(raw_steps[: self.max_steps], start=1):
            instruction = fragment.strip()
            if not instruction:
                continue

            short_title = instruction
            if len(short_title) > 36:
                short_title = short_title[:33].rstrip() + "..."

            steps.append(
                {
                    "step_name": f"Step {idx}: {short_title}",
                    "instruction": instruction,
                    "success_criteria": f"已完成：{instruction}，并且界面可继续下一步。",
                }
            )

        if not steps:
            steps = [
                {
                    "step_name": "Step 1: 执行任务",
                    "instruction": request,
                    "success_criteria": f"已完成：{request}。",
                }
            ]

        plan = {
            "planner_version": "phase2-v1",
            "task": request,
            "steps": steps,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

        self.validate_plan(plan)
        return plan

    def validate_plan(self, plan: Dict[str, Any]) -> bool:
        if not isinstance(plan, dict):
            raise ValueError("Task plan must be a dictionary")

        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Task plan must contain a non-empty steps list")

        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"Step #{idx + 1} must be a dictionary")
            for key in self.REQUIRED_KEYS:
                value = str(step.get(key, "")).strip()
                if not value:
                    raise ValueError(f"Step #{idx + 1} missing required field: {key}")

        return True

    def _split_request(self, request: str) -> List[str]:
        text = request.strip()

        # First pass: punctuation / line breaks
        tokens = re.split(r"(?:[\n;；。]+)", text)
        parts: List[str] = []
        for token in tokens:
            token = token.strip()
            if not token:
                continue

            # Second pass: common connectors (CN + EN)
            fragments = re.split(
                r"(?:\s+and then\s+|\s+then\s+|\s+next\s+|\s+finally\s+|然后|并且|接着|再|最后)",
                token,
                flags=re.IGNORECASE,
            )
            for frag in fragments:
                frag = frag.strip(" ,，")
                if frag:
                    parts.append(frag)

        return parts


class PhoneAgent:
    """
    Phone automation agent using Qwen3-VL for visual understanding and ADB for control.
    
    This agent:
    - Captures screenshots from Android devices via ADB
    - Uses Qwen3-VL to analyze screens and determine actions
    - Executes actions through ADB commands
    - Tracks context and action history
    """

    EXCEPTION_KEYWORDS = {
        'permission_popup': [
            '权限', '允许', '始终允许', '仅在使用中允许', '仅在使用该应用时允许',
            'permission', 'allow', 'deny', 'while using the app'
        ],
        'update_popup': [
            '立即更新', '发现新版本', '版本更新', '升级', '更新应用',
            'update', 'upgrade', 'new version', 'later', 'not now', 'skip'
        ],
        'login_guide': [
            '登录后', '登录体验', '立即登录', '注册', '手机号登录', '一键登录', '游客',
            'sign in', 'log in', 'login', 'create account', 'continue with'
        ],
        'network_error': [
            '网络异常', '网络错误', '连接失败', '加载失败', '请求失败', '重试',
            'network error', 'connection failed', 'no internet', 'request failed', 'retry'
        ],
        'captcha_entry': [
            '验证码', '滑块', '拼图', '图形验证', '安全验证', '二次验证', '短信验证', '动态码',
            'captcha', 'verify', 'verification', 'otp', '2fa', 'two-factor', 'security check'
        ],
    }

    EXCEPTION_PRIORITY = [
        'captcha_entry',
        'permission_popup',
        'update_popup',
        'login_guide',
        'network_error',
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize the phone agent.
        
        Args:
            config: Configuration dictionary
        """
        # Default configuration
        default_config = {
            'device_id': None,  # Auto-detect first device if None
            'screen_width': 1080,  # Must match your device
            'screen_height': 2340,  # Must match your device
            'screenshot_dir': './screenshots',
            'max_retries': 3,
            'model_name': 'Qwen/Qwen3-VL-30B-A3B-Instruct',
            'use_flash_attention': False,
            'temperature': 0.1,
            'max_tokens': 512,
            'step_delay': 1.5,  # Seconds to wait after each action
            'enable_visual_debug': False,  # Save annotated screenshots
            # Remote API mode (OpenAI-compatible)
            'use_remote_api': False,
            'api_base_url': '',
            'api_key': '',
            'api_model': 'qwen3.5-plus',
            'api_timeout': 120,
            'adb_command_timeout': 15,
            'ignore_terminate_for_continuous_tasks': True,
            'continuous_min_cycles': 20,
            'continuous_min_minutes': 0,
            # Phase-1: dynamic retry budget
            'enable_dynamic_retry_budget': True,
            'retry_budget_simple': 2,
            'retry_budget_medium': 4,
            'retry_budget_complex': 6,
            'retry_budget_cap': 8,
            # Phase-2: task planner + checkpoint recovery
            'enable_task_planner': True,
            'planner_max_steps': 8,
            'enable_checkpoint_recovery': True,
            'checkpoint_dir': './checkpoints',
            # Phase-3: UI exception handling
            'enable_exception_handler': True,
            'hitl_on_captcha': True,
            'exception_network_backoff_ms': 2000,
        }
        
        self.config = default_config
        if config:
            self.config.update(config)

        # Basic config sanitization for runtime stability
        try:
            self.config['step_delay'] = max(0.0, float(self.config.get('step_delay', 1.5)))
        except Exception:
            self.config['step_delay'] = 1.5
        try:
            self.config['max_retries'] = max(1, int(self.config.get('max_retries', 3)))
        except Exception:
            self.config['max_retries'] = 3
        try:
            self.config['adb_command_timeout'] = max(3, int(self.config.get('adb_command_timeout', 15)))
        except Exception:
            self.config['adb_command_timeout'] = 15

        self.config['enable_dynamic_retry_budget'] = bool(self.config.get('enable_dynamic_retry_budget', True))
        try:
            self.config['retry_budget_simple'] = max(1, int(self.config.get('retry_budget_simple', 2)))
        except Exception:
            self.config['retry_budget_simple'] = 2
        try:
            self.config['retry_budget_medium'] = max(1, int(self.config.get('retry_budget_medium', 4)))
        except Exception:
            self.config['retry_budget_medium'] = 4
        try:
            self.config['retry_budget_complex'] = max(1, int(self.config.get('retry_budget_complex', 6)))
        except Exception:
            self.config['retry_budget_complex'] = 6
        try:
            self.config['retry_budget_cap'] = max(1, int(self.config.get('retry_budget_cap', 8)))
        except Exception:
            self.config['retry_budget_cap'] = 8

        self.config['enable_task_planner'] = bool(self.config.get('enable_task_planner', True))
        try:
            self.config['planner_max_steps'] = max(1, int(self.config.get('planner_max_steps', 8)))
        except Exception:
            self.config['planner_max_steps'] = 8
        self.config['enable_checkpoint_recovery'] = bool(self.config.get('enable_checkpoint_recovery', True))

        checkpoint_dir = str(self.config.get('checkpoint_dir', './checkpoints') or './checkpoints').strip()
        self.config['checkpoint_dir'] = checkpoint_dir

        self.config['enable_exception_handler'] = bool(self.config.get('enable_exception_handler', True))
        self.config['hitl_on_captcha'] = bool(self.config.get('hitl_on_captcha', True))
        try:
            self.config['exception_network_backoff_ms'] = max(500, int(self.config.get('exception_network_backoff_ms', 2000)))
        except Exception:
            self.config['exception_network_backoff_ms'] = 2000

        self.task_planner = TaskPlanner(max_steps=self.config['planner_max_steps'])
        self.current_plan: Optional[Dict[str, Any]] = None
        self.current_step_index: int = 0
        self.step_status: Dict[str, str] = {}
        self.current_checkpoint_path: Optional[str] = None

        # Session context
        self.context = {
            'previous_actions': [],
            'current_app': "Home",
            'task_request': "",
            'continuous_task': False,
            'task_started_at': None,
            'session_id': datetime.now().strftime("%Y%m%d_%H%M%S"),
            'screenshots': [],
            'exception_events': [],
            'last_exception_type': None,
            'last_handler_action': None,
            'last_hitl_triggered': False,
        }
        
        # Setup logging
        self._setup_logging()
        
        # Initialize directories
        self._setup_directories()
        
        # Check ADB connection
        self._check_adb_connection()
        
        # Initialize Qwen3-VL agent
        logging.info("Initializing Qwen3-VL agent...")
        self.vl_agent = QwenVLAgent(
            model_name=self.config.get('model_name', 'Qwen/Qwen3-VL-8B-Instruct'),
            use_flash_attention=self.config.get('use_flash_attention', False),
            temperature=self.config['temperature'],
            max_tokens=self.config['max_tokens'],
            use_remote_api=self.config.get('use_remote_api', False),
            api_base_url=self.config.get('api_base_url') or None,
            api_key=self.config.get('api_key') or None,
            api_model=self.config.get('api_model') or None,
            api_timeout=int(self.config.get('api_timeout', 120)),
        )
        logging.info("Phone agent ready")
    
    def _setup_logging(self):
        """Configure logging for this session."""
        log_file = f"phone_agent_{self.context['session_id']}.log"
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        logging.info(f"Session started: {self.context['session_id']}")
    
    def _setup_directories(self):
        """Create necessary directories."""
        Path(self.config['screenshot_dir']).mkdir(parents=True, exist_ok=True)
        Path(self.config['checkpoint_dir']).mkdir(parents=True, exist_ok=True)
        logging.info(f"Screenshots directory: {self.config['screenshot_dir']}")
        logging.info(f"Checkpoint directory: {self.config['checkpoint_dir']}")
    
    def _check_adb_connection(self):
        """Verify ADB connection and get device info."""
        try:
            # List devices
            result = subprocess.run(
                ["adb", "devices"],
                check=True,
                capture_output=True,
                text=True
            )
            
            # Auto-detect device if not specified
            if self.config['device_id'] is None:
                lines = result.stdout.strip().split('\n')
                if len(lines) > 1:
                    device_info = lines[1].split('\t')
                    if len(device_info) > 0 and device_info[1].strip() == 'device':
                        self.config['device_id'] = device_info[0].strip()
                        logging.info(f"Auto-detected device: {self.config['device_id']}")
                    else:
                        raise Exception("No authorized device found")
                else:
                    raise Exception("No devices connected")
            
            # Test connection
            self._run_adb_command("shell echo 'Connected'")
            logging.info("✓ ADB connection verified")
            
            # Get actual screen resolution
            self._verify_screen_resolution()
            
        except subprocess.CalledProcessError as e:
            logging.error(f"ADB error: {e}")
            raise Exception(
                "Failed to connect via ADB. Ensure USB debugging is enabled and device is authorized."
            )
    
    def _verify_screen_resolution(self):
        """Verify the configured screen resolution matches the device."""
        try:
            result = self._run_adb_command("shell wm size")
            # Output format: "Physical size: 1080x2340"
            if "Physical size:" in result:
                size_str = result.split("Physical size:")[1].strip()
                width, height = map(int, size_str.split('x'))
                
                if width != self.config['screen_width'] or height != self.config['screen_height']:
                    logging.warning("=" * 60)
                    logging.warning("RESOLUTION MISMATCH DETECTED!")
                    logging.warning(f"Device actual:    {width} x {height}")
                    logging.warning(f"Config setting:   {self.config['screen_width']} x {self.config['screen_height']}")
                    logging.warning("Please update config.json with correct resolution!")
                    logging.warning("=" * 60)
                    
                    # Update config automatically
                    self.config['screen_width'] = width
                    self.config['screen_height'] = height
                    logging.info(f"Auto-corrected to: {width} x {height}")
                else:
                    logging.info(f"✓ Screen resolution confirmed: {width} x {height}")
        except Exception as e:
            logging.warning(f"Could not verify screen resolution: {e}")
    
    def _run_adb_command(self, command: str) -> str:
        """Execute an ADB command and return output."""
        device_id = self.config.get('device_id') if isinstance(self.config, dict) else None
        device_prefix = f"-s {device_id}" if device_id else ""
        full_command = f"adb {device_prefix} {command}"
        timeout_s = int(self.config.get('adb_command_timeout', 15))

        try:
            result = subprocess.run(
                full_command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return result.stdout
        except subprocess.TimeoutExpired as e:
            logging.error(f"ADB command timeout ({timeout_s}s): {command}")
            stderr = (e.stderr or "").strip() if hasattr(e, 'stderr') else ""
            if stderr:
                logging.error(f"Timeout stderr: {stderr}")
            raise TimeoutError(f"ADB command timed out after {timeout_s}s: {command}")
        except subprocess.CalledProcessError as e:
            logging.error(f"ADB command failed: {command}")
            stderr = (e.stderr or '').strip()
            stdout = (e.stdout or '').strip()
            if stderr:
                logging.error(f"ADB stderr: {stderr}")
            if stdout:
                logging.error(f"ADB stdout: {stdout}")
            raise
    
    def capture_screenshot(self) -> str:
        """
        Capture a screenshot from the device.
        
        Returns:
            Path to the saved screenshot
        """
        timestamp = int(time.time())
        screenshot_path = os.path.join(
            self.config['screenshot_dir'],
            f"screen_{self.context['session_id']}_{timestamp}.png"
        )
        
        try:
            # Capture and transfer screenshot
            self._run_adb_command("shell screencap -p /sdcard/screenshot.png")
            self._run_adb_command(f"pull /sdcard/screenshot.png {screenshot_path}")
            self._run_adb_command("shell rm /sdcard/screenshot.png")
            
            logging.info(f"Screenshot captured: {screenshot_path}")
            self.context['screenshots'].append(screenshot_path)
            return screenshot_path
            
        except Exception as e:
            logging.error(f"Screenshot capture failed: {e}")
            raise
    
    def execute_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute an action on the device.
        
        Args:
            action: Action dictionary from Qwen3-VL
            
        Returns:
            Result dictionary with success status
        """
        try:
            action_type = action['action']
            logging.info(f"Executing: {action_type}")
            
            # Handle task completion
            if action_type == 'terminate':
                status = action.get('status', 'success')
                message = action.get('message', 'Task complete')

                # For continuous tasks (e.g. "刷一会", "持续"), avoid ending too early.
                if (
                    self.config.get('ignore_terminate_for_continuous_tasks', True)
                    and self.context.get('continuous_task')
                ):
                    current_cycle = int(self.context.get('cycle_index', 0))
                    min_cycles = int(self.config.get('continuous_min_cycles', 20))
                    min_minutes = float(self.config.get('continuous_min_minutes', 0) or 0)
                    elapsed = 0.0
                    if self.context.get('task_started_at'):
                        elapsed = max(0.0, time.time() - float(self.context.get('task_started_at')))

                    hold_by_cycle = current_cycle < min_cycles
                    hold_by_time = (min_minutes > 0) and (elapsed < min_minutes * 60)

                    if hold_by_cycle or hold_by_time:
                        logging.info(
                            f"↺ Ignore terminate for continuous task (cycle {current_cycle}/{min_cycles}, elapsed={elapsed:.1f}s, min_minutes={min_minutes}): {status} - {message}"
                        )
                        self._execute_wait({'waitTime': 6000})
                        return {
                            'success': True,
                            'action': {'action': 'wait', 'waitTime': 6000, 'observation': 'continuous-task-guard'},
                            'task_complete': False
                        }

                logging.info(f"✓ Task {status}: {message}")
                return {
                    'success': True,
                    'action': action,
                    'task_complete': True
                }
            
            # Handle each action type
            if action_type == 'tap':
                self._execute_tap(action)
            
            elif action_type == 'swipe':
                self._execute_swipe(action)
            
            elif action_type == 'type':
                self._execute_type(action)
            
            elif action_type == 'wait':
                self._execute_wait(action)

            elif action_type == 'system':
                self._execute_system(action)
            
            else:
                raise ValueError(f"Unknown action type: {action_type}")
            
            # Record action in history
            self.context['previous_actions'].append({
                'action': action_type,
                'timestamp': time.time(),
                'elementName': action.get('observation', '')[:50]  # Brief description
            })
            
            # Standard delay after action
            time.sleep(self.config['step_delay'])
            
            return {
                'success': True,
                'action': action,
                'task_complete': False
            }
            
        except Exception as e:
            logging.error(f"Action execution failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'action': action,
                'task_complete': False
            }
    
    def _execute_tap(self, action: Dict[str, Any]):
        """Execute a tap action."""
        if 'coordinates' not in action:
            raise ValueError("Tap action missing coordinates")
        
        # Get normalized coordinates
        norm_x, norm_y = action['coordinates']
        
        # Convert to pixel coordinates
        x = int(norm_x * self.config['screen_width'])
        y = int(norm_y * self.config['screen_height'])
        
        # Clamp to screen bounds
        x = max(0, min(x, self.config['screen_width'] - 1))
        y = max(0, min(y, self.config['screen_height'] - 1))
        
        logging.info(f"Tapping at ({x}, {y}) [normalized: ({norm_x:.3f}, {norm_y:.3f})]")
        self._run_adb_command(f"shell input tap {x} {y}")
    
    def _execute_swipe(self, action: Dict[str, Any]):
        """Execute a swipe action."""
        direction = action.get('direction', 'up')
        
        # Calculate swipe coordinates
        center_x = self.config['screen_width'] // 2
        center_y = self.config['screen_height'] // 2
        
        start_x, start_y = center_x, center_y
        
        # Define swipe distances (70% of screen dimension)
        swipe_distance = 0.7
        
        if direction == 'up':
            end_x = center_x
            end_y = int(center_y * (1 - swipe_distance))
        elif direction == 'down':
            end_x = center_x
            end_y = int(center_y * (1 + swipe_distance))
        elif direction == 'left':
            end_x = int(center_x * (1 - swipe_distance))
            end_y = center_y
        elif direction == 'right':
            end_x = int(center_x * (1 + swipe_distance))
            end_y = center_y
        else:
            raise ValueError(f"Invalid swipe direction: {direction}")
        
        logging.info(f"Swiping {direction}: ({start_x}, {start_y}) -> ({end_x}, {end_y})")
        self._run_adb_command(f"shell input swipe {start_x} {start_y} {end_x} {end_y} 300")
    
    def _execute_type(self, action: Dict[str, Any]):
        """Execute a type action."""
        if 'text' not in action:
            raise ValueError("Type action missing text")

        text = str(action.get('text', ''))
        if text.strip() == '':
            raise ValueError("Type action has empty text")

        # Check if we tapped a text field recently
        recent_actions = self.context['previous_actions'][-3:]
        tapped_text_field = any(
            a.get('action') == 'tap' for a in recent_actions
        )

        if not tapped_text_field:
            logging.warning("Type action without recent tap - may fail")

        # Escape and format text for ADB
        escaped_text = text.replace("'", "\\'").replace('"', '\\"')
        escaped_text = escaped_text.replace(" ", "%s")  # ADB requires %s for spaces

        logging.info(f"Typing: {text}")
        self._run_adb_command(f'shell input text "{escaped_text}"')
    
    def _execute_wait(self, action: Dict[str, Any]):
        """Execute a wait action."""
        wait_time = action.get('waitTime', 1000) / 1000.0  # Convert ms to seconds
        logging.info(f"Waiting {wait_time:.1f}s...")
        time.sleep(wait_time)

    def _execute_system(self, action: Dict[str, Any]):
        """Execute Android system actions like HOME/BACK/RECENTS."""
        key_text = str(action.get('text', '')).strip().lower()

        key_map = {
            'home': 3,
            'back': 4,
            'recent': 187,
            'recents': 187,
            'app_switch': 187,
            'power': 26,
            'enter': 66,
            'menu': 82,
        }

        if not key_text:
            raise ValueError("System action missing 'text' field")

        if key_text not in key_map:
            raise ValueError(f"Unsupported system action: {key_text}")

        keycode = key_map[key_text]
        logging.info(f"System action: {key_text} (KEYCODE {keycode})")
        self._run_adb_command(f"shell input keyevent {keycode}")
    
    def _extract_text_tokens(self, screenshot_path: str) -> List[str]:
        """Best-effort OCR/token extraction from UI XML dump for exception detection."""
        tokens: List[str] = []

        try:
            remote_tmp = f"/sdcard/ui_dump_{self.context.get('session_id', 'sess')}.xml"
            local_tmp = str(Path(self.config.get('screenshot_dir', './screenshots')) / f"ui_dump_{int(time.time())}.xml")

            self._run_adb_command(f"shell uiautomator dump {remote_tmp}")
            self._run_adb_command(f"pull {remote_tmp} {local_tmp}")
            self._run_adb_command(f"shell rm {remote_tmp}")

            if os.path.exists(local_tmp):
                try:
                    root = ET.parse(local_tmp).getroot()
                    for node in root.iter('node'):
                        for attr in ('text', 'content-desc', 'resource-id'):
                            val = str(node.attrib.get(attr, '')).strip()
                            if val:
                                tokens.append(val)
                finally:
                    try:
                        os.remove(local_tmp)
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"UI dump unavailable for exception detection: {e}")

        # fallback: attach model observation/reasoning snippets from recent actions
        for item in self.context.get('previous_actions', [])[-3:]:
            snippet = str(item.get('elementName', '')).strip()
            if snippet:
                tokens.append(snippet)

        return tokens

    def _detect_ui_exception(self, screenshot_path: str) -> Optional[str]:
        """Detect common blocking UI exception state from visible text tokens."""
        if not self.config.get('enable_exception_handler', True):
            return None

        tokens = self._extract_text_tokens(screenshot_path)
        if not tokens:
            return None

        haystack = "\n".join(tokens).lower()
        for exception_type in self.EXCEPTION_PRIORITY:
            markers = self.EXCEPTION_KEYWORDS.get(exception_type, [])
            if any(str(marker).lower() in haystack for marker in markers):
                return exception_type
        return None

    def _record_exception_event(self, exception_type: Optional[str], handler_action: str, hitl_triggered: bool) -> None:
        event = {
            'timestamp': datetime.now().isoformat(timespec='seconds'),
            'exception_type': exception_type,
            'handler_action': handler_action,
            'hitl_triggered': bool(hitl_triggered),
        }
        self.context.setdefault('exception_events', []).append(event)
        self.context['last_exception_type'] = exception_type
        self.context['last_handler_action'] = handler_action
        self.context['last_hitl_triggered'] = bool(hitl_triggered)
        logging.info(
            f"Exception handling event: type={exception_type}, handler_action={handler_action}, hitl={hitl_triggered}"
        )

    def _select_exception_strategy(self, exception_type: Optional[str]) -> Dict[str, Any]:
        """Select deterministic handler strategy for detected exception."""
        if not exception_type:
            return {'mode': 'none', 'handler_action': 'none', 'hitl': False, 'action': None}

        if exception_type == 'captcha_entry':
            if self.config.get('hitl_on_captcha', True):
                return {
                    'mode': 'hitl',
                    'handler_action': 'trigger_hitl_captcha',
                    'hitl': True,
                    'action': {
                        'action': 'terminate',
                        'status': 'failure',
                        'message': 'HITL required: captcha or secondary verification detected',
                        'observation': 'captcha-entry-detected',
                    }
                }
            return {'mode': 'none', 'handler_action': 'captcha_hitl_disabled', 'hitl': False, 'action': None}

        if exception_type == 'permission_popup':
            return {
                'mode': 'blocking_popup',
                'handler_action': 'tap_allow_permission',
                'hitl': False,
                'action': {
                    'action': 'tap',
                    'coordinates': [0.78, 0.90],
                    'observation': 'exception-handler:allow-permission',
                }
            }

        if exception_type == 'update_popup':
            return {
                'mode': 'blocking_popup',
                'handler_action': 'tap_skip_update',
                'hitl': False,
                'action': {
                    'action': 'tap',
                    'coordinates': [0.22, 0.90],
                    'observation': 'exception-handler:skip-update',
                }
            }

        if exception_type == 'login_guide':
            return {
                'mode': 'blocking_popup',
                'handler_action': 'tap_skip_login_guide',
                'hitl': False,
                'action': {
                    'action': 'tap',
                    'coordinates': [0.88, 0.08],
                    'observation': 'exception-handler:close-login-guide',
                }
            }

        if exception_type == 'network_error':
            wait_ms = int(self.config.get('exception_network_backoff_ms', 2000))
            return {
                'mode': 'blocking_popup',
                'handler_action': f'wait_network_recover_{wait_ms}ms',
                'hitl': False,
                'action': {
                    'action': 'wait',
                    'waitTime': wait_ms,
                    'observation': 'exception-handler:network-backoff',
                }
            }

        return {'mode': 'none', 'handler_action': 'none', 'hitl': False, 'action': None}

    def _handle_detected_exception(self, screenshot_path: str) -> Optional[Dict[str, Any]]:
        """Apply exception-first priority; return preemption result if handled."""
        exception_type = self._detect_ui_exception(screenshot_path)
        strategy = self._select_exception_strategy(exception_type)

        if strategy.get('mode') == 'none':
            return None

        handler_action = str(strategy.get('handler_action', 'none'))
        hitl_triggered = bool(strategy.get('hitl', False))
        self._record_exception_event(exception_type, handler_action, hitl_triggered)

        action = strategy.get('action')
        if not action:
            return None

        result = self.execute_action(action)
        result['screenshot_path'] = screenshot_path
        result['planned_action'] = action
        result['preempted_by_exception'] = True
        result['exception_type'] = exception_type
        result['handler_action'] = handler_action
        result['hitl_triggered'] = hitl_triggered

        # HITL must explicitly stop blind execution
        if hitl_triggered:
            result['task_complete'] = True
            result['success'] = False

        return result

    def _actions_equivalent(self, left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> bool:
        """Check whether two actions are effectively the same (to avoid blind retries)."""
        if not left or not right:
            return False

        if left.get('action') != right.get('action'):
            return False

        action_type = left.get('action')
        if action_type == 'tap':
            l = left.get('coordinates') or [None, None]
            r = right.get('coordinates') or [None, None]
            if None in l or None in r:
                return False
            return abs(float(l[0]) - float(r[0])) <= 0.01 and abs(float(l[1]) - float(r[1])) <= 0.01

        if action_type == 'swipe':
            return str(left.get('direction', '')) == str(right.get('direction', ''))

        if action_type == 'type':
            return str(left.get('text', '')) == str(right.get('text', ''))

        if action_type == 'system':
            return str(left.get('text', '')).lower() == str(right.get('text', '')).lower()

        return action_type in {'wait', 'terminate'}

    def _classify_retry_reason(self, error_message: str, attempted_action: Optional[Dict[str, Any]] = None) -> str:
        """Classify failure reason into fixed observable categories."""
        action = attempted_action or {}
        merged = " ".join([
            str(error_message or ''),
            str(action.get('action', '')),
            str(action.get('observation', '')),
            str(action.get('reasoning', '')),
        ]).lower()

        coordinate_markers = [
            'coordinate', 'coordinates', 'out of bounds', 'tap action missing coordinates',
            'invalid swipe direction', 'offset', 'element not clickable point'
        ]
        loading_markers = [
            'timeout', 'timed out', 'loading', 'still loading', 'network',
            'failed to get action from model', 'remote api empty content', 'unable to verify'
        ]
        popup_markers = [
            'permission', 'popup', 'dialog', 'overlay', 'blocked', 'intercepted',
            'securityexception', 'unauthorized', 'allow', 'deny'
        ]

        if any(marker in merged for marker in coordinate_markers):
            return '坐标偏差'
        if any(marker in merged for marker in loading_markers):
            return '页面未加载'
        if any(marker in merged for marker in popup_markers):
            return '弹窗阻断'
        return '未知'

    def _estimate_task_complexity(self, user_request: str) -> str:
        """Estimate task complexity level: simple / medium / complex."""
        req = (user_request or '').strip().lower()
        if not req:
            return 'simple'

        score = 0
        if len(req) >= 24:
            score += 1
        if len(req) >= 64:
            score += 1

        connectors = ['然后', '并且', '接着', '再', '最后', 'and then', 'then', 'after', 'while']
        connector_hits = sum(req.count(k) for k in connectors)
        if connector_hits >= 1:
            score += 1
        if connector_hits >= 3:
            score += 1

        medium_markers = ['搜索', 'search', '设置', 'setting', '下载', 'download', '登录', 'login']
        complex_markers = ['持续', '一直', '不停', 'for a while', 'continuously', '循环', 'loop', 'until']

        if any(k in req for k in medium_markers):
            score += 1
        if any(k in req for k in complex_markers):
            score += 2

        if score >= 4:
            return 'complex'
        if score >= 2:
            return 'medium'
        return 'simple'

    def _resolve_retry_budget(self, user_request: str) -> Tuple[str, int]:
        """Resolve max consecutive retries based on configured dynamic budget."""
        cap = max(1, int(self.config.get('retry_budget_cap', 8)))

        if not self.config.get('enable_dynamic_retry_budget', True):
            static_budget = max(1, int(self.config.get('max_retries', 3)))
            return 'static', min(cap, static_budget)

        simple = max(1, int(self.config.get('retry_budget_simple', 2)))
        medium = max(simple, int(self.config.get('retry_budget_medium', 4)))
        complex_budget = max(medium, int(self.config.get('retry_budget_complex', 6)))

        level = self._estimate_task_complexity(user_request)
        mapping = {
            'simple': simple,
            'medium': medium,
            'complex': complex_budget,
        }
        return level, min(cap, mapping.get(level, medium))

    def _run_failure_feedback_loop(
        self,
        user_request: str,
        failed_action: Dict[str, Any],
        failed_error: str,
        cycle_index: int,
    ) -> Dict[str, Any]:
        """Failure recovery loop: re-screenshot -> classify -> ask model for corrected action."""
        retry_reason = self._classify_retry_reason(failed_error, failed_action)
        retry_round = int(self.context.get('retry_round', 0)) + 1
        self.context['retry_round'] = retry_round
        self.context['last_retry_reason'] = retry_reason

        decision = {
            'cycle': cycle_index,
            'retry_round': retry_round,
            'retry_reason': retry_reason,
            'failed_error': failed_error,
            'failed_action': failed_action,
            'decision': 're_screenshot_and_request_correction',
        }
        self.context.setdefault('retry_decisions', [])

        logging.warning(
            f"Retry loop triggered (cycle={cycle_index}, round={retry_round}, reason={retry_reason}): {failed_error}"
        )

        fresh_screenshot = self.capture_screenshot()
        feedback_payload = {
            'retry_reason': retry_reason,
            'failed_error': failed_error,
            'failed_action': failed_action,
            'retry_round': retry_round,
            'must_avoid_same_action': True,
        }

        corrected_action = self.vl_agent.analyze_screenshot(
            fresh_screenshot,
            user_request,
            self.context,
            retry_feedback=feedback_payload,
        )

        if not corrected_action:
            decision['decision'] = 'model_failed_to_return_correction'
            self.context['retry_decisions'].append(decision)
            logging.error('Retry loop failed: model returned no correction action')
            return {
                'success': False,
                'error': f"Retry correction unavailable ({retry_reason})",
                'task_complete': False,
                'retry_reason': retry_reason,
                'retry_decision': decision['decision'],
            }

        if self._actions_equivalent(corrected_action, failed_action):
            logging.warning('Model suggested same failed action; requesting alternative correction once more')
            feedback_payload['avoid_exact_action'] = failed_action
            corrected_action = self.vl_agent.analyze_screenshot(
                fresh_screenshot,
                user_request,
                self.context,
                retry_feedback=feedback_payload,
            )

        if not corrected_action or self._actions_equivalent(corrected_action, failed_action):
            decision['decision'] = 'correction_repeated_same_action'
            self.context['retry_decisions'].append(decision)
            logging.error('Retry loop aborted: correction still repeats failed action')
            return {
                'success': False,
                'error': f"Correction repeated failed action ({retry_reason})",
                'task_complete': False,
                'retry_reason': retry_reason,
                'retry_decision': decision['decision'],
            }

        decision['decision'] = f"execute_corrected_action:{corrected_action.get('action', 'unknown')}"
        corrected_result = self.execute_action(corrected_action)
        corrected_result['retry_reason'] = retry_reason
        corrected_result['retry_decision'] = decision['decision']
        corrected_result['corrected_action'] = corrected_action
        self.context['retry_decisions'].append(decision)

        logging.info(
            f"Retry correction decision: reason={retry_reason}, action={corrected_action.get('action')}, success={corrected_result.get('success')}"
        )
        return corrected_result

    def _ensure_phase2_runtime_state(self) -> None:
        """Initialize phase-2 runtime fields for tests/backward compatibility."""
        if not hasattr(self, 'config') or not isinstance(self.config, dict):
            self.config = {}

        self.config.setdefault('enable_task_planner', True)
        self.config.setdefault('planner_max_steps', 8)
        self.config.setdefault('enable_checkpoint_recovery', True)
        self.config.setdefault('checkpoint_dir', './checkpoints')
        self.config.setdefault('enable_exception_handler', True)
        self.config.setdefault('hitl_on_captcha', True)
        self.config.setdefault('exception_network_backoff_ms', 2000)

        if not hasattr(self, 'task_planner') or self.task_planner is None:
            self.task_planner = TaskPlanner(max_steps=int(self.config.get('planner_max_steps', 8)))

        if not hasattr(self, 'current_plan'):
            self.current_plan = None
        if not hasattr(self, 'current_step_index'):
            self.current_step_index = 0
        if not hasattr(self, 'step_status') or not isinstance(self.step_status, dict):
            self.step_status = {}
        if not hasattr(self, 'current_checkpoint_path'):
            self.current_checkpoint_path = None

        if not hasattr(self, 'context') or not isinstance(self.context, dict):
            self.context = {}
        self.context.setdefault('exception_events', [])
        self.context.setdefault('last_exception_type', None)
        self.context.setdefault('last_handler_action', None)
        self.context.setdefault('last_hitl_triggered', False)

    def _task_fingerprint(self, user_request: str) -> str:
        request = (user_request or '').strip()
        return hashlib.sha256(request.encode('utf-8')).hexdigest()[:16]

    def _get_checkpoint_path(self, user_request: str) -> str:
        task_key = self._task_fingerprint(user_request)
        checkpoint_dir = self.config.get('checkpoint_dir', './checkpoints')
        return str(Path(checkpoint_dir) / f"task_{task_key}.json")

    def _default_step_status(self, steps: List[Dict[str, Any]]) -> Dict[str, str]:
        return {str(i): 'pending' for i in range(len(steps))}

    def _load_checkpoint(self, user_request: str) -> Optional[Dict[str, Any]]:
        self._ensure_phase2_runtime_state()
        checkpoint_path = self._get_checkpoint_path(user_request)
        if not os.path.exists(checkpoint_path):
            return None

        try:
            with open(checkpoint_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Checkpoint JSON corrupted: {checkpoint_path} ({e})")

        expected = self._task_fingerprint(user_request)
        if payload.get('task_fingerprint') != expected:
            logging.warning("Checkpoint fingerprint mismatch, ignore stale checkpoint")
            return None

        plan = payload.get('plan')
        self.task_planner.validate_plan(plan)

        step_status = payload.get('step_status')
        if not isinstance(step_status, dict):
            raise RuntimeError('Checkpoint missing step_status dictionary')

        idx_raw = payload.get('current_step_index', 0)
        try:
            current_step_index = max(0, int(idx_raw))
        except Exception:
            raise RuntimeError(f"Invalid checkpoint current_step_index: {idx_raw}")

        payload['current_step_index'] = current_step_index
        payload['checkpoint_path'] = checkpoint_path
        return payload

    def _save_checkpoint(
        self,
        *,
        last_action: Optional[Dict[str, Any]] = None,
        last_screenshot: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self._ensure_phase2_runtime_state()
        if not self.config.get('enable_checkpoint_recovery', True):
            return

        task_request = self.context.get('task_request', '')
        if not task_request:
            return

        checkpoint_path = self.current_checkpoint_path or self._get_checkpoint_path(task_request)
        self.current_checkpoint_path = checkpoint_path
        Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)

        payload = {
            'task_fingerprint': self._task_fingerprint(task_request),
            'task_request': task_request,
            'plan': self.current_plan,
            'current_step_index': int(self.current_step_index),
            'step_status': dict(self.step_status),
            'last_action': last_action if last_action is not None else self.context.get('last_action'),
            'last_screenshot': last_screenshot if last_screenshot is not None else self.context.get('last_screenshot'),
            'timestamp': datetime.now().isoformat(timespec='seconds'),
        }

        if last_error:
            payload['last_error'] = str(last_error)

        tmp_path = checkpoint_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, checkpoint_path)

    def _clear_checkpoint(self) -> None:
        checkpoint_path = self.current_checkpoint_path
        if not checkpoint_path:
            return
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
            logging.info(f"Checkpoint cleared: {checkpoint_path}")

    def _build_step_prompt(self, full_request: str, step: Dict[str, Any]) -> str:
        instruction = str(step.get('instruction', '')).strip()
        success_criteria = str(step.get('success_criteria', '')).strip()
        return (
            f"总体任务：{full_request}\n"
            f"当前仅执行此步骤：{instruction}\n"
            f"步骤完成标准：{success_criteria}"
        )

    def _prepare_plan_and_recovery(self, user_request: str) -> Tuple[Dict[str, Any], int, bool]:
        self._ensure_phase2_runtime_state()
        checkpoint_path = self._get_checkpoint_path(user_request)
        self.current_checkpoint_path = checkpoint_path

        if self.config.get('enable_checkpoint_recovery', True):
            payload = self._load_checkpoint(user_request)
            if payload:
                self.current_plan = payload['plan']
                self.step_status = payload['step_status']
                self.current_step_index = min(
                    int(payload.get('current_step_index', 0)),
                    len(self.current_plan.get('steps', [])),
                )
                if payload.get('last_action') is not None:
                    self.context['last_action'] = payload.get('last_action')
                if payload.get('last_screenshot') is not None:
                    self.context['last_screenshot'] = payload.get('last_screenshot')
                logging.info(
                    f"Checkpoint recovery enabled: resume from step index {self.current_step_index}"
                )
                return self.current_plan, self.current_step_index, True

        if self.config.get('enable_task_planner', True):
            plan = self.task_planner.build_plan(user_request)
        else:
            plan = {
                'planner_version': 'phase2-v1-disabled',
                'task': user_request,
                'steps': [
                    {
                        'step_name': 'Step 1: 执行任务',
                        'instruction': user_request,
                        'success_criteria': f"已完成：{user_request}。",
                    }
                ],
                'generated_at': datetime.now().isoformat(timespec='seconds'),
            }

        self.task_planner.validate_plan(plan)
        self.current_plan = plan
        self.current_step_index = 0
        self.step_status = self._default_step_status(plan['steps'])
        return plan, 0, False

    def _execute_step_cycles(self, step_prompt: str, max_cycles: int = 15) -> Dict[str, Any]:
        self.context['retry_round'] = 0
        self.context['last_retry_reason'] = None
        self.context['retry_decisions'] = []

        effective_max_cycles = max(1, int(max_cycles))
        min_cycles = max(1, int(self.config.get('continuous_min_cycles', 20)))
        min_minutes = max(0.0, float(self.config.get('continuous_min_minutes', 0) or 0))
        avg_cycle_seconds = max(1.0, float(self.config.get('step_delay', 1.5)) + 8.0)

        if self.context.get('continuous_task'):
            required_by_time = math.ceil((min_minutes * 60.0) / avg_cycle_seconds) if min_minutes > 0 else 0
            planned_min_cycles = max(min_cycles, required_by_time)
            if effective_max_cycles < planned_min_cycles:
                logging.info(
                    f"Continuous task detected: override max_cycles {effective_max_cycles} -> {planned_min_cycles} "
                    f"(min_cycles={min_cycles}, min_minutes={min_minutes}, est_cycle={avg_cycle_seconds:.1f}s)"
                )
                effective_max_cycles = planned_min_cycles

        retry_level, retry_budget = self._resolve_retry_budget(step_prompt)
        logging.info(
            f"Retry budget resolved: level={retry_level}, budget={retry_budget}, cap={self.config.get('retry_budget_cap', 8)}"
        )

        cycles = 0
        step_complete = False
        last_error = None
        consecutive_failures = 0
        last_action: Optional[Dict[str, Any]] = None
        last_screenshot: Optional[str] = None

        while cycles < effective_max_cycles and not step_complete and not self.context.get('stop_requested', False):
            cycles += 1
            self.context['cycle_index'] = cycles
            logging.info(f"\n--- Cycle {cycles}/{effective_max_cycles} ---")

            try:
                result = self.execute_cycle(step_prompt)
                last_action = result.get('planned_action') or result.get('action')
                last_screenshot = result.get('screenshot_path')
                self.context['last_action'] = last_action
                self.context['last_screenshot'] = last_screenshot

                if result.get('hitl_triggered'):
                    last_error = str((last_action or {}).get('message') or 'HITL required by exception handler')
                    logging.error(f"HITL triggered, stop autonomous retries: {last_error}")
                    self._save_checkpoint(
                        last_action=last_action,
                        last_screenshot=last_screenshot,
                        last_error=last_error,
                    )
                    break

                terminate_failure = False
                if result.get('task_complete'):
                    status = str((last_action or {}).get('status', 'success')).lower()
                    if (last_action or {}).get('action') == 'terminate' and status == 'failure':
                        terminate_failure = True
                        last_error = str((last_action or {}).get('message') or 'Step reported failure')
                        logging.warning(f"Step terminate=failure, will retry: {last_error}")
                    else:
                        step_complete = True
                        self._save_checkpoint(last_action=last_action, last_screenshot=last_screenshot)
                        logging.info("✓ Step marked complete by agent")
                        break

                if terminate_failure or not result['success']:
                    if not terminate_failure:
                        last_error = result.get('error', 'Unknown error')
                    failed_action = result.get('action') or result.get('planned_action') or {}
                    retry_reason = self._classify_retry_reason(last_error, failed_action)
                    logging.warning(f"Action failed: reason={retry_reason}, error={last_error}")

                    corrected_result = self._run_failure_feedback_loop(
                        user_request=step_prompt,
                        failed_action=failed_action,
                        failed_error=last_error,
                        cycle_index=cycles,
                    )

                    corrected_action = corrected_result.get('corrected_action') or corrected_result.get('action')
                    if corrected_action is not None:
                        self.context['last_action'] = corrected_action
                    if self.context.get('screenshots'):
                        self.context['last_screenshot'] = self.context['screenshots'][-1]

                    if corrected_result.get('task_complete'):
                        corrected_status = str((corrected_action or {}).get('status', 'success')).lower()
                        if corrected_status == 'failure':
                            last_error = str((corrected_action or {}).get('message') or 'Correction terminate failure')
                            consecutive_failures += 1
                            logging.warning(
                                f"Correction ended with terminate=failure (consecutive={consecutive_failures}/{retry_budget}): {last_error}"
                            )
                            if consecutive_failures >= retry_budget:
                                break
                        else:
                            step_complete = True
                            self._save_checkpoint(
                                last_action=corrected_action,
                                last_screenshot=self.context.get('last_screenshot'),
                            )
                            logging.info("✓ Step completed during correction loop")
                            break
                    elif corrected_result.get('success'):
                        consecutive_failures = 0
                        logging.info(
                            f"Correction applied successfully (reason={corrected_result.get('retry_reason', retry_reason)})."
                        )
                    else:
                        last_error = corrected_result.get('error', last_error)
                        consecutive_failures += 1
                        logging.warning(
                            f"Correction failed (consecutive={consecutive_failures}/{retry_budget}): {last_error}"
                        )
                        if consecutive_failures >= retry_budget:
                            logging.error(f"Max retries exceeded (budget={retry_budget})")
                            break
                else:
                    consecutive_failures = 0

                self._save_checkpoint(
                    last_action=self.context.get('last_action'),
                    last_screenshot=self.context.get('last_screenshot'),
                    last_error=last_error,
                )

            except KeyboardInterrupt:
                logging.info("Task interrupted by user")
                self._save_checkpoint(
                    last_action=self.context.get('last_action'),
                    last_screenshot=self.context.get('last_screenshot'),
                    last_error='KeyboardInterrupt',
                )
                raise
            except Exception as e:
                last_error = str(e)
                retry_reason = self._classify_retry_reason(last_error, None)
                consecutive_failures += 1
                logging.error(
                    f"Cycle error (consecutive={consecutive_failures}/{retry_budget}, reason={retry_reason}): {e}"
                )

                self._save_checkpoint(
                    last_action=self.context.get('last_action'),
                    last_screenshot=self.context.get('last_screenshot'),
                    last_error=last_error,
                )

                if consecutive_failures >= retry_budget:
                    logging.error(f"Max retries exceeded (budget={retry_budget})")
                    break

                time.sleep(2)

        if cycles >= effective_max_cycles and not step_complete and not self.context.get('stop_requested', False):
            elapsed = max(0.0, time.time() - float(self.context.get('task_started_at') or time.time()))

            if self.context.get('continuous_task') and min_minutes > 0 and elapsed < min_minutes * 60:
                logging.info(
                    f"Continuous run reached cycle budget before time target: elapsed={elapsed:.1f}s < {min_minutes*60:.1f}s"
                )
            else:
                logging.info("Max cycles reached, checking if current step is actually complete...")
                screenshot_path = self.capture_screenshot()
                self.context['last_screenshot'] = screenshot_path
                completion_check = self.vl_agent.check_task_completion(
                    screenshot_path,
                    step_prompt,
                    self.context
                )
                if completion_check.get('complete'):
                    step_complete = True
                    logging.info(f"✓ Step verified complete: {completion_check.get('reason')}")
                else:
                    last_error = completion_check.get('reason') or last_error

                self._save_checkpoint(
                    last_action=self.context.get('last_action'),
                    last_screenshot=self.context.get('last_screenshot'),
                    last_error=last_error,
                )

        return {
            'success': bool(step_complete),
            'task_complete': bool(step_complete),
            'cycles': cycles,
            'last_error': last_error,
            'last_action': self.context.get('last_action'),
            'last_screenshot': self.context.get('last_screenshot'),
        }

    def execute_cycle(self, user_request: str) -> Dict[str, Any]:
        """
        Execute a single interaction cycle.

        Priority:
            1) security/blocking exception handling
            2) current step main flow
            3) normal wait / fallback
        """
        screenshot_path = self.capture_screenshot()

        # Exception-first preemption (Phase-3)
        exception_result = self._handle_detected_exception(screenshot_path)
        if exception_result is not None:
            return exception_result

        action = self.vl_agent.analyze_screenshot(
            screenshot_path,
            user_request,
            self.context
        )

        if not action:
            raise Exception("Failed to get action from model")

        # Log model's observation and reasoning
        if 'observation' in action:
            logging.info(f"Model observation: {action['observation']}")
        if 'reasoning' in action:
            logging.info(f"Model reasoning: {action['reasoning']}")

        # Execute the action
        result = self.execute_action(action)
        result['screenshot_path'] = screenshot_path
        result['planned_action'] = action
        result.setdefault('preempted_by_exception', False)
        result.setdefault('exception_type', None)
        result.setdefault('handler_action', None)
        result.setdefault('hitl_triggered', False)
        return result
    
    def execute_task(self, user_request: str, max_cycles: int = 15) -> Dict[str, Any]:
        """
        Execute a complete task through multiple cycles.

        Args:
            user_request: The user's task description
            max_cycles: Maximum number of action cycles per step

        Returns:
            Task result dictionary
        """
        self._ensure_phase2_runtime_state()

        self.context['task_request'] = user_request
        self.context['stop_requested'] = False
        self.context['task_started_at'] = time.time()
        self.context['retry_round'] = 0
        self.context['last_retry_reason'] = None
        self.context['retry_decisions'] = []
        self.context['exception_events'] = []
        self.context['last_exception_type'] = None
        self.context['last_handler_action'] = None
        self.context['last_hitl_triggered'] = False

        req_l = (user_request or '').lower()
        continuous_markers = [
            '刷', '持续', '一直', '不停', 'for a while', 'keep', 'continuously',
            'watch', 'scroll', 'reels', 'shorts'
        ]
        self.context['continuous_task'] = any(k in req_l for k in continuous_markers)

        logging.info('=' * 60)
        logging.info(f'STARTING TASK: {user_request}')
        if self.context['continuous_task']:
            logging.info('Continuous task mode: ON')

        plan, recovered_step_index, resumed = self._prepare_plan_and_recovery(user_request)
        steps = plan.get('steps', [])
        total_steps = len(steps)
        if total_steps <= 0:
            raise RuntimeError('Task planner produced empty steps')

        self.context['task_plan'] = plan

        logging.info(
            f"Task plan ready: steps={total_steps}, resumed={resumed}, start_step={recovered_step_index + 1 if total_steps else 1}"
        )

        total_cycles = 0
        task_complete = False
        last_error = None

        if recovered_step_index >= total_steps:
            task_complete = True
        else:
            for idx in range(recovered_step_index, total_steps):
                if self.context.get('stop_requested', False):
                    break

                self.current_step_index = idx
                step = steps[idx]
                step_key = str(idx)
                step_name = str(step.get('step_name', f'Step {idx + 1}'))
                instruction = str(step.get('instruction', '')).strip()

                current_status = self.step_status.get(step_key, 'pending')
                if current_status == 'done':
                    self.current_step_index = idx + 1
                    continue

                self.step_status[step_key] = 'in_progress'
                self.context['current_step_name'] = step_name
                self.context['current_step_instruction'] = instruction

                logging.info('-' * 60)
                logging.info(f"Executing {step_name} ({idx + 1}/{total_steps})")
                logging.info(f"Instruction: {instruction}")

                self._save_checkpoint(
                    last_action=self.context.get('last_action'),
                    last_screenshot=self.context.get('last_screenshot'),
                )

                step_prompt = self._build_step_prompt(user_request, step)
                step_result = self._execute_step_cycles(step_prompt=step_prompt, max_cycles=max_cycles)
                total_cycles += int(step_result.get('cycles', 0))
                last_error = step_result.get('last_error', last_error)

                if step_result.get('success'):
                    self.step_status[step_key] = 'done'
                    self.current_step_index = idx + 1
                    self._save_checkpoint(
                        last_action=step_result.get('last_action'),
                        last_screenshot=step_result.get('last_screenshot'),
                    )
                    logging.info(f"✓ Step completed: {step_name}")
                    continue

                self.step_status[step_key] = 'failed'
                self.current_step_index = idx
                fail_msg = last_error or f'Step failed: {step_name}'
                self._save_checkpoint(
                    last_action=step_result.get('last_action'),
                    last_screenshot=step_result.get('last_screenshot'),
                    last_error=fail_msg,
                )
                logging.error(f"✗ Step failed: {step_name}, error={fail_msg}")
                break

            task_complete = self.current_step_index >= total_steps

        logging.info("\n" + '=' * 60)
        if self.context.get('stop_requested', False):
            logging.info(f"⏹ TASK STOPPED by user at step {self.current_step_index + 1}/{total_steps}")
            success = True
        elif task_complete:
            logging.info(f"✓ TASK COMPLETED in {total_cycles} cycles ({total_steps} steps)")
            success = True
        elif self.context.get('continuous_task'):
            logging.info(
                f"ℹ CONTINUOUS TASK SESSION ENDED at step {self.current_step_index + 1}/{total_steps}, cycles={total_cycles}"
            )
            if last_error:
                logging.info(f"Last error: {last_error}")
            success = True
        else:
            logging.info(
                f"✗ TASK INCOMPLETE at step {self.current_step_index + 1}/{total_steps}, cycles={total_cycles}"
            )
            if last_error:
                logging.info(f"Last error: {last_error}")
            success = False
        logging.info('=' * 60)

        if task_complete:
            self._clear_checkpoint()

        return {
            'success': success,
            'cycles': total_cycles,
            'task_complete': task_complete,
            'context': self.context,
            'screenshots': self.context['screenshots'],
            'plan': self.current_plan,
            'current_step_index': self.current_step_index,
            'step_status': dict(self.step_status),
            'checkpoint_path': self.current_checkpoint_path,
        }


if __name__ == "__main__":
    # Simple test
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python phone_agent.py 'your task here'")
        sys.exit(1)
    
    task = ' '.join(sys.argv[1:])
    
    # Load config
    config_path = 'config.json'
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        config = {}
    
    # Run task
    agent = PhoneAgent(config)
    result = agent.execute_task(task)
    
    if result['success']:
        print(f"\n✓ Task completed in {result['cycles']} cycles")
    else:
        print(f"\n✗ Task failed after {result['cycles']} cycles")