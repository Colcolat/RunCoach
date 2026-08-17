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


# --- F5: the designed interface ----------------------------------------------

@pytest.mark.parametrize("asset", ["index.html", "app.js", "style.css"])
def test_the_client_pulls_nothing_from_the_internet(asset):
    """The design arrived as Tailwind from a CDN plus two Google Fonts.

    All of it was inlined. A page that fetches from someone else's host fails
    offline, leaks the visitor to a third party, and adds a runtime dependency
    to a deployment (F7) that has no reason to carry one. Tailwind's own docs
    call the CDN build development-only.
    """
    source = (WEB / asset).read_text(encoding="utf-8")

    assert "http://" not in source
    assert "https://" not in source


def test_the_voice_control_has_a_distinct_look_for_every_state():
    """State must be visible, which was the point of the brief asking for it."""
    css = (WEB / "style.css").read_text(encoding="utf-8")

    for state in ("listening", "thinking", "speaking", "unavailable"):
        assert f'[data-state="{state}"]' in css, f"falta el estado {state}"


def test_the_state_is_readable_without_seeing_colour():
    """Colour alone would leave the state invisible to some of the people using it."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert 'role="status"' in page
    for spoken in ("Escuchando", "Pensando", "El entrenador está hablando"):
        assert spoken in script


def test_the_panel_has_a_slot_for_every_field_the_api_returns():
    """A field added to the endpoint with nowhere to land would vanish silently."""
    from src.routes.chat import ProfileResponse

    page = (WEB / "index.html").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")

    shown = {"goal": "field-goal", "experience_level": "field-level",
             "weekly_km": "field-volume", "race_date": "field-race",
             "telegram_url": "telegram-link"}
    for field, slot in shown.items():
        assert slot in page, f"el panel no tiene sitio para {field}"
        assert field in script, f"app.js no lee {field}"

    # Everything the endpoint returns is either displayed or deliberately not.
    # weeks_to_race and reminder_at are rendered into other fields' text rather
    # than getting a slot of their own.
    ignored = {"session_id", "username", "weeks_to_race", "telegram_linked",
               "reminder_at"}
    assert set(ProfileResponse.model_fields) == set(shown) | ignored


def test_the_panel_never_offers_to_edit_the_profile():
    """The runner speaks their profile; a form would compete with the conversation."""
    page = (WEB / "index.html").read_text(encoding="utf-8")

    assert "field-goal" in page  # the panel exists
    assert "<form" in page  # the composer does too
    assert page.count("<form") == 1, "solo el composer debe ser un formulario"


def test_a_degraded_reply_is_marked_as_the_app_speaking():
    """A rate limit clears in a minute and must not read like a broken coach."""
    script = (WEB / "app.js").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")

    assert "notice: data.degraded" in script
    assert ".bubble.notice" in css


def test_the_weeks_to_the_race_come_from_the_server():
    """Recomputing them here would be free to disagree with what the coach says."""
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert "data.weeks_to_race" in script


@pytest.mark.parametrize("asset", ["index.html", "app.js", "style.css"])
def test_the_interface_carries_no_emoji(asset):
    """A house rule, and the design tool inserts them unless told not to."""
    source = (WEB / asset).read_text(encoding="utf-8")

    assert not [c for c in source if ord(c) > 0x2100], "hay emoji o pictogramas"


def test_the_grid_columns_are_allowed_to_shrink():
    """Found on a real phone: every heading clipped off the left edge.

    A grid item defaults to min-width:auto, so a column will not shrink below
    its contents - and the profile strip is deliberately wider than the screen,
    scrolling inside its own overflow. With a bare 1fr the strip stretched the
    column, the column stretched the page, and a 375px viewport got a 675px
    document. The wide layout already used minmax(0, ...); the narrow one, the
    one phones actually get, did not.

    It survived review because the preview pane never goes below ~674px, which
    happened to be exactly the width the content wanted.
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")

    assert "grid-template-columns: minmax(0, 1fr)" in css
    assert "grid-template-columns: minmax(0, 52rem)" in css
    assert "overflow-x: hidden" in css


def test_speaking_updates_the_panel_like_typing_does():
    """Voice used to be write-only for the profile; the panel never moved."""
    script = (WEB / "app.js").read_text(encoding="utf-8")

    assert "profile_updated" in script


def test_hiding_something_actually_hides_it():
    """Seen in a screenshot: the "Conectar Telegram" button sitting directly
    above the words "Telegram conectado". The element had the hidden attribute,
    but .telegram-link sets display:inline-block, and an author rule beats the
    browser's own [hidden] at equal specificity."""
    css = (WEB / "style.css").read_text(encoding="utf-8")

    assert "[hidden]" in css
    assert "display: none !important" in css


# --- disclosure and privacy ---------------------------------------------------

def test_the_page_says_it_is_not_a_person():
    """The coach has a persona, a name and a voice. Someone could reasonably
    take it for a human, and it costs one sentence to say otherwise."""
    page = (WEB / "index.html").read_text(encoding="utf-8")

    assert "inteligencia artificial" in page
    assert "no sustituyen a un médico" in page


def test_the_privacy_notice_is_reachable_from_the_coach(client):
    page = (WEB / "index.html").read_text(encoding="utf-8")

    assert 'href="/privacidad"' in page
    assert client.get("/privacidad").status_code == 200


def test_the_notice_says_what_is_kept_and_what_is_not():
    aviso = (WEB / "privacidad.html").read_text(encoding="utf-8")

    for prometido in ("Google", "Telegram", "localStorage", "audio"):
        assert prometido in aviso, f"el aviso no menciona {prometido}"


def test_the_notice_admits_the_session_id_is_not_authenticated():
    """It is the one limitation with a real consequence for a reader, so it is
    stated where a reader will see it rather than only in the README."""
    aviso = (WEB / "privacidad.html").read_text(encoding="utf-8")

    assert "no está autenticado" in aviso


def test_the_notice_carries_a_crisis_line():
    """A health-adjacent product that talks to people should not make anyone
    search for this."""
    aviso = (WEB / "privacidad.html").read_text(encoding="utf-8")

    assert "911" in aviso


def test_no_third_party_collector_was_ever_added():
    """The notice promises none. This is what keeps that true."""
    for asset in ("index.html", "privacidad.html", "app.js"):
        source = (WEB / asset).read_text(encoding="utf-8").lower()
        for rastreador in ("gtag", "analytics", "googletagmanager", "facebook",
                           "hotjar", "clarity.ms", "segment.io", "mixpanel"):
            assert rastreador not in source, f"{asset} carga {rastreador}"
