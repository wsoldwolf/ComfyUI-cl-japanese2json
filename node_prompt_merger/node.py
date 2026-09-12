"""ComfyUI wrapper for deterministic reduced-Markdown prompt merging."""

from __future__ import annotations

import logging
from typing import Any

from ..common.logging import log_node_success
from .merger import merge_reduced_markdown_detailed


LOGGER = logging.getLogger("cl_prompt_merger")


class CLPromptMerger:
    """Merge global prompt fragments without an LLM."""

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("merged_markdown",)
    FUNCTION = "merge_prompts"
    CATEGORY = "MiniMax H3/Prompt Tools"
    OUTPUT_NODE = False

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "original_markdown": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Base reduced-Markdown fragment containing only "
                            "Subjects, Retention, and Common sections."
                        ),
                    },
                ),
                "merge_markdown": (
                    "STRING",
                    {
                        "forceInput": True,
                        "tooltip": (
                            "Optional reduced-Markdown fragment to merge into "
                            "the base prompt. Empty input returns the base unchanged."
                        ),
                    },
                ),
                "common_omit_rules": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "tooltip": (
                            "Literal terms separated by | or physical lines. "
                            "Matching original Common bullets are omitted; merge "
                            "Common bullets are always retained."
                        ),
                    },
                ),
            }
        }

    @staticmethod
    def merge_prompts(
        original_markdown: str,
        merge_markdown: str,
        common_omit_rules: str = "",
    ) -> tuple[str]:
        outcome = merge_reduced_markdown_detailed(
            original_markdown,
            merge_markdown,
            common_omit_rules,
        )
        if outcome.passthrough:
            log_node_success(
                LOGGER,
                "cl_prompt_merger",
                "merge input empty; returned %d character(s) unchanged",
                len(outcome.text),
            )
        else:
            log_node_success(
                LOGGER,
                "cl_prompt_merger",
                (
                    "merged %d subject(s), %d retention rule(s), and %d common "
                    "line(s); omitted %d original common line(s); output=%d character(s)"
                ),
                outcome.subject_count,
                outcome.retention_count,
                outcome.common_count,
                outcome.omitted_original_common_count,
                len(outcome.text),
            )
        return (outcome.text,)
