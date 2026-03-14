# skills/app_launcher.py
"""AppLauncher — 按名称打开手机 App。

策略优先级：
1. 通过 ADB am start 直接启动（包名匹配）
2. 如果包名未知，使用 Home → 上滑打开抽屉 → VLM 定位图标
"""
import logging
import re
from typing import Any, Optional

from skills.base import BaseSkill, SkillResult


class AppLauncherSkill(BaseSkill):
    name = 'app_launcher'
    description = '按名称打开手机 App，支持 ADB 直启和视觉搜索'
    max_retries = 2

    # 常见 App 包名映射（中英文）
    KNOWN_PACKAGES = {
        '微信': 'com.tencent.mm',
        'wechat': 'com.tencent.mm',
        '抖音': 'com.ss.android.ugc.aweme',
        'tiktok': 'com.ss.android.ugc.aweme',
        '淘宝': 'com.taobao.taobao',
        '支付宝': 'com.eg.android.AlipayGphone',
        'alipay': 'com.eg.android.AlipayGphone',
        '设置': 'com.android.settings',
        'settings': 'com.android.settings',
        '相机': 'com.android.camera',
        'camera': 'com.android.camera',
        'chrome': 'com.android.chrome',
        '浏览器': 'com.android.chrome',
        'youtube': 'com.google.android.youtube',
        '地图': 'com.autonavi.minimap',
        '高德': 'com.autonavi.minimap',
        '百度': 'com.baidu.searchbox',
        '京东': 'com.jingdong.app.mall',
        '拼多多': 'com.xunmeng.pinduoduo',
        '美团': 'com.sankuai.meituan',
        '饿了么': 'me.ele',
        '快手': 'com.smile.gifmaker',
        '小红书': 'com.xingin.xhs',
        'bilibili': 'tv.danmaku.bili',
        'b站': 'tv.danmaku.bili',
        '哔哩哔哩': 'tv.danmaku.bili',
        'telegram': 'org.telegram.messenger',
        'whatsapp': 'com.whatsapp',
    }

    def execute(self, app_name: str = '', **kwargs: Any) -> SkillResult:
        if not app_name:
            return SkillResult(success=False, message='app_name 参数不能为空')

        app_lower = app_name.strip().lower()

        # 1. 尝试直接包名启动
        package = self.KNOWN_PACKAGES.get(app_lower)
        if not package:
            package = self._search_package(app_lower)

        if package:
            result = self._launch_by_package(package)
            if result.success:
                return result

        # 2. 回退到 VLM 视觉搜索
        return self._launch_by_visual(app_name)

    def _search_package(self, keyword: str) -> Optional[str]:
        """通过 pm list 搜索匹配的包名。"""
        try:
            output = self._adb(['shell', 'pm', 'list', 'packages'])
            for line in output.strip().split('\n'):
                pkg = line.replace('package:', '').strip()
                if keyword in pkg.lower():
                    return pkg
        except Exception:
            pass
        return None

    def _launch_by_package(self, package: str) -> SkillResult:
        """通过 ADB monkey 命令启动 App。"""
        try:
            self._adb([
                'shell', 'monkey', '-p', package,
                '-c', 'android.intent.category.LAUNCHER', '1',
            ])
            import time
            time.sleep(2)  # 等待 App 启动

            # 验证是否成功
            output = self._adb(['shell', 'dumpsys', 'window', 'windows'])
            if package in output:
                return SkillResult(
                    success=True,
                    data={'package': package, 'method': 'adb_direct'},
                    message=f'App {package} 已通过 ADB 启动',
                    actions_taken=1,
                )
        except Exception as e:
            self.logger.warning(f'ADB launch failed for {package}: {e}')

        return SkillResult(success=False, message=f'ADB 启动 {package} 失败')

    def _launch_by_visual(self, app_name: str) -> SkillResult:
        """通过回到桌面 + VLM 视觉定位启动 App。"""
        # 回到桌面
        self._adb(['shell', 'input', 'keyevent', '3'])
        import time
        time.sleep(1)

        # 上滑打开应用抽屉
        self.agent.execute_action({'action': 'swipe', 'direction': 'up'})
        time.sleep(1)

        # VLM 查找 App 图标
        screenshot = self._capture()
        prompt = (
            f'Find the app icon labeled "{app_name}" on this screen. '
            f'If found, click on it. If not found, use action=wait.'
        )
        action = self._analyze(screenshot, prompt)

        if action and action.get('action') in ('tap', 'click'):
            self.agent.execute_action(action)
            time.sleep(2)
            return SkillResult(
                success=True,
                data={'method': 'visual_search'},
                message=f'App "{app_name}" 通过视觉搜索打开',
                actions_taken=3,
            )

        return SkillResult(
            success=False,
            message=f'未在桌面/应用抽屉找到 "{app_name}"',
            actions_taken=3,
        )
