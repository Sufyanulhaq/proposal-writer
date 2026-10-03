"""What the freelancer tells the tool about themselves. A draft may only claim what is in here."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Profile(BaseModel):
    name: str = Field(default="", max_length=80)
    headline: str = Field(default="", max_length=120)
    skills: list[str] = Field(default_factory=list, max_length=30)
    proof_points: list[str] = Field(default_factory=list, max_length=8)
    tone: Literal["friendly", "formal"] = "friendly"

    def cleaned(self) -> "Profile":
        return Profile(
            name=self.name.strip(),
            headline=self.headline.strip(),
            skills=[s.strip()[:40] for s in self.skills if s.strip()],
            proof_points=[p.strip()[:280] for p in self.proof_points if p.strip()],
            tone=self.tone,
        )

    def facts(self) -> str:
        """Everything the profile says, as text. Used to check a draft has not made things up."""
        return " ".join([self.name, self.headline, *self.skills, *self.proof_points])
