"""Tests for homeassistant.worker.config."""

from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.worker.config import CONF_PANEL_URL, CONFIG_SCHEMA


def test_schema_accepts_empty():
    """Empty workers list is valid."""
    result = CONFIG_SCHEMA({"workers": []})
    assert result["workers"] == []


def test_schema_process_worker():
    """Process worker schema validates correctly."""
    result = CONFIG_SCHEMA(
        {"workers": [{"name": "W", "type": "process", "port": 50052}]}
    )
    assert result["workers"][0]["name"] == "W"


def test_schema_remote_worker():
    """Remote worker schema validates correctly."""
    result = CONFIG_SCHEMA(
        {"workers": [{"name": "R", "type": "remote", "address": "1.2.3.4:50052"}]}
    )
    assert result["workers"][0]["address"] == "1.2.3.4:50052"


def test_schema_docker_worker():
    """Docker worker schema validates correctly."""
    result = CONFIG_SCHEMA(
        {
            "workers": [
                {
                    "name": "D",
                    "type": "docker",
                    "host": "tcp://1.2.3.4:2375",
                    "image": "ha-worker:latest",
                    "port": 50053,
                    "core_address": "localhost:50051",
                }
            ]
        }
    )
    assert result["workers"][0]["stop_on_shutdown"] is True  # default


def test_schema_rejects_unknown_type():
    """Unknown worker type raises Invalid."""
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({"workers": [{"name": "X", "type": "badtype", "port": 50052}]})


def test_schema_panel_url_default():
    """panel_url defaults to config/workers."""
    result = CONFIG_SCHEMA({"workers": []})
    assert result.get("workers_panel_url", "config/workers") == "config/workers"


def test_schema_panel_url_custom():
    """Custom panel_url is accepted."""
    result = CONFIG_SCHEMA({"workers": [], "workers_panel_url": "my/workers"})
    assert result["workers_panel_url"] == "my/workers"
