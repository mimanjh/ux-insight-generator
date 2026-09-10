"""Validate the report contract at the model and API boundaries."""
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Rating = Literal["high", "medium", "low"]


class Citation(BaseModel):
    article_id: str
    title: str
    url: HttpUrl
    relevance_note: str | None


class Finding(BaseModel):
    title: str = Field(min_length=1)
    theme: Literal["trust_and_credibility", "information_hierarchy", "navigation_and_wayfinding", "content_clarity", "interaction_design", "accessibility", "other"]
    severity: Rating
    observation_confidence: Rating
    judgment_confidence: Rating
    what_i_see: str
    why_it_matters: str
    suggested_fix: str
    caveat: str | None
    citation: Citation | None = None
    citation_status: Literal["matched", "no_match", "unavailable"] = "unavailable"


class Analysis(BaseModel):
    what_im_looking_at: str = Field(min_length=1)
    whats_working: list[str]
    findings: list[Finding]
