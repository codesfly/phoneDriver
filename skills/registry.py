# skills/registry.py
"""Skill 注册中心 — 按名称查找和调用 Skill。"""
import logging
from typing import Any, Dict, Optional, Type

from skills.base import BaseSkill, SkillResult


class SkillRegistry:
    """统一管理所有 Skill 的注册与调用。

    用法:
        registry = SkillRegistry(agent)
        result = registry.run('smart_scroll', target='确认按钮')
    """

    def __init__(self, agent: Any):
        self.agent = agent
        self._skills: Dict[str, BaseSkill] = {}
        self._auto_register()

    def _auto_register(self) -> None:
        """自动注册内置 Skill。"""
        from skills.smart_scroll import SmartScrollSkill
        from skills.app_launcher import AppLauncherSkill
        from skills.ocr_extractor import OCRExtractorSkill
        from skills.form_filler import FormFillerSkill
        from skills.screenshot_comparator import ScreenshotComparatorSkill

        for cls in [
            SmartScrollSkill,
            AppLauncherSkill,
            OCRExtractorSkill,
            FormFillerSkill,
            ScreenshotComparatorSkill,
        ]:
            self.register(cls)

    def register(self, skill_cls: Type[BaseSkill]) -> None:
        """注册一个 Skill 类。"""
        instance = skill_cls(self.agent)
        self._skills[instance.name] = instance
        logging.debug(f"Skill registered: {instance.name}")

    def run(self, skill_name: str, **kwargs: Any) -> SkillResult:
        """按名称调用 Skill。"""
        skill = self._skills.get(skill_name)
        if not skill:
            return SkillResult(
                success=False,
                message=f"Unknown skill: '{skill_name}'. Available: {list(self._skills.keys())}",
            )
        return skill.run(**kwargs)

    def get(self, name: str) -> Optional[BaseSkill]:
        return self._skills.get(name)

    def list_skills(self) -> Dict[str, str]:
        """返回所有已注册 Skill 名称及描述。"""
        return {name: s.description for name, s in self._skills.items()}
