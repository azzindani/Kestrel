"""Shared pytest fixtures available to all test modules."""

from __future__ import annotations

import pytest

from tests.helpers.factories import (
    make_app_config,
    make_bucket_state,
    make_params,
    make_signal,
    make_trending_candles,
)


@pytest.fixture
def cfg():
    return make_app_config()


@pytest.fixture
def params():
    return make_params()


@pytest.fixture
def trending_candles():
    return make_trending_candles(n=60)


@pytest.fixture
def default_signal():
    return make_signal()


@pytest.fixture
def default_state():
    return make_bucket_state()
