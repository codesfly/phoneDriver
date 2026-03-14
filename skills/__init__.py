# skills/__init__.py
"""PhoneDriver Skill 框架 — 可复用的 LLM 驱动操作模块。

每个 Skill 封装一组原子操作（截图、解析、执行）为可被 TaskPlanner
或用户直接调用的高阶能力，内部自带重试和验证逻辑。

用法:
    from skills import SkillRegistry
    registry = SkillRegistry(agent)
    result = registry.run('smart_scroll', target='找到确认按钮')
"""
from skills.base import BaseSkill, SkillResult
from skills.registry import SkillRegistry

__all__ = ['BaseSkill', 'SkillResult', 'SkillRegistry']
