"""Tests for the dataclass models."""

from datetime import datetime

import pytest

from stumbleupon.models import Clip, Posting, Site, Sound


def test_site_construction_minimal() -> None:
    site = Site(id=1, url="https://example.com")
    assert site.id == 1
    assert site.url == "https://example.com"
    assert site.title is None
    assert site.status == "fresh"
    assert isinstance(site.discovered_at, datetime)


def test_site_status_must_be_known() -> None:
    with pytest.raises(ValueError, match="status"):
        Site(id=1, url="https://example.com", status="bogus")


def test_clip_status_must_be_known() -> None:
    with pytest.raises(ValueError, match="status"):
        Clip(id=1, site_id=1, status="bogus")


def test_clip_uses_edited_caption_when_present() -> None:
    clip = Clip(
        id=1,
        site_id=1,
        caption="original",
        edited_caption="human-tweaked",
    )
    assert clip.effective_caption == "human-tweaked"


def test_clip_falls_back_to_caption_when_no_edit() -> None:
    clip = Clip(id=1, site_id=1, caption="original")
    assert clip.effective_caption == "original"


def test_sound_round_robin_score() -> None:
    sound = Sound(id=1, tiktok_sound_id="abc", title="x", artist="y", trending_score=85.5)
    assert sound.trending_score == 85.5


def test_posting_required_fields() -> None:
    posting = Posting(id=1, clip_id=1, platform="tiktok", status="queued")
    assert posting.platform == "tiktok"
    assert posting.status == "queued"
