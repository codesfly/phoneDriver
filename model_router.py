# model_router.py
"""Intelligent model routing for PhoneDriver (LLM optimization #5).

Routes VLM requests to the most cost-effective model based on task complexity,
retry state, and action history. Simple actions use a smaller/cheaper model;
complex reasoning uses the most capable model.
"""
import logging
from typing import Any, Dict, Optional, Tuple


class ModelRouter:
    """Routes requests to appropriate models based on complexity."""

    # Default model tiers (can be overridden via config)
    DEFAULT_TIERS = {
        'simple': {
            'model': 'qwen-vl-plus',
            'max_tokens': 256,
            'temperature': 0.05,
        },
        'medium': {
            'model': 'qwen-vl-max',
            'max_tokens': 512,
            'temperature': 0.1,
        },
        'complex': {
            'model': 'qwen3.5-vl-plus',
            'max_tokens': 1024,
            'temperature': 0.15,
        },
    }

    # Action types that are inherently simple
    SIMPLE_FOLLOW_UPS = {
        'wait', 'back', 'scroll', 'swipe',
    }

    def __init__(self, tiers: Optional[Dict[str, Dict[str, Any]]] = None):
        self.tiers = tiers or self.DEFAULT_TIERS
        self._stats: Dict[str, int] = {'simple': 0, 'medium': 0, 'complex': 0}

    def route(
        self,
        context: Dict[str, Any],
        retry_feedback: Optional[Dict[str, Any]] = None,
        user_request: str = '',
    ) -> Tuple[str, Dict[str, Any]]:
        """Determine which model tier to use.

        Returns:
            (tier_name, tier_config) e.g. ('simple', {'model': '...', ...})
        """
        tier = self._classify(context, retry_feedback, user_request)
        config = self.tiers.get(tier, self.tiers.get('medium', {}))
        self._stats[tier] = self._stats.get(tier, 0) + 1
        logging.debug(f"ModelRouter: routed to tier='{tier}' model={config.get('model')}")
        return tier, config

    def _classify(
        self,
        context: Dict[str, Any],
        retry_feedback: Optional[Dict[str, Any]],
        user_request: str,
    ) -> str:
        """Classify request complexity."""
        # 1. Retries always go to complex model (needs best reasoning)
        if retry_feedback:
            retry_round = int(retry_feedback.get('retry_round', 0))
            if retry_round >= 2:
                return 'complex'
            return 'medium'

        # 2. Check last action — simple follow-ups stay simple
        last_action = context.get('last_action')
        if isinstance(last_action, dict):
            last_type = last_action.get('action', '')
            if last_type in self.SIMPLE_FOLLOW_UPS:
                return 'simple'

        # 3. First cycle of a task always needs full reasoning
        cycle_index = context.get('cycle_index', 0)
        if isinstance(cycle_index, int) and cycle_index <= 1:
            return 'complex'

        # 4. Continuous tasks (e.g. "刷视频") are inherently simple after setup
        if context.get('continuous_task'):
            return 'simple'

        # 5. Default: medium
        return 'medium'

    def get_stats(self) -> Dict[str, int]:
        """Return routing statistics for monitoring."""
        return dict(self._stats)
