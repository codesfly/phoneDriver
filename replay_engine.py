import json
import logging
import os
import hashlib
from typing import Dict, Any, List, Optional

class ReplayEngine:
    """
    Smart Replay Engine for PhoneDriver.
    Records task executions and replays them to bypass the Vision-Language Model (VLM),
    reducing token costs and latency.
    """

    def __init__(self, records_dir: str = "records"):
        self.records_dir = records_dir
        if not os.path.exists(self.records_dir):
            os.makedirs(self.records_dir)
        
        # Current recording state
        self.is_recording = False
        self.current_task_name = None
        self.current_record = []
        
        # Current playback state
        self.is_playing = False
        self.playback_record = []
        self.playback_step = 0
        
        logging.info(f"ReplayEngine initialized (records_dir: {self.records_dir})")

    def _get_record_path(self, task_name: str) -> str:
        """Get the file path for a task's record."""
        # Sanitize task name for filename
        safe_name = "".join([c if c.isalnum() else "_" for c in task_name]).strip("_")
        return os.path.join(self.records_dir, f"{safe_name}.json")

    def _calculate_fingerprint(self, ui_tree_data: Optional[List[Dict[str, Any]]]) -> str:
        """
        Calculate a fingerprint for the current UI state.
        Uses SHA-256 for collision resistance and includes element index
        for structural differentiation between pages with similar text.
        """
        if not ui_tree_data:
            return "empty_ui_tree"

        elements_str = ""
        for idx, node in enumerate(ui_tree_data):
            text = node.get('text', '')
            desc = node.get('content_desc', '')
            res_id = node.get('resource_id', '')
            # Include element index as a rudimentary structural signal
            elements_str += f"{idx}:{text}|{desc}|{res_id};\n"

        return hashlib.sha256(elements_str.encode('utf-8')).hexdigest()[:32]

    def start_recording(self, task_name: str):
        """Start recording actions for a new task."""
        self.is_recording = True
        self.current_task_name = task_name
        self.current_record = []
        self.is_playing = False
        logging.info(f"ReplayEngine: Started recording task '{task_name}'")

    def record_step(self, ui_tree_data: Optional[List[Dict[str, Any]]], action: Dict[str, Any], result: Dict[str, Any]):
        """Record a successful step."""
        if not self.is_recording:
            return
            
        fingerprint = self._calculate_fingerprint(ui_tree_data)
        
        step_data = {
            "fingerprint": fingerprint,
            "action": action,
            "expected_result": {
                "success": result.get("success", True)
            }
        }
        self.current_record.append(step_data)
        logging.debug(f"ReplayEngine: Recorded step {len(self.current_record)} (Action: {action.get('action')})")

    def save_record(self):
        """Save the recorded task to disk."""
        if not self.is_recording or not self.current_task_name or not self.current_record:
            return
            
        record_path = self._get_record_path(self.current_task_name)
        try:
            with open(record_path, 'w', encoding='utf-8') as f:
                json.dump({
                    "task_name": self.current_task_name,
                    "steps": self.current_record
                }, f, ensure_ascii=False, indent=2)
            logging.info(f"ReplayEngine: Saved record for '{self.current_task_name}' with {len(self.current_record)} steps")
        except Exception as e:
            logging.error(f"ReplayEngine: Failed to save record: {e}")
            
        self.is_recording = False

    def cancel_recording(self):
        """Cancel the current recording (e.g., if task fails)."""
        if self.is_recording:
            logging.info(f"ReplayEngine: Cancelled recording for '{self.current_task_name}'")
            self.is_recording = False
            self.current_record = []
            self.current_task_name = None

    def try_start_playback(self, task_name: str) -> bool:
        """Attempt to start playback for a task."""
        self.is_recording = False
        self.is_playing = False
        self.playback_record = []
        self.playback_step = 0
        
        record_path = self._get_record_path(task_name)
        if not os.path.exists(record_path):
            return False
            
        try:
            with open(record_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.playback_record = data.get("steps", [])
                
            if self.playback_record:
                self.is_playing = True
                self.current_task_name = task_name
                logging.info(f"ReplayEngine: Started playback for '{task_name}' ({len(self.playback_record)} steps)")
                return True
        except Exception as e:
            logging.error(f"ReplayEngine: Failed to load record for playback: {e}")
            
        return False

    def get_next_action(self, ui_tree_data: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        """
        Get the next action from the playback record if the current UI state matches.
        Returns None to signal a cache miss (fallback to VLM required).
        """
        if not self.is_playing or self.playback_step >= len(self.playback_record):
            # Playback finished or not running
            if self.is_playing:
                logging.info(f"ReplayEngine: Playback finished for '{self.current_task_name}'")
                self.is_playing = False
            return None
            
        expected_step = self.playback_record[self.playback_step]
        expected_fingerprint = expected_step.get("fingerprint")
        current_fingerprint = self._calculate_fingerprint(ui_tree_data)
        
        if expected_fingerprint != current_fingerprint:
            logging.warning(f"ReplayEngine: Cache Miss at step {self.playback_step + 1}. "
                          f"Expected fingerprint {expected_fingerprint[:8]}..., got {current_fingerprint[:8]}... "
                          f"Falling back to VLM.")
            self.cancel_playback()
            return None
            
        # Match! Proceed with cached action
        action = expected_step.get("action")
        logging.info(f"ReplayEngine: Cache Hit at step {self.playback_step + 1}. Action: {action.get('action')}")
        self.playback_step += 1
        
        # Mark as replayed so executor knows not to use VLM
        action['_is_replayed'] = True
        return action
        
    def cancel_playback(self):
        """Cancel the current playback and fallback to VLM flow.
        
        L1-5 fix: automatically starts recording after cancellation so that
        the remaining (non-cached) steps are captured for future replays.
        """
        if self.is_playing:
            task_name = self.current_task_name
            logging.info(f"ReplayEngine: Cancelled playback for '{task_name}'")
            self.is_playing = False
            self.playback_record = []
            self.playback_step = 0
            # Start recording the remainder so this execution is not wasted
            if task_name:
                self.start_recording(task_name)
