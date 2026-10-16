"""Project-local ComfyUI V3 extension. No model or path registry mutation."""

from comfy_api.latest import ComfyExtension

from .nodes import VoiceMotionAnalyze, VoiceMotionProject
from .mv_nodes import VoiceMotionMVCreate


class VoiceMotionExtension(ComfyExtension):
    async def get_node_list(self):
        return [VoiceMotionAnalyze, VoiceMotionProject, VoiceMotionMVCreate]


async def comfy_entrypoint():
    return VoiceMotionExtension()


__all__ = ["comfy_entrypoint", "VoiceMotionAnalyze", "VoiceMotionProject", "VoiceMotionMVCreate"]
