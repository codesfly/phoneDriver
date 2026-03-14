# skills/screenshot_comparator.py
"""ScreenshotComparator — 对比两张截图的相似度。

使用像素级直方图对比（无需额外依赖），快速判断两帧之间是否发生了
有意义的 UI 变化。用于到底检测、操作验证等场景。
"""
import logging
from typing import Any

from skills.base import BaseSkill, SkillResult


class ScreenshotComparatorSkill(BaseSkill):
    name = 'screenshot_comparator'
    description = '对比两张截图相似度，判断页面是否发生了变化'
    max_retries = 1

    def execute(
        self,
        screenshot_a: str = '',
        screenshot_b: str = '',
        threshold: float = 0.95,
        **kwargs: Any,
    ) -> SkillResult:
        """
        Args:
            screenshot_a: 第一张截图路径
            screenshot_b: 第二张截图路径
            threshold: 相似度阈值 (0-1)，超过此值视为"无变化"
        """
        if not screenshot_a or not screenshot_b:
            return SkillResult(
                success=False,
                message='必须提供 screenshot_a 和 screenshot_b 路径',
            )

        try:
            from PIL import Image

            img_a = Image.open(screenshot_a).convert('RGB')
            img_b = Image.open(screenshot_b).convert('RGB')

            # 统一尺寸
            size = (320, 320)
            img_a = img_a.resize(size, Image.Resampling.LANCZOS)
            img_b = img_b.resize(size, Image.Resampling.LANCZOS)

            # 逐像素比较（归一化差异）
            pixels_a = list(img_a.getdata())
            pixels_b = list(img_b.getdata())

            total_diff = 0
            for pa, pb in zip(pixels_a, pixels_b):
                for ca, cb in zip(pa, pb):
                    total_diff += abs(ca - cb)

            max_diff = len(pixels_a) * 3 * 255  # RGB, 每通道最大差 255
            similarity = 1.0 - (total_diff / max_diff) if max_diff > 0 else 1.0

            is_similar = similarity >= threshold

            return SkillResult(
                success=True,
                data={
                    'similarity': round(similarity, 4),
                    'threshold': threshold,
                    'similar': is_similar,
                    'changed': not is_similar,
                },
                message=(
                    f'相似度 {similarity:.2%}'
                    f' ({"无变化" if is_similar else "有变化"}'
                    f', 阈值 {threshold:.0%})'
                ),
                actions_taken=0,
            )

        except Exception as e:
            return SkillResult(
                success=False,
                message=f'截图对比失败: {e}',
            )
