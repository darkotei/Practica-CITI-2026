"""Selector Auto-Data pentru aplicații Streamlit.

Preia, la cerere, ierarhia marcă -> model -> generație -> motorizare
de pe auto-data.net și extrage specificațiile utile OptiFuel. Rezultatele
sunt puse în cache o zi, ca aplicația să nu solicite aceeași pagină la
fiecare rerulare Streamlit.

Folosește modulul doar dacă termenii site-ului îți permit acest lucru.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
import streamlit as st


BASE_URL = "https://www.auto-data.net"
BRANDS_URL = f"{BASE_URL}/ro/allbrands"
REQUEST_HEADERS = {
    "User-Agent": "OptiFuel/1.0 (personal catalog lookup; contact@example.com)"
}
CACHE_SECONDS = 60 * 60 * 24

BRAND_PATH = re.compile(r"^/ro/[^/]+-brand-\d+/?$")
MODEL_PATH = re.compile(r"^/ro/[^/]+-model-\d+/?$")
GENERATION_PATH = re.compile(r"^/ro/[^/]+-generation-\d+/?$")
NUMERIC_ENDING = re.compile(r"-\d+/?$")


class AutoDataError(RuntimeError):
    """Eroare controlată la citirea catalogului Auto-Data."""


class _PageParser(HTMLParser):
    """Parser HTML mic, bazat doar pe biblioteca standard Python.

    Este suficient pentru linkurile și textul tabelar de pe paginile de catalog
    și evită o dependență suplimentară (BeautifulSoup).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._ignored_depth = 0
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if tag == "a":
            attributes = dict(attrs)
            self._href = attributes.get("href")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if tag == "a" and self._href:
            self.links.append((self._href, _clean(" ".join(self._anchor_text))))
            self._href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self.text_parts.append(data)
        if self._href is not None:
            self._anchor_text.append(data)


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _range_average(first: str, second: str | None) -> float | None:
    start = _to_float(first)
    end = _to_float(second)
    if start is None:
        return None
    return round((start + end) / 2, 2) if end is not None else start


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def _html(url: str) -> str:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AutoDataError(
            "Catalogul Auto-Data nu a putut fi accesat. Verifică conexiunea "
            "la internet sau încearcă din nou mai târziu."
        ) from exc
    return response.text


def _links(url: str, pattern: re.Pattern[str]) -> list[dict[str, str]]:
    """Extrage linkuri unice la un singur nivel din catalog."""
    parser = _PageParser()
    parser.feed(_html(url))
    parser.close()
    found: dict[str, str] = {}

    for href, label in parser.links:
        destination = urljoin(url, href)
        path = urlsplit(destination).path

        if pattern.match(path) and label:
            found.setdefault(destination, label)

    return [
        {"label": label, "url": destination}
        for destination, label in sorted(found.items(), key=lambda item: item[1].casefold())
    ]


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def brands() -> list[dict[str, str]]:
    return _links(BRANDS_URL, BRAND_PATH)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def models(brand_url: str) -> list[dict[str, str]]:
    return _links(brand_url, MODEL_PATH)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def generations(model_url: str) -> list[dict[str, str]]:
    return _links(model_url, GENERATION_PATH)


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def engines(generation_url: str) -> list[dict[str, str]]:
    """Extrage motorizările. Aceste URL-uri se termină doar într-un ID numeric."""
    parser = _PageParser()
    parser.feed(_html(generation_url))
    parser.close()
    found: dict[str, str] = {}

    for href, label in parser.links:
        destination = urljoin(generation_url, href)
        path = urlsplit(destination).path

        is_engine_page = (
            path.startswith("/ro/")
            and NUMERIC_ENDING.search(path) is not None
            and not BRAND_PATH.match(path)
            and not MODEL_PATH.match(path)
            and not GENERATION_PATH.match(path)
        )
        if is_engine_page and label:
            found.setdefault(destination, label)

    return [
        {"label": label, "url": destination}
        for destination, label in sorted(found.items(), key=lambda item: item[1].casefold())
    ]


def _first_match(text: str, *patterns: str) -> re.Match[str] | None:
    for pattern in patterns:
        matched = re.search(pattern, text)
        if matched:
            return matched
    return None


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def specifications(engine_url: str) -> dict[str, Any]:
    """Extrage valorile de care are nevoie calculatorul de consum.

    Valorile lipsă rămân ``None``; formularul Streamlit va oferi valori
    editabile pentru ele. Consumul declarat ca interval devine media intervalului.
    """
    parser = _PageParser()
    parser.feed(_html(engine_url))
    parser.close()
    text = _clean(" ".join(parser.text_parts))

    def declared_consumption(kind: str) -> float | None:
        matched = _first_match(
            text,
            # Unele mașini au standardul de măsurare după tipul consumului,
            # de exemplu: „urban (NEDC)” sau „mixt (WLTP)”.
            rf"Consumul de combustibil\s*-\s*{kind}(?:\s*\([^)]{{1,30}}\))?\s+"
            r"(\d+(?:[.,]\d+)?)(?:\s*-\s*(\d+(?:[.,]\d+)?))?\s*l/100\s*km",
        )
        return _range_average(matched.group(1), matched.group(2)) if matched else None

    urban = declared_consumption("urban")
    extraurban = declared_consumption("extra-urban")
    mixed = declared_consumption("mixt")
    power = _first_match(text, r"\bPutere\s+(\d+(?:[.,]\d+)?)\s*CP")
    displacement = _first_match(text, r"\bVolumul motorului\s+([\d ]+)\s*cm")
    mass = _first_match(text, r"\bMasă proprie\s+([\d ]+)\s*kg")
    payload = _first_match(
        text,
        r"\b(?:Încărcătura maximă|Încarcatura maxima)\s+([\d ]+)\s*kg",
    )
    gross_mass = _first_match(
        text,
        r"\b(?:Masă maximă autorizată|Masa maximă autorizată)\s+([\d ]+)\s*kg",
    )
    gearbox = _first_match(
        text,
        r"Numărul de viteze și tipul cutiei de viteze\s+(.{1,90}?)"
        r"(?=\s+(?:Suspensie|Frâne|Sisteme de asistență|Tipul de virare))",
    )
    drag = _first_match(
        text,
        r"Coeficientul aerodinamic.{0,45}?(\d+(?:[.,]\d+)?)"
    )

    if "Hidrogen" in text:
        fuel = "Hidrogen"
    elif "Electric" in text and "Motorină" not in text and "Benzină" not in text:
        fuel = "Electric"
    elif "Plug-in Hybrid" in text:
        fuel = "Plug-in Hybrid"
    elif "Hibrid" in text:
        fuel = "Hibrid"
    elif "Motorină" in text:
        fuel = "Motorină"
    elif "GPL" in text:
        fuel = "GPL"
    elif "Benzină" in text:
        fuel = "Benzină"
    else:
        fuel = None

    transmission_text = gearbox.group(1) if gearbox else ""
    if "automat" in transmission_text.casefold():
        transmission = "Automată"
    elif "manual" in transmission_text.casefold():
        transmission = "Manuală"
    else:
        transmission = None

    return {
        "url_sursa": engine_url,
        "combustibil": fuel,
        "c_urban": urban,
        "c_extraurban": extraurban,
        "c_mixt": mixed,
        "cp": _to_float(power.group(1)) if power else None,
        "cc": _to_float(displacement.group(1)) if displacement else None,
        "masa_proprie": _to_float(mass.group(1)) if mass else None,
        "incarcatura_maxima": _to_float(payload.group(1)) if payload else None,
        "masa_maxima_autorizata": _to_float(gross_mass.group(1)) if gross_mass else None,
        "cutie": transmission,
        "cutie_text": transmission_text or None,
        "cx": _to_float(drag.group(1)) if drag else None,
    }


def _select_url(label: str, choices: list[dict[str, str]], key: str) -> str | None:
    if not choices:
        st.info(f"Nu au fost găsite opțiuni pentru: {label}.")
        return None

    labels = {item["url"]: item["label"] for item in choices}
    return st.selectbox(label, list(labels), format_func=labels.get, key=key)


def render_vehicle_selector() -> dict[str, Any]:
    """Afișează selectorul complet. A se apela din ``with st.sidebar``.

    Returnează dicționarul ``spec`` compatibil cu calculatorul OptiFuel.
    """
    try:
        brand_url = _select_url("🏎️ Marca:", brands(), "auto_brand")
        if brand_url is None:
            return {}
        model_url = _select_url("🚗 Modelul:", models(brand_url), "auto_model")
        if model_url is None:
            return {}
        generation_url = _select_url(
            "🏷️ Generația:", generations(model_url), "auto_generation"
        )
        if generation_url is None:
            return {}
        engine_url = _select_url(
            "🔧 Motorizarea:", engines(generation_url), "auto_engine"
        )
        if engine_url is None:
            return {}
        data = specifications(engine_url)
    except AutoDataError as exc:
        st.error(str(exc))
        return {}

    brand_label = next(item["label"] for item in brands() if item["url"] == brand_url)
    model_label = next(item["label"] for item in models(brand_url) if item["url"] == model_url)
    generation_label = next(
        item["label"] for item in generations(model_url) if item["url"] == generation_url
    )
    engine_label = next(
        item["label"] for item in engines(generation_url) if item["url"] == engine_url
    )
    # Cheile trebuie să conțină ID-ul motorizării. Altfel Streamlit păstrează
    # valorile introduse pentru motorizarea precedentă, chiar dacă argumentul
    # ``value`` s-a schimbat.
    engine_id = urlsplit(engine_url).path.rstrip("/").rsplit("-", 1)[-1]
    field_key = f"auto_specs_{engine_id}"

    st.caption("Date preluate din catalog; le poți corecta înainte de calcul.")
    def consumption_label(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f} l/100 km"

    st.caption(
        "Consum declarat Auto-Data — "
        f"urban: **{consumption_label(data['c_urban'])}** · "
        f"extraurban: **{consumption_label(data['c_extraurban'])}** · "
        f"mixt: **{consumption_label(data['c_mixt'])}**"
    )
    # Aceste date sunt preluate automat și apar în fișa tehnică. Nu au câmpuri
    # editabile, fiindcă formula actuală de consum folosește consumul mixt.
    fuel = data["combustibil"] or "Necunoscut"
    cc = int(data["cc"]) if data["cc"] is not None else None
    cp = int(data["cp"]) if data["cp"] is not None else None
    curb_mass = int(data["masa_proprie"] or 1250)
    maximum_load = int(data["incarcatura_maxima"] or 5000)
    st.caption(f"Masă proprie preluată: **{curb_mass} kg**")
    cargo = st.number_input(
        "📦 Încărcătură adăugată (kg):",
        min_value=0,
        max_value=maximum_load,
        value=0,
        step=10,
        key=f"{field_key}_cargo",
        help="Încărcătură maximă admisă: "
        + (f"{maximum_load} kg" if data["incarcatura_maxima"] else "necunoscută"),
    )
    mass = curb_mass + cargo
    st.caption(f"**Masă vehicul + încărcătură: {mass} kg**")
    gearbox = data["cutie"] or "Necunoscută"
    consumption = st.number_input(
        "⛽ Consum mixt declarat/personalizat (L/100 km):",
        min_value=0.0,
        max_value=100.0,
        value=float(data["c_mixt"] or 6.5),
        step=0.1,
        format="%.1f",
        key=f"{field_key}_consumption",
    )

    return {
        "marca": brand_label,
        "model": model_label,
        "generatie": generation_label,
        "motorizare": engine_label,
        "combustibil": fuel,
        "cc": cc,
        "cp": cp,
        "masa": mass,
        "masa_proprie": curb_mass,
        "incarcatura": cargo,
        "incarcatura_maxima": data["incarcatura_maxima"],
        "masa_maxima_autorizata": data["masa_maxima_autorizata"],
        "cutie": gearbox,
        # c_mixt este valoarea editabilă pe care o folosește calculatorul.
        "c_mixt": consumption,
        # Valorile declarate de site rămân separate, pentru afișare corectă.
        "c_urban_declarat": data["c_urban"],
        "c_extraurban_declarat": data["c_extraurban"],
        "c_mixt_declarat": data["c_mixt"],
        "cx": data["cx"] or 0.32,
        "aria": 2.2,
        "url_sursa": engine_url,
    }
