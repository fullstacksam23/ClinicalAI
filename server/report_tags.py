"""Validation for patient-created report tags."""

from pydantic import BaseModel, Field, field_validator


def report_type_tags(report_type: str | None) -> list[str]:
    """Use an extracted report type as an initial tag when it fits the tag rules."""
    tag = (report_type or "").strip()
    if not tag or len(tag) > 32 or "," in tag or any(ord(char) < 32 for char in tag):
        return []
    return [tag]


class ReportTagsRequest(BaseModel):
    tags: list[str] = Field(max_length=10)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        tags = []
        seen = set()
        for raw in value:
            tag = raw.strip()
            if not tag or len(tag) > 32 or "," in tag or any(ord(char) < 32 for char in tag):
                raise ValueError("Tags must be 1–32 characters and cannot contain commas or control characters.")
            key = tag.casefold()
            if key not in seen:
                tags.append(tag)
                seen.add(key)
        return tags
