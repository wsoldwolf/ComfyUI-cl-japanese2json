"""ComfyUI custom-node registration."""

from .audio_nodes import CLAudioPad
from .nodes import CLJapaneseToJSONGGUF
from .text_file_nodes import CLLoadTextFile
from .vocal_prompt_nodes import CLVocalToPromptSegments


NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
    "CLAudioPad": CLAudioPad,
    "CLVocalToPromptSegments": CLVocalToPromptSegments,
    "CLLoadTextFile": CLLoadTextFile,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
    "CLAudioPad": "CL Audio Pad (PCM Silence)",
    "CLVocalToPromptSegments": "CL Vocal to Prompt Segments",
    "CLLoadTextFile": "CL Load Text File (Drag & Drop)",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
