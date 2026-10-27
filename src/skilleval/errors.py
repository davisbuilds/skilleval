"""Errors safe to show at the command boundary."""


class SkillEvalError(Exception):
    """Invalid input or unverifiable evidence; no successful outcome is implied."""
