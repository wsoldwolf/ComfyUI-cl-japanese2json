"""ComfyUI custom-node registration."""

from .node_audio_pad.node import CLAudioPad, CLAudioPadPair
from .node_japanese_to_json.node import CLJapaneseToJSONGGUF
from .node_mv_prompt_planner.node import CLMVPromptPlannerGGUF
from .node_scene_limiter.node import CLSceneLimiter
from .node_text_file.node import CLLoadTextFile
from .node_vocal_to_prompt_segments.node import CLVocalToPromptSegments
from .node_vision_analyzer.node import CLImageAnalyzerVisionGGUF


NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
    "CLMVPromptPlannerGGUF": CLMVPromptPlannerGGUF,
    "CLSceneLimiter": CLSceneLimiter,
    "CLAudioPad": CLAudioPad,
    "CLAudioPadPair": CLAudioPadPair,
    "CLVocalToPromptSegments": CLVocalToPromptSegments,
    "CLLoadTextFile": CLLoadTextFile,
    "CLImageAnalyzerVisionGGUF": CLImageAnalyzerVisionGGUF,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
    "CLMVPromptPlannerGGUF": "CL MV Prompt Planner (GGUF)",
    "CLSceneLimiter": "CL Scene Limiter (Reduced Markdown)",
    "CLAudioPad": "CL Audio Pad (PCM Silence)",
    "CLAudioPadPair": "CL Audio Pad Pair (PCM Silence)",
    "CLVocalToPromptSegments": "CL Vocal to Prompt Segments",
    "CLLoadTextFile": "CL Load Text File (Drag & Drop)",
    "CLImageAnalyzerVisionGGUF": "CL Image Analyzer (Vision GGUF)",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
