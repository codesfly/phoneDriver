# skills/smart_scroll.py
"""SmartScroll — 智能滚动查找目标元素。

在当前页面向指定方向滚动，每次滚动后用 VLM 检测目标元素是否出现，
直到找到或达到最大滚动次数。支持到底检测（连续两帧无变化即判定到底）。
"""
import logging
from typing import Any

from skills.base import BaseSkill, SkillResult


class SmartScrollSkill(BaseSkill):
    name = 'smart_scroll'
    description = '智能滚动查找目标元素，支持方向、最大次数、到底检测'
    max_retries = 1  # 滚动本身自带内循环，不需外层重试

    def execute(
        self,
        target: str = '',
        direction: str = 'up',
        max_scrolls: int = 10,
        **kwargs: Any,
    ) -> SkillResult:
        """
        Args:
            target: 要查找的目标描述（如 "确认按钮"、"张三的聊天记录"）
            direction: 滚动方向 up/down/left/right
            max_scrolls: 最大滚动次数
        """
        if not target:
            return SkillResult(success=False, message='target 参数不能为空')

        prev_screenshot = None
        for i in range(max_scrolls):
            # 截图并让 VLM 判断目标是否可见
            screenshot = self._capture()

            prompt = (
                f'Look at the current screen carefully. '
                f'Is the element "{target}" visible on this screen? '
                f'If YES, respond with action=click on its center coordinate. '
                f'If NO, respond with action=wait (I will scroll for you).'
            )
            action = self._analyze(screenshot, prompt)

            if action and action.get('action') in ('tap', 'click'):
                self.logger.info(f'Target "{target}" found after {i} scrolls')
                # 执行点击
                self.agent.execute_action(action)
                return SkillResult(
                    success=True,
                    data={'found_at_scroll': i, 'action': action},
                    message=f'目标 "{target}" 在第 {i} 次滚动后找到并点击',
                    actions_taken=i + 1,
                )

            # 到底检测：对比前后截图
            if prev_screenshot:
                from skills.screenshot_comparator import ScreenshotComparatorSkill
                comp = ScreenshotComparatorSkill(self.agent)
                comp_result = comp.execute(
                    screenshot_a=prev_screenshot,
                    screenshot_b=screenshot,
                    threshold=0.98,
                )
                if comp_result.success and comp_result.data.get('similar', False):
                    self.logger.info(f'Screen unchanged after scroll — reached end')
                    return SkillResult(
                        success=False,
                        message=f'已滚动到底，未找到 "{target}"',
                        actions_taken=i + 1,
                    )

            prev_screenshot = screenshot

            # 执行滚动
            self.agent.execute_action({
                'action': 'swipe',
                'direction': direction,
            })
            self.logger.debug(f'Scroll {direction} #{i + 1}')

        return SkillResult(
            success=False,
            message=f'滚动 {max_scrolls} 次后仍未找到 "{target}"',
            actions_taken=max_scrolls,
        )
