"""Ingest defaults: embed on by default, apply_default transformations honored."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.models import SourceCreate
from api.source_intake_service import default_transformation_ids


def test_embed_defaults_true():
    """Sources embed unless the caller opts out — unembedded sources are
    invisible to search, which bulk API ingests hit for weeks unnoticed."""
    assert SourceCreate(type="link", url="https://example.com").embed is True


def test_embed_still_optable_out():
    assert (
        SourceCreate(type="link", url="https://example.com", embed=False).embed is False
    )


@pytest.mark.asyncio
async def test_default_transformation_ids_filters_apply_default():
    transformations = [
        SimpleNamespace(id="transformation:keep", apply_default=True),
        SimpleNamespace(id="transformation:skip", apply_default=False),
        SimpleNamespace(id=None, apply_default=True),
    ]
    with patch(
        "open_notebook.domain.transformation.Transformation.get_all",
        new=AsyncMock(return_value=transformations),
    ):
        assert await default_transformation_ids() == ["transformation:keep"]


@pytest.mark.asyncio
async def test_default_transformation_ids_survives_db_failure():
    with patch(
        "open_notebook.domain.transformation.Transformation.get_all",
        new=AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert await default_transformation_ids() == []
