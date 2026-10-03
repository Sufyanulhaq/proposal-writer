"""Shared pieces of the two writers: the result shape and the check that a draft does not contain
figures the freelancer never supplied."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class Draft:
    body: str
    engine: str  # "offline", "claude", or "offline (fallback)"
    warnings: list[str] = field(default_factory=list)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}


def ungrounded_figures(draft: str, *sources: str) -> list[str]:
    """Numbers in the draft that appear in neither the job post nor the profile."""
    known = set().union(*(_numbers(s) for s in sources))
    return sorted(_numbers(draft) - known, key=lambda n: (len(n), n))


def warn_about(draft: str, job_text: str, facts: str) -> list[str]:
    figures = ungrounded_figures(draft, job_text, facts)
    if not figures:
        return []
    return [f"The draft mentions {', '.join(figures)}, which is not in your profile or the job post. Check it before you send."]
