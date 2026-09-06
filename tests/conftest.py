"""Shared pytest command-line options for explicit hardware validation."""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    hardware = parser.getgroup("deskvision hardware")
    hardware.addoption(
        "--camera-device-index",
        action="store",
        type=int,
        default=0,
        help="camera index used by tests marked hardware",
    )
    hardware.addoption(
        "--soak-duration-seconds",
        action="store",
        type=float,
        default=900.0,
        help="duration of the test marked soak; defaults to the 15-minute acceptance run",
    )
    hardware.addoption(
        "--soak-warmup-seconds",
        action="store",
        type=float,
        default=60.0,
        help="warmup excluded from soak memory analysis",
    )
