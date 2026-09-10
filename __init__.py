"""ComfyUI custom-node registration."""

from .node_audio_pad.node import CLAudioPad, CLAudioPadPair
from .node_japanese_to_json.node import CLJapaneseToJSONGGUF
from .node_text_file.node import CLLoadTextFile
from .node_vocal_to_prompt_segments.node import CLVocalToPromptSegments


NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
    "CLAudioPad": CLAudioPad,
    "CLAudioPadPair": CLAudioPadPair,
    "CLVocalToPromptSegments": CLVocalToPromptSegments,
    "CLLoadTextFile": CLLoadTextFile,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
    "CLAudioPad": "CL Audio Pad (PCM Silence)",
    "CLAudioPadPair": "CL Audio Pad Pair (PCM Silence)",
    "CLVocalToPromptSegments": "CL Vocal to Prompt Segments",
    "CLLoadTextFile": "CL Load Text File (Drag & Drop)",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
