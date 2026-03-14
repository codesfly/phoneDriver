# skills/ocr_extractor.py
"""OCRExtractor — 利用 VLM 从屏幕提取结构化文本。

通过让大模型阅读截图中指定区域/全屏的文字内容，返回纯文本或
键值对结构。适用于读取余额、状态信息、验证码等场景。
"""
from typing import Any

from skills.base import BaseSkill, SkillResult


class OCRExtractorSkill(BaseSkill):
    name = 'ocr_extractor'
    description = '利用 VLM 从屏幕截图中提取文字内容（余额/状态/验证码等）'
    max_retries = 2

    def execute(
        self,
        query: str = '',
        region: str = 'full',
        output_format: str = 'text',
        **kwargs: Any,
    ) -> SkillResult:
        """
        Args:
            query: 要提取的内容描述（如 "账户余额"、"验证码"、"所有列表项文字"）
            region: 屏幕区域 full/top/bottom/left/right
            output_format: 返回格式 text（纯文本）/ json（键值对）
        """
        if not query:
            return SkillResult(success=False, message='query 参数不能为空')

        screenshot = self._capture()

        region_hint = ''
        if region != 'full':
            region_hint = f' Focus on the {region} part of the screen.'

        format_hint = ''
        if output_format == 'json':
            format_hint = ' Return the result as a JSON object with key-value pairs.'

        prompt = (
            f'Read the screen carefully.{region_hint} '
            f'Extract the following information: "{query}".{format_hint} '
            f'Return ONLY the extracted text/data in your Thought section, '
            f'then use action=terminate with status=success.'
        )

        action = self._analyze(screenshot, prompt)

        if action:
            # 从模型的 reasoning/observation 中提取文字
            extracted = (
                action.get('reasoning', '') or
                action.get('observation', '') or
                action.get('message', '')
            )
            if extracted:
                return SkillResult(
                    success=True,
                    data={'text': extracted, 'query': query, 'region': region},
                    message=extracted,
                    actions_taken=1,
                )

        return SkillResult(
            success=False,
            message=f'无法从屏幕提取 "{query}"',
            actions_taken=1,
        )
