# skills/form_filler.py
"""FormFiller — 自动填充表单字段。

接收字段名 → 值的映射，依次定位每个输入框并填入对应内容。
支持自动清空原有内容、多字段批量填写。
"""
import time
from typing import Any, Dict, List

from skills.base import BaseSkill, SkillResult


class FormFillerSkill(BaseSkill):
    name = 'form_filler'
    description = '按字段名自动定位并填写表单，支持多字段批量输入'
    max_retries = 1

    def execute(
        self,
        fields: Dict[str, str] | None = None,
        clear_existing: bool = True,
        **kwargs: Any,
    ) -> SkillResult:
        """
        Args:
            fields: 字段名 → 值的映射，如 {"用户名": "test", "密码": "123456"}
            clear_existing: 填写前是否清空已有内容
        """
        if not fields:
            return SkillResult(success=False, message='fields 参数不能为空')

        filled: List[str] = []
        failed: List[str] = []

        for field_name, value in fields.items():
            success = self._fill_field(field_name, value, clear_existing)
            if success:
                filled.append(field_name)
            else:
                failed.append(field_name)

        all_ok = len(failed) == 0
        return SkillResult(
            success=all_ok,
            data={'filled': filled, 'failed': failed},
            message=(
                f'已填写 {len(filled)} 个字段' +
                (f'，{len(failed)} 个失败: {failed}' if failed else '')
            ),
            actions_taken=len(filled) + len(failed),
        )

    def _fill_field(self, field_name: str, value: str, clear: bool) -> bool:
        """定位并填写单个字段。"""
        try:
            screenshot = self._capture()

            prompt = (
                f'Find the input field labeled or described as "{field_name}" '
                f'on this screen and click on it to activate it.'
            )
            action = self._analyze(screenshot, prompt)

            if not action or action.get('action') not in ('tap', 'click'):
                self.logger.warning(f'Cannot locate field: {field_name}')
                return False

            # 点击激活输入框
            self.agent.execute_action(action)
            time.sleep(0.5)

            # 清空现有内容（全选 + 删除）
            if clear:
                # Ctrl+A 全选
                self._adb(['shell', 'input', 'keyevent', '29', '113'])  # CTRL_LEFT + A
                time.sleep(0.2)
                self._adb(['shell', 'input', 'keyevent', '67'])  # DEL
                time.sleep(0.2)

            # 输入新内容
            self.agent.execute_action({
                'action': 'type',
                'text': value,
            })
            time.sleep(0.3)

            self.logger.info(f'Filled "{field_name}" = "{value[:20]}..."')
            return True

        except Exception as e:
            self.logger.error(f'Failed to fill "{field_name}": {e}')
            return False
