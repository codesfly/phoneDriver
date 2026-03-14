# skills/base.py
"""Skill 基类和结果类型定义。"""
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillResult:
    """Skill 执行结果的标准化包装。"""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    message: str = ''
    actions_taken: int = 0
    elapsed_ms: int = 0

    def __bool__(self) -> bool:
        return self.success


class BaseSkill:
    """所有 Skill 的抽象基类。

    子类必须实现:
        - name: 技能名称
        - description: 一句话描述（给 TaskPlanner 使用）
        - execute(**kwargs) -> SkillResult
    """

    name: str = 'base'
    description: str = ''
    max_retries: int = 2

    def __init__(self, agent: Any):
        """
        Args:
            agent: PhoneAgent 实例，提供 capture_screenshot / _run_adb_command 等基础能力。
        """
        self.agent = agent
        self.logger = logging.getLogger(f'skill.{self.name}')

    def run(self, **kwargs: Any) -> SkillResult:
        """带重试和计时的执行入口。"""
        start = time.monotonic()
        last_err: Optional[str] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                self.logger.info(f'[{self.name}] attempt {attempt}/{self.max_retries}')
                result = self.execute(**kwargs)
                result.elapsed_ms = int((time.monotonic() - start) * 1000)
                if result.success:
                    self.logger.info(f'[{self.name}] succeeded in {result.elapsed_ms}ms')
                    return result
                last_err = result.message
            except Exception as e:
                last_err = str(e)
                self.logger.warning(f'[{self.name}] attempt {attempt} failed: {last_err}')

        elapsed = int((time.monotonic() - start) * 1000)
        return SkillResult(
            success=False,
            message=f'All {self.max_retries} attempts failed. Last: {last_err}',
            elapsed_ms=elapsed,
        )

    def execute(self, **kwargs: Any) -> SkillResult:
        """子类实现具体技能逻辑。"""
        raise NotImplementedError

    def _capture(self) -> str:
        """快捷截图。"""
        return self.agent.capture_screenshot()

    def _adb(self, args: List[str]) -> str:
        """快捷 ADB 命令。"""
        return self.agent._run_adb_command(args)

    def _analyze(self, screenshot_path: str, prompt: str, **ctx: Any) -> Optional[Dict[str, Any]]:
        """快捷 VLM 分析。"""
        return self.agent.vl_agent.analyze_screenshot(
            screenshot_path, prompt,
            context=ctx.get('context', self.agent.context),
        )
