"""Reading the week's plan out of what the coach just said.

Domain logic, like src.coaching.prompts and src.coaching.extraction: no SDK
imports and no I/O, so the rules can be tested without a network or a key.

This module exists because the plan was never data. The coach announces it in
prose, inside a reply, and prose is what got stored:

    "Haz el martes diez kilómetros suaves, el jueves doce incluyendo series
     cortas de un minuto rápido, el sábado seis suaves y el domingo la tirada
     larga de dieciséis a un ritmo cómodo."

Parsing that with regular expressions was the first idea and it was wrong.
Sampling real replies showed why: the number comes before the day ("cuatro
kilómetros suaves el martes") as often as after it ("el jueves doce"), the
quantity is frequently bare ("otros cuatro el jueves"), every number is spelled
out, and some numbers are not kilometres at all - "series cortas de un minuto"
would have been read as a one-kilometre session. A panel showing the wrong
distance is worse than no panel, because a runner would follow it.

So the model reads it back. What keeps that affordable is `looks_like_a_plan`:
naming a weekday is something a plan always does and a reply like "descansa hoy"
never does, and unlike the distances, weekday names are unambiguous enough to
test for in code.

One number is deliberately not extracted. The weekly total is summed from the
sessions here, never taken from the model, because the panel and the sessions
inside it must agree - and two sources for one number eventually disagree.
"""

from __future__ import annotations

import re
import unicodedata

# Stored and rendered as integers rather than names because the coach answers in
# whatever language it is spoken to, and a plan given in English must fill the
# same panel as one given in Spanish. The client turns these back into names.
MONDAY, SUNDAY = 1, 7

# A single session. The longest run in the persona's marathon guidance is thirty
# to thirty-two kilometres, so this leaves room without admitting a transcript
# artefact like "ciento veinte".
MAX_SESSION_KM = 80.0

# Long enough for "tirada larga con series" and short enough to fit a chip in
# the panel without wrapping into a paragraph.
MAX_NOTE = 40

_DAY_WORDS = {
    "lunes": 1, "monday": 1,
    "martes": 2, "tuesday": 2,
    "miercoles": 3, "wednesday": 3,
    "jueves": 4, "thursday": 4,
    "viernes": 5, "friday": 5,
    "sabado": 6, "saturday": 6,
    "domingo": 7, "sunday": 7,
}

_WORD = re.compile(r"[a-z]+")


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


# --- the gate ----------------------------------------------------------------

def looks_like_a_plan(reply: str) -> bool:
    """Whether this reply is worth spending an extraction request on.

    Two weekdays, not one. A single day appears in plenty of replies that carry
    no plan at all - "el domingo descansas", "¿cómo te fue el martes?" - while a
    week laid out across days always names at least two. That difference is what
    keeps this from firing on most of a conversation.
    """
    words = set(_WORD.findall(_strip_accents((reply or "").lower())))
    return len(words & _DAY_WORDS.keys()) >= 2


# --- the prompt --------------------------------------------------------------

PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sessions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "day": {"type": "INTEGER"},
                    "km": {"type": "NUMBER"},
                    "note": {"type": "STRING", "nullable": True},
                },
                "required": ["day", "km", "note"],
            },
        },
        # Whether this reply lays out a whole week or only speaks about part of
        # one. Without it every mention of two days replaced everything: told
        # "mantén el sábado de tres y el domingo de nueve", the panel dropped
        # the Tuesday and Thursday it already had and showed a twelve-kilometre
        # week the coach never prescribed.
        "replaces_the_week": {"type": "BOOLEAN"},
    },
    "required": ["sessions", "replaces_the_week"],
}


PLAN_PROMPT = """Lees el plan de entrenamiento de una semana a partir de lo que un entrenador \
acaba de decirle a un corredor.

Devuelve una sesión por cada día en el que el entrenador manda CORRER. Los días de descanso no se \
devuelven: se deducen de los que faltan.

day es el día de la semana como número, del 1 al 7, donde 1 es lunes y 7 es domingo.

km son los kilómetros de ESE día. Casi siempre vienen escritos con letra: "cuatro kilómetros" son \
4, "dieciséis" son 16, "veintidós" son 22.

Ten cuidado con dos cosas concretas:

Hay números que no son distancias. "Series cortas de un minuto", "durante treinta minutos" o \
"a ritmo de cinco por kilómetro" hablan de tiempo o de ritmo, no de kilómetros. Nunca los \
devuelvas como km.

La distancia y el día pueden venir en cualquier orden, y la distancia puede venir sin la palabra \
kilómetros: en "haz cuatro el martes y otros cuatro el jueves", el martes son 4 y el jueves son 4.

note es una descripción muy corta del tipo de sesión, de dos o tres palabras, tal como la dijo el \
entrenador: "suaves", "tirada larga", "series cortas", "tempo". Si no dice nada del tipo, devuelve \
null.

replaces_the_week distingue dos cosas que suenan parecido y no lo son.

Es true cuando el entrenador está montando la semana entera: "esta semana haces", "repartimos los \
veintidós kilómetros así", "el plan de esta semana es".

Es false cuando solo habla de algunos días de una semana que ya existía: "para lo que queda de \
semana, mantén el sábado y el domingo", "cambiamos el jueves por el viernes", "el martes mejor \
hazlo más suave". Ahí devuelves únicamente los días de los que habla, y los demás días de su \
semana se quedan como estaban.

Si el entrenador no está dando un plan repartido por días, devuelve una lista vacía. Es mucho \
mejor devolver nada que inventar un reparto que no dijo: el corredor va a ver estos números en \
pantalla y a salir a correrlos."""


# --- validation --------------------------------------------------------------

def _clean_km(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    km = float(value)
    if not 0 < km <= MAX_SESSION_KM:
        return None
    return round(km, 1)


def _clean_day(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MONDAY <= value <= SUNDAY else None


def _clean_note(value) -> str | None:
    if not isinstance(value, str):
        return None
    note = " ".join(value.split())[:MAX_NOTE].strip()
    return note or None


def replaces_the_week(raw: dict | None) -> bool:
    """Whether the reading should replace the stored week or be folded into it.

    Defaults to False, which is the safe direction: folding a genuine new week
    into an old one leaves at most a stale day, while replacing on a passing
    mention of two days deletes the rest of somebody's training.
    """
    return bool(isinstance(raw, dict) and raw.get("replaces_the_week") is True)


def merge(existing: list[dict], update: list[dict]) -> list[dict]:
    """Fold a partial reading into the week already on record.

    A day named again is overwritten - "el martes mejor hazlo más suave" is a
    correction, and the newer number is the one the coach means. Days not
    mentioned survive untouched, which is the whole point.
    """
    by_day = {session["day"]: session for session in existing}
    by_day.update({session["day"]: session for session in update})
    return [by_day[day] for day in sorted(by_day)]


def clean(raw: dict | None) -> list[dict]:
    """Keep the sessions that survive validation, in day order.

    Returns a list rather than a dict of extras: an empty list means "the coach
    was not laying out a week", and the caller must treat that as "change
    nothing" rather than "erase the plan". A runner who asks an unrelated
    question should not watch their week disappear from the panel.

    A day named twice keeps its first session. The alternative - summing them -
    would invent a distance the coach never said.
    """
    if not isinstance(raw, dict):
        return []

    sessions: dict[int, dict] = {}
    for item in raw.get("sessions") or []:
        if not isinstance(item, dict):
            continue
        day = _clean_day(item.get("day"))
        km = _clean_km(item.get("km"))
        if day is None or km is None or day in sessions:
            continue
        sessions[day] = {"day": day, "km": km, "note": _clean_note(item.get("note"))}

    return [sessions[day] for day in sorted(sessions)]


def week_total(sessions: list[dict]) -> float:
    """The week's volume, summed rather than believed.

    The coach announces a total out loud and the panel shows one; if the panel
    took the model's word for it, the two could differ by a kilometre and the
    runner would have no way to tell which was right. Summing what is displayed
    means the total is always the total of what is displayed.
    """
    return round(sum(session["km"] for session in sessions), 1)
