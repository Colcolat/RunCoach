"""The client is served by the same process as the API.

These tests are deliberately shallow: they assert the files are reachable and
that the pieces the voice path depends on are present. Whether the audio
actually works is a browser question, verified by hand with a microphone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


def test_the_page_is_served_at_the_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "RunCoach" in response.text


@pytest.mark.parametrize("asset", ["app.js", "pcm-processor.js", "style.css"])
def test_every_asset_the_page_references_is_reachable(client, asset):
    assert client.get(f"/static/{asset}").status_code == 200


def test_the_worklet_is_a_separate_file_because_it_loads_by_url(client):
    """addModule() takes a URL, so the processor cannot be inlined in app.js."""
    body = client.get("/static/app.js").text

    assert "addModule" in body
    assert "/static/pcm-processor.js" in body


def test_the_worklet_targets_the_rate_the_api_expects():
    from src.services.live_service import INPUT_SAMPLE_RATE

    source = (WEB / "pcm-processor.js").read_text(encoding="utf-8")
    declared = int(re.search(r"TARGET_RATE\s*=\s*(\d+)", source).group(1))

    assert declared == INPUT_SAMPLE_RATE


def test_the_worklet_converts_to_signed_16_bit():
    """Gemini Live wants Int16 PCM; Float32 straight from the mic would be noise."""
    source = (WEB / "pcm-processor.js").read_text(encoding="utf-8")

    assert "Int16Array" in source


def test_the_client_falls_back_to_text_when_voice_is_refused():
    source = (WEB / "app.js").read_text(encoding="utf-8")

    assert "budget_exhausted" in source
    assert "not_configured" in source


def test_the_client_reads_the_sample_rates_from_the_handshake():
    """Hardcoding them in the browser would drift from the server silently."""
    source = (WEB / "app.js").read_text(encoding="utf-8")

    assert "message.output_sample_rate" in source
    assert "message.input_sample_rate" in source
