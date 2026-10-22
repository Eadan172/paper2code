"""语音合成适配层（SiliconFlow 优先，失败回退 edge-tts / SAPI / 占位）。"""

from .base import TTSEngine, get_tts  # noqa: F401

__all__ = ["TTSEngine", "get_tts"]
