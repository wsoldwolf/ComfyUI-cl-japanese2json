"""ComfyUI custom-node registration."""

from .audio_nodes import CLAudioPad
from .nodes import CLJapaneseToJSONGGUF


NODE_CLASS_MAPPINGS = {
    "CLJapaneseToJSONGGUF": CLJapaneseToJSONGGUF,
    "CLAudioPad": CLAudioPad,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CLJapaneseToJSONGGUF": "CL Japanese to JSON (GGUF)",
    "CLAudioPad": "CL Audio Pad (PCM Silence)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
