# qwen_vl_agent.py
import base64
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration  # NOT MoeFor
# from transformers import Qwen3VLMoeForConditionalGeneration, AutoProcessor - This is only for the MoE Variants!!!
from qwen_vl_utils import process_vision_info


class QwenVLAgent:
    """
    Vision-Language agent for mobile GUI automation.

    Supports two inference backends:
    1) Local HF model (`use_remote_api=False`)
    2) OpenAI-compatible remote API (`use_remote_api=True`), e.g. qwen-plus/qwen3.5-plus
    """

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-VL-8B-Instruct",
        device_map: str = "auto",
        dtype: Optional[torch.dtype] = None,
        use_flash_attention: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 512,
        use_remote_api: bool = False,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_model: Optional[str] = None,
        api_timeout: int = 120,
    ) -> None:
        """Initialize the Qwen3-VL agent."""
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.use_remote_api = use_remote_api
        self.api_timeout = api_timeout

        # System prompt matching official format
        self.system_prompt = """# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name": "mobile_use", "description": "Use a touchscreen to interact with a mobile device, and take screenshots.\n* This is an interface to a mobile device with touchscreen. You can perform actions like clicking, typing, swiping, etc.\n* Some applications may take time to start or process actions, so you may need to wait and take successive screenshots to see the results of your actions.\n* The screen's resolution is 999x999.\n* Make sure to click any buttons, links, icons, etc with the cursor tip in the center of the element. Don't click boxes on their edges unless asked.", "parameters": {"properties": {"action": {"description": "The action to perform. The available actions are:\n* `click`: Click the point on the screen with coordinate (x, y).\n* `swipe`: Swipe from the starting point with coordinate (x, y) to the end point with coordinates2 (x2, y2).\n* `type`: Input the specified text into the activated input box.\n* `wait`: Wait specified seconds for the change to happen.\n* `terminate`: Terminate the current task and report its completion status.", "enum": ["click", "swipe", "type", "wait", "terminate"], "type": "string"}, "coordinate": {"description": "(x, y): The x (pixels from the left edge) and y (pixels from the top edge) coordinates to click. Required only by `action=click` and `action=swipe`. Range: 0-999.", "type": "array"}, "coordinate2": {"description": "(x, y): The end coordinates for swipe. Required only by `action=swipe`. Range: 0-999.", "type": "array"}, "text": {"description": "Required only by `action=type`.", "type": "string"}, "time": {"description": "The seconds to wait. Required only by `action=wait`.", "type": "number"}, "status": {"description": "The status of the task. Required only by `action=terminate`.", "type": "string", "enum": ["success", "failure"]}}, "required": ["action"], "type": "object"}}}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call>

Rules:
- Output exactly in the order: Thought, Action, <tool_call>.
- Be brief: one sentence for Thought, one for Action.
- Do not output anything else outside those three parts.
- If finishing, use action=terminate in the tool call.
- For each function call, there must be an "action" key in the "arguments" which denote the type of the action.
- Coordinates are in 999x999 space where (0,0) is top-left and (999,999) is bottom-right."""

        self.model = None
        self.processor = None

        if self.use_remote_api:
            defaults = self._load_openclaw_bailian_defaults()

            self.api_base_url = (api_base_url or defaults.get("base_url") or "").rstrip("/")
            self.api_key = api_key or defaults.get("api_key")
            self.api_model = api_model or defaults.get("model") or "qwen3.5-plus"

            if not self.api_base_url:
                raise ValueError("Remote API mode enabled but api_base_url is missing")
            if not self.api_key:
                raise ValueError("Remote API mode enabled but api_key is missing")

            logging.info(
                f"Using remote OpenAI-compatible model: {self.api_model} @ {self.api_base_url}"
            )
            return

        logging.info(f"Loading local Qwen3-VL model: {model_name}")

        if dtype is None:
            dtype = torch.bfloat16

        # Build model kwargs once; load once
        model_kwargs: Dict[str, Any] = dict(
            torch_dtype=dtype,
            device_map=device_map,
            low_cpu_mem_usage=True,
            # Only for Strix Halo with 96gb set in bios
            # max_memory={0: "90GiB"},
        )

        if use_flash_attention:
            try:
                import flash_attn  # noqa: F401

                model_kwargs["attn_implementation"] = "flash_attention_2"
                logging.info("Flash Attention 2 enabled")
            except Exception:
                logging.warning("flash_attn not installed; using default attention")

        self.model = Qwen3VLForConditionalGeneration.from_pretrained(model_name, **model_kwargs)
        self.processor = AutoProcessor.from_pretrained(model_name)
        # For MoE Models You need to change to self.model=Qwen3VLMoeForConditionalGeneration.from_pretrained
        logging.info("Qwen3-VL local agent initialized successfully")

    @staticmethod
    def _load_openclaw_bailian_defaults() -> Dict[str, Optional[str]]:
        """Load qwen-plus-style defaults from local OpenClaw config if present."""
        config_candidates = [
            os.environ.get("OPENCLAW_CONFIG", ""),
            os.environ.get("OPENCLOW_CONFIG", ""),  # backward-compatible typo fallback
            str(Path.home() / ".openclaw" / "openclaw.json"),
        ]

        candidates = []
        seen = set()
        for raw in config_candidates:
            if not raw or not str(raw).strip():
                continue
            path = Path(raw).expanduser()
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(path)

        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
                providers = data.get("models", {}).get("providers", {})
                bailian = providers.get("bailian", {})
                base_url = bailian.get("baseUrl")
                api_key = bailian.get("apiKey") or bailian.get("key") or bailian.get("token")

                model = None
                for m in bailian.get("models", []):
                    mid = m.get("id", "")
                    if "qwen" in mid.lower() and "plus" in mid.lower():
                        model = mid
                        break
                if not model and bailian.get("models"):
                    model = bailian["models"][0].get("id")

                return {"base_url": base_url, "api_key": api_key, "model": model}
            except Exception:
                continue

        return {"base_url": None, "api_key": None, "model": None}

    @staticmethod
    def _image_to_data_url(image: Image.Image) -> str:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64}"

    def _convert_messages_for_openai(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", [])
            if isinstance(content, str):
                converted.append({"role": role, "content": content})
                continue

            parts = []
            for item in content:
                if item.get("type") == "text":
                    parts.append({"type": "text", "text": item.get("text", "")})
                elif item.get("type") == "image" and "image" in item:
                    img = item["image"]
                    if not isinstance(img, Image.Image):
                        continue
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": self._image_to_data_url(img)},
                        }
                    )

            converted.append({"role": role, "content": parts})

        return converted

    @staticmethod
    def _extract_text_from_openai_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        chunks.append(item.get("text", ""))
                    elif "text" in item and isinstance(item.get("text"), str):
                        chunks.append(item["text"])
                elif isinstance(item, str):
                    chunks.append(item)
            return "\n".join(chunks)
        return str(content)

    def analyze_screenshot(
        self,
        screenshot_path: str,
        user_request: str,
        context: Optional[Dict[str, Any]] = None,
        retry_feedback: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Analyze a phone screenshot and determine the next action."""
        try:
            # Load and resize image to prevent OOM
            image = Image.open(screenshot_path)

            # Resize if too large - keep aspect ratio, max dimension 1280
            max_size = 1280
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
                logging.info(f"Resized image to {image.size}")

            # Build action history
            history = []
            if context:
                previous_actions = context.get("previous_actions", [])
                for i, act in enumerate(previous_actions[-5:], 1):  # Last 5 actions
                    action_type = act.get("action", "unknown")
                    element = act.get("elementName", "")
                    history.append(f"Step {i}: {action_type} {element}".strip())

            history_str = "; ".join(history) if history else "No previous actions"

            # Build user query in official format
            user_query = f"""The user query: {user_request}.
Task progress (You have done the following operation on the current device): {history_str}."""

            if retry_feedback:
                retry_reason = retry_feedback.get("retry_reason", "未知")
                failed_error = retry_feedback.get("failed_error", "")
                failed_action = retry_feedback.get("failed_action", {}) or {}
                retry_round = int(retry_feedback.get("retry_round", 0))
                user_query += (
                    f"\n\nRetry feedback (round={retry_round}): previous action failed."
                    f" Failure reason category: {retry_reason}."
                    f" Failed action: {json.dumps(failed_action, ensure_ascii=False)}."
                    f" Error: {failed_error}."
                    " You must output a corrected action and avoid repeating the same failed action."
                )

            # Messages in official format
            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_query},
                        {"type": "image", "image": image},
                    ],
                },
            ]

            # Generate response
            action = self._generate_action(messages)

            if action:
                logging.info(f"Generated action: {action.get('action', 'unknown')}")
                logging.debug(f"Full action: {json.dumps(action, indent=2)}")

            return action

        except Exception as e:
            logging.error(f"Error analyzing screenshot: {e}", exc_info=True)
            return None

    def _generate_action(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if self.use_remote_api:
            return self._generate_action_remote(messages)
        return self._generate_action_local(messages)

    def _generate_action_local(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Generate an action from local model given messages."""
        try:
            # Use processor's chat template
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)

            # Collect image/video inputs
            image_inputs, video_inputs = process_vision_info(messages)

            # IMPORTANT FIX: avoid empty lists (use None)
            if not image_inputs:
                image_inputs = None
            if not video_inputs:
                video_inputs = None

            inputs = self.processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,  # None when no videos -> skips video path
                padding=True,
                return_tensors="pt",
            )

            # Move to device
            inputs = inputs.to(self.model.device)

            logging.debug("Generating local model response...")

            # Optional: clear cache around generation
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            with torch.no_grad():
                generated_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_tokens,
                    temperature=self.temperature,
                    do_sample=self.temperature > 0,
                    pad_token_id=self.processor.tokenizer.pad_token_id,
                )

            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            # Trim input tokens from output
            generated_ids_trimmed = [
                out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]

            # Decode
            output_text = self.processor.batch_decode(
                generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
            )[0]

            logging.debug(f"Local model output: {output_text}")

            # Parse action
            action = self._parse_action(output_text)
            return action

        except Exception as e:
            logging.error(f"Error generating local action: {e}", exc_info=True)
            return None

    def _generate_action_remote(self, messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Generate an action from remote OpenAI-compatible API."""
        try:
            endpoint = f"{self.api_base_url}/chat/completions"
            payload = {
                "model": self.api_model,
                "messages": self._convert_messages_for_openai(messages),
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            logging.debug(f"Calling remote model {self.api_model} @ {endpoint}")
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=self.api_timeout)

            if resp.status_code >= 400:
                logging.error(f"Remote API error {resp.status_code}: {resp.text[:1000]}")
                return None

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                logging.error(f"Remote API returned no choices: {data}")
                return None

            message = choices[0].get("message", {})

            # Prefer structured tool_calls first for API compatibility.
            tool_calls = message.get("tool_calls") or []
            output_text = ""
            if isinstance(tool_calls, list) and tool_calls:
                tc = tool_calls[0] or {}
                fn = tc.get("function") or {}
                fname = fn.get("name")
                fargs = fn.get("arguments")
                if fname and fargs is not None:
                    if not isinstance(fargs, str):
                        fargs = json.dumps(fargs, ensure_ascii=False)
                    output_text = f"<tool_call>\n{{\"name\": \"{fname}\", \"arguments\": {fargs}}}\n</tool_call>"
                    logging.debug("Remote API tool_calls detected; using structured function call")

            # Fallback 1: plain content
            if not output_text:
                output_text = self._extract_text_from_openai_content(message.get("content", ""))

            # Fallback 2: reasoning content
            if not output_text:
                output_text = self._extract_text_from_openai_content(message.get("reasoning_content", ""))
                if output_text:
                    logging.warning("Remote API returned empty content; falling back to reasoning_content")

            if not output_text:
                logging.error(f"Remote API empty content: {message}")
                return None

            logging.debug(f"Remote model output: {output_text}")
            action = self._parse_action(output_text)
            return action

        except Exception as e:
            logging.error(f"Error generating remote action: {e}", exc_info=True)
            return None

    def _parse_action(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse action from model output in official format."""
        try:
            # Extract tool_call XML content
            match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.DOTALL)

            # Fallback: sometimes model returns raw JSON directly
            if not match:
                raw_json_match = re.search(r"\{\s*\"name\"\s*:\s*\"mobile_use\".*\}", text, re.DOTALL)
                if raw_json_match:
                    tool_call_json = raw_json_match.group(0)
                else:
                    logging.error("No <tool_call> tags or raw tool JSON found in output")
                    logging.debug(f"Output text: {text}")
                    return None
            else:
                tool_call_json = match.group(1)

            tool_call = json.loads(tool_call_json)

            # Extract arguments
            if "arguments" not in tool_call:
                logging.error("No 'arguments' in tool_call")
                return None

            args = tool_call["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError as e:
                    logging.error(f"Failed to parse tool_call.arguments JSON string: {e}")
                    return None
            if not isinstance(args, dict):
                logging.error(f"tool_call.arguments must be an object, got: {type(args).__name__}")
                return None

            action_type = args.get("action")
            if not action_type:
                logging.error("No 'action' in arguments")
                return None

            # Convert to our internal format
            action: Dict[str, Any] = {"action": action_type}

            # Handle coordinates (convert from 999x999 space to normalized 0-1)
            if "coordinate" in args:
                coord = args["coordinate"]
                action["coordinates"] = [coord[0] / 999.0, coord[1] / 999.0]

            if "coordinate2" in args:
                coord2 = args["coordinate2"]
                action["coordinate2"] = [coord2[0] / 999.0, coord2[1] / 999.0]

            # Handle swipe - convert to direction for compatibility
            if action_type == "swipe" and "coordinates" in action and "coordinate2" in action:
                start = action["coordinates"]
                end = action["coordinate2"]
                dx = end[0] - start[0]
                dy = end[1] - start[1]
                if abs(dy) > abs(dx):
                    action["direction"] = "down" if dy > 0 else "up"
                else:
                    action["direction"] = "right" if dx > 0 else "left"

            # Map action names
            if action_type == "click":
                action["action"] = "tap"  # our internal name

            # Copy other fields
            if "text" in args:
                action["text"] = args["text"]
            if "time" in args:
                action["waitTime"] = int(float(args["time"]) * 1000)  # ms
            if "status" in args:
                action["status"] = args["status"]
                action["message"] = f"Task {args['status']}"

            # Extract thought/action description
            thought_match = re.search(r"Thought:\s*(.+?)(?:\n|$)", text)
            action_match = re.search(r"Action:\s*(.+?)(?:\n|$)", text)
            if thought_match:
                action["reasoning"] = thought_match.group(1).strip().strip('"')
            if action_match:
                action["observation"] = action_match.group(1).strip().strip('"')

            # Validate essentials
            if action["action"] == "tap" and "coordinates" not in action:
                logging.error("Tap action missing coordinates")
                return None
            if action["action"] == "type" and "text" not in action:
                logging.error("Type action missing text")
                return None

            # Normalize provider-specific aliases
            if action.get("action") == "system":
                txt = str(action.get("text", "")).strip().lower()
                if txt in {"home", "back", "recent", "recents", "app_switch", "power", "menu", "enter"}:
                    # pass-through, handled by PhoneAgent._execute_system
                    pass
                else:
                    logging.error(f"Unsupported system action text: {txt}")
                    return None

            return action

        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON from tool_call: {e}")
            logging.debug(f"Text: {text}")
            return None
        except Exception as e:
            logging.error(f"Error parsing action: {e}")
            logging.debug(f"Text: {text}")
            return None

    def check_task_completion(
        self,
        screenshot_path: str,
        user_request: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Ask the model if the task has been completed."""
        try:
            # Load and resize image
            image = Image.open(screenshot_path)
            max_size = 1280
            if max(image.size) > max_size:
                ratio = max_size / max(image.size)
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            completion_query = f"""The user query: {user_request}.

You have completed {len(context.get('previous_actions', []))} actions.

Look at the current screen and determine: Has the task been completed successfully?

If complete, use action=terminate with status=\"success\".
If not complete, explain what still needs to be done and use action=terminate with status=\"failure\"."""

            messages = [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": self.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": completion_query},
                        {"type": "image", "image": image},
                    ],
                },
            ]

            action = self._generate_action(messages)

            if action and action.get("action") == "terminate":
                return {
                    "complete": action.get("status") == "success",
                    "reason": action.get("message", ""),
                    "confidence": 0.9 if action.get("status") == "success" else 0.7,
                }

            return {"complete": False, "reason": "Unable to verify", "confidence": 0.0}

        except Exception as e:
            logging.error(f"Error checking completion: {e}")
            return {"complete": False, "reason": f"Error: {str(e)}", "confidence": 0.0}
