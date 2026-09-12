"""Errors raised by the deterministic MV prompt planner."""

from ..common.errors import CLNodeError


class MVPlannerError(CLNodeError):
    """Base error for planner input, inference, and output failures."""


class PlanningBriefError(MVPlannerError):
    """The user planning subset is structurally invalid."""


class TimelineParseError(MVPlannerError):
    """The Vocal-to-Prompt timeline cannot be locked safely."""


class PlannerResponseError(MVPlannerError):
    """The model response does not satisfy the planner line protocol."""


class PlannerInferenceStallError(PlannerResponseError):
    """The local planner model stopped producing streamed output."""
