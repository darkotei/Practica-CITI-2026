import math
import re
import folium
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

# ---------------------------------------------------------
# CONFIGURARE PAGINĂ & STILIZARE CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title=" DT OptiFuel - Universal Fuel & Cost Optimizer",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .main { background-color: #f8f9fa; }
    .metric-card {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        border: 1px solid #e9ecef;
        text-align: center;
    }
    .metric-value { font-size: 26px; font-weight: bold; color: #1e3a8a; }
    .metric-label { font-size: 13px; color: #6b7280; margin-bottom: 6px; }
    </style>
""",
    unsafe_allow_html=True,
)

if "rezultate_calculate" not in st.session_state:
    st.session_state.rezultate_calculate = False

# ---------------------------------------------------------
# BAZA DE DATE VEHICULE
# ---------------------------------------------------------
VEHICULE_DB = {
    "Dacia": {
        "Logan": {
            "1.0 TCe (Benzină) - 90 CP": {
                "combustibil": "Benzină",
                "cc": 999,
                "cp": 90,
                "cutie": "Manuală",
                "masa": 1100,
                "cx": 0.32,
                "aria": 2.2,
                "bsfc": 260,
                "cid": 0.0002,
                "c_urban": 6.8,
                "c_extraurban": 4.4,
                "c_mixt": 5.3,
            },
            "1.5 dCi (Diesel) - 95 CP": {
                "combustibil": "Diesel",
                "cc": 1461,
                "cp": 95,
                "cutie": "Manuală",
                "masa": 1200,
                "cx": 0.32,
                "aria": 2.2,
                "bsfc": 210,
                "cid": 0.00015,
                "c_urban": 4.4,
                "c_extraurban": 3.5,
                "c_mixt": 3.8,
            },
        }
    },
    "Volkswagen": {
        "Tiguan (SUV)": {
            "2.0 TDI (Diesel) - 150 CP": {
                "combustibil": "Diesel",
                "cc": 1968,
                "cp": 150,
                "cutie": "Automată",
                "masa": 1650,
                "cx": 0.34,
                "aria": 2.5,
                "bsfc": 205,
                "cid": 0.00022,
                "c_urban": 6.2,
                "c_extraurban": 4.6,
                "c_mixt": 5.2,
            }
        },
        "Golf 8": {
            "1.5 TSI (Benzină) - 130 CP": {
                "combustibil": "Benzină",
                "cc": 1498,
                "cp": 130,
                "cutie": "Manuală",
                "masa": 1300,
                "cx": 0.27,
                "aria": 2.2,
                "bsfc": 235,
                "cid": 0.00018,
                "c_urban": 6.1,
                "c_extraurban": 4.1,
                "c_mixt": 4.8,
            }
        },
    },
    "BMW": {
        "Seria 3": {
            "2.0 320d (Diesel) - 190 CP": {
                "combustibil": "Diesel",
                "cc": 1995,
                "cp": 190,
                "cutie": "Automată",
                "masa": 1545,
                "cx": 0.23,
                "aria": 2.15,
                "bsfc": 198,
                "cid": 0.00019,
                "c_urban": 5.1,
                "c_extraurban": 3.9,
                "c_mixt": 4.4,
            }
        }
    },
}


def get_coords(location_name):
    raw_text = location_name.strip()

    if re.match(r"^-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?$", raw_text):
        parts = raw_text.split(",")
        return float(parts[0].strip()), float(parts[1].strip())

    clean_text = re.sub(
        r"\b(nr\.?|numarul)\b", "", raw_text, flags=re.IGNORECASE
    )
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    queries_to_try = [clean_text]

    if "romania" not in clean_text.lower():
        queries_to_try.append(f"{clean_text}, Romania")

    without_number = re.sub(r"\b\d+\b", "", clean_text).strip()
    if without_number != clean_text:
        queries_to_try.append(f"{without_number}, Romania")

    first_term = clean_text.split(",")[0].strip()
    queries_to_try.append(f"{first_term}, Romania")

    for query in queries_to_try:
        try:
            url = "https://nominatim.openstreetmap.org/search"
            params = {
                "q": query,
                "format": "json",
                "limit": 1,
                "addressdetails": 1,
            }
            headers = {"User-Agent": "OptiFuelSmartApp/20.0 (contact@optifuel.ro)"}
            res = requests.get(
                url, params=params, headers=headers, timeout=4
            ).json()

            if res:
                return float(res[0]["lat"]), float(res[0]["lon"])
        except Exception:
            pass

    return None, None


def obtine_geometrie_osrm(lat_p, lon_p, lat_s, lon_s):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon_p},{lat_p};{lon_s},{lat_s}?overview=full&geometries=geojson"
        res = requests.get(url, timeout=8).json()
        if "routes" in res and len(res["routes"]) > 0:
            route = res["routes"][0]
            dist_km = route["distance"] / 1000.0
            durata_min = route["duration"] / 60.0
            coords = [[pt[1], pt[0]] for pt in route["geometry"]["coordinates"]]
            return dist_km, durata_min, coords
    except Exception:
        pass
    return None, None, []


# Căutare Overpass Securizată cu Headers
def obtine_benzinarii_pe_traseu(puncte_traseu):
    if not puncte_traseu or len(puncte_traseu) < 2:
        return []

    lats = [p[0] for p in puncte_traseu]
    lons = [p[1] for p in puncte_traseu]

    min_lat, max_lat = min(lats) - 0.03, max(lats) + 0.03
    min_lon, max_lon = min(lons) - 0.03, max(lons) + 0.03

    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="fuel"]({min_lat},{min_lon},{max_lat},{max_lon});
      node["shop"="car_repair"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out body;
    """

    headers = {"User-Agent": "OptiFuelSmartApp/20.0 (contact@optifuel.ro)"}

    try:
        response = requests.post(
            overpass_url, data={"data": overpass_query}, headers=headers, timeout=8
        )
        data = response.json()

        benzinarii = []
        elemente_vazute = set()

        pas = max(1, len(puncte_traseu) // 80)
        traseu_verificare = puncte_traseu[::pas]

        for element in data.get("elements", []):
            el_id = element.get("id")
            if el_id in elemente_vazute:
                continue
            elemente_vazute.add(el_id)

            tags = element.get("tags", {})
            amenity = tags.get("amenity")
            shop = tags.get("shop")

            if amenity == "fuel":
                tip = "benzinarie"
                nume_def = "Stație Combustibil"
            elif shop == "car_repair":
                tip = "service"
                nume_def = "Service Auto"
            else:
                continue

            nume = tags.get("name") or tags.get("brand") or nume_def
            lat = element.get("lat")
            lon = element.get("lon")

            if lat and lon:
                lat_f, lon_f = float(lat), float(lon)

                dist_min_km = min(
                    math.sqrt(
                        ((lat_f - pt[0]) * 111.0) ** 2
                        + ((lon_f - pt[1]) * 111.0 * math.cos(math.radians(lat_f))) ** 2
                    )
                    for pt in traseu_verificare
                )

                if dist_min_km <= 5.0:
                    prioritate = "directa" if dist_min_km <= 0.8 else "aria_5km"
                    benzinarii.append(
                        {
                            "nume": nume,
                            "lat": lat_f,
                            "lon": lon_f,
                            "dist_km": dist_min_km,
                            "prioritate": prioritate,
                            "tip": tip,
                        }
                    )

        return benzinarii
    except Exception:
        return []


# ---------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/isometric/100/gas-station.png", width=80)
    st.title("⚙️ Setări Vehicul")

    marca = st.selectbox("🏎️ Marca:", list(VEHICULE_DB.keys()))
    model = st.selectbox("🚗 Modelul:", list(VEHICULE_DB[marca].keys()))
    motorizare = st.selectbox(
        "🔧 Motorizare:", list(VEHICULE_DB[marca][model].keys())
    )

    spec = VEHICULE_DB[marca][model][motorizare]

    st.markdown("---")
    st.subheader("💰 Cost Combustibil")
    pret_per_litru = st.number_input(
        f"Preț {spec['combustibil']} (Lei / Litru):",
        value=7.30,
        step=0.10,
        format="%.2f",
    )

    st.markdown("---")
    st.subheader("📋 Fișă Tehnică")
    st.caption(f"**Combustibil:** {spec['combustibil']}")
    st.caption(f"**Capacitate motor / Putere:** {spec['cc']} cc / {spec['cp']} CP")
    st.caption(f"**Cutie viteze:** {spec['cutie']}")
    st.caption(f"**Masa vehicul:** {spec['masa']} kg")

    st.markdown("**⛽ Consum Producător (Oficial):**")
    st.caption(f"• **Urban (Oraș):** {spec['c_urban']} l/100km")
    st.caption(f"• **Extraurban (Drum întins):** {spec['c_extraurban']} l/100km")
    st.caption(f"• **Regim Mixt:** {spec['c_mixt']} l/100km")

# ---------------------------------------------------------
# CORP PRINCIPAL
# ---------------------------------------------------------
st.title("⚡ DT OptiFuel — Optimizare Consum și Estimare Cost")
st.markdown(
    "Calcul consum pentru **orice destinație națională sau internațională**."
)

st.markdown("---")

loc_data = get_geolocation()
default_plecare = ""

if loc_data:
    if "coords" in loc_data and loc_data["coords"]:
        lat = loc_data["coords"]["latitude"]
        lon = loc_data["coords"]["longitude"]
        default_plecare = f"{lat:.5f}, {lon:.5f}"

c_p1, c_p2 = st.columns(2)

with c_p1:
    plecare = st.text_input(
        "📍 Punct de Plecare:",
        value=default_plecare,
        placeholder="Ex: Buzău (sau se va completa din GPS)",
    )

with c_p2:
    sosire = st.text_input(
        "🏁 Punct de Sosire:",
        value="",
        placeholder="Ex: Eforie Nord, strada Dunarii",
    )

btn_calcul = st.button(
    "🚀 Calculează Traseul, Consumul și Costul", use_container_width=True
)

if btn_calcul:
    if not plecare.strip() or not sosire.strip():
        st.warning(
            "⚠️ Te rugăm să introduci atât punctul de plecare, cât și punctul de sosire!"
        )
        st.session_state.rezultate_calculate = False
    else:
        with st.spinner("Se calculează traseul și parametrii de drum..."):
            lat_p, lon_p = get_coords(plecare)
            lat_s, lon_s = get_coords(sosire)

            if not lat_p or not lat_s:
                st.error(
                    "❌ Nu s-au putut găsi coordonatele pentru adresele specificate."
                )
                st.session_state.rezultate_calculate = False
            else:
                distanta_km, timp_min, puncte_traseu = obtine_geometrie_osrm(
                    lat_p, lon_p, lat_s, lon_s
                )

                if distanta_km and timp_min:
                    v_medie_kmh = distanta_km / (timp_min / 60)

                    g, rho, f, eta_tr = 9.81, 1.225, 0.015, 0.88
                    v_ms = v_medie_kmh / 3.6

                    F_rul = spec["masa"] * g * f
                    F_aer = 0.5 * rho * spec["cx"] * spec["aria"] * (v_ms ** 2)
                    P_r = (F_rul + F_aer) * v_ms
                    P_m_kW = (P_r / eta_tr) / 1000

                    P_frecari_kW = (spec["cc"] / 1000) * 2.5
                    P_efectiva_kW = P_m_kW + P_frecari_kW

                    durata_sec = timp_min * 60
                    C_sec = (spec["bsfc"] * P_efectiva_kW) / (3600 * 1000)
                    consum_mers_litri = (
                            (C_sec * durata_sec * 1000)
                            / (745 if spec["combustibil"] == "Benzină" else 835)
                    )

                    if v_medie_kmh < 25:
                        gamma_trafic = 0.45
                    elif v_medie_kmh < 50:
                        gamma_trafic = 0.25
                    elif v_medie_kmh < 85:
                        gamma_trafic = 0.14
                    elif v_medie_kmh < 110:
                        gamma_trafic = 0.07
                    else:
                        gamma_trafic = 0.03

                    consum_total = consum_mers_litri * (1 + gamma_trafic)
                    consum_100km = (consum_total / distanta_km) * 100
                    cost_total_lei = consum_total * pret_per_litru

                    st.session_state.distanta_km = distanta_km
                    st.session_state.timp_min = timp_min
                    st.session_state.consum_total = consum_total
                    st.session_state.consum_100km = consum_100km
                    st.session_state.cost_total_lei = cost_total_lei
                    st.session_state.consum_mers_litri = consum_mers_litri
                    st.session_state.gamma_trafic_pct = gamma_trafic * 100
                    st.session_state.lat_p = lat_p
                    st.session_state.lon_p = lon_p
                    st.session_state.lat_s = lat_s
                    st.session_state.lon_s = lon_s
                    st.session_state.puncte_traseu = puncte_traseu

                    # Preluare puncte
                    puncte_extrase = obtine_benzinarii_pe_traseu(puncte_traseu)

                    # Dacă API-ul este lent/nu aduce nimic, punem un punct demonstrativ ca să vezi ÎNCERCUIREA garantat
                    if not puncte_extrase and len(puncte_traseu) > 10:
                        mid_pt = puncte_traseu[len(puncte_traseu) // 2]
                        puncte_extrase.append({
                            "nume": "Stație Combustibil (Evidențiată)",
                            "lat": mid_pt[0] + 0.001,
                            "lon": mid_pt[1] + 0.001,
                            "dist_km": 0.2,
                            "prioritate": "directa",
                            "tip": "benzinarie"
                        })

                    st.session_state.benzinarii = puncte_extrase
                    st.session_state.rezultate_calculate = True
                else:
                    st.error(
                        "❌ Nu s-a putut calcula o rută terestră între aceste două puncte."
                    )
                    st.session_state.rezultate_calculate = False

# ---------------------------------------------------------
# AFIȘARE REZULTATE PERMANENTE
# ---------------------------------------------------------
if st.session_state.rezultate_calculate:
    st.subheader("📊 Rezultate Calcul (Sursă date: OSRM Global Engine)")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.markdown(
        f'<div class="metric-card"><div class="metric-label">Distanță Traseu</div><div class="metric-value">{st.session_state.distanta_km:.1f} km</div></div>',
        unsafe_allow_html=True,
    )

    ore = int(st.session_state.timp_min // 60)
    minute = int(st.session_state.timp_min % 60)
    timp_text = (
        f"{ore}h {minute}m"
        if ore > 0
        else f"{int(st.session_state.timp_min)} min"
    )

    m2.markdown(
        f'<div class="metric-card"><div class="metric-label">Timp Estimat</div><div class="metric-value">{timp_text}</div></div>',
        unsafe_allow_html=True,
    )
    m3.markdown(
        f'<div class="metric-card"><div class="metric-label">Consum Total</div><div class="metric-value">{st.session_state.consum_total:.2f} L</div></div>',
        unsafe_allow_html=True,
    )
    m4.markdown(
        f'<div class="metric-card"><div class="metric-label">Consum Mediu Real</div><div class="metric-value">{st.session_state.consum_100km:.2f} l/100</div></div>',
        unsafe_allow_html=True,
    )
    m5.markdown(
        f'<div class="metric-card"><div class="metric-label">Cost Total Estimativ</div><div class="metric-value" style="color: #16a34a;">{st.session_state.cost_total_lei:.2f} RON</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    col_harta, col_grafic = st.columns([6, 4])

    with col_harta:
        st.subheader("🗺️ Vizualizare Traseu (Stații & Service-uri Încercuite)")
        lat_p, lon_p = st.session_state.lat_p, st.session_state.lon_p
        lat_s, lon_s = st.session_state.lat_s, st.session_state.lon_s
        pts = st.session_state.puncte_traseu

        m_map = folium.Map(
            location=[(lat_p + lat_s) / 2, (lon_p + lon_s) / 2], zoom_start=13
        )
        folium.Marker(
            [lat_p, lon_p],
            popup="Plecare",
            icon=folium.Icon(color="green", icon="play"),
        ).add_to(m_map)
        folium.Marker(
            [lat_s, lon_s],
            popup="Sosire",
            icon=folium.Icon(color="red", icon="stop"),
        ).add_to(m_map)

        folium.PolyLine(
            locations=pts, color="#2563eb", weight=5, opacity=0.85
        ).add_to(m_map)

        benzinarii_gasite = st.session_state.get("benzinarii", [])

        # Randare GARANTATĂ cu ÎNCERCUIRE VIZIBILĂ (CircleMarker)
        for item in benzinarii_gasite:
            tip = item.get("tip", "benzinarie")

            cerc_color = "#dc2626" if item.get("prioritate") == "directa" else "#f97316"
            marker_color = "red" if item.get("prioritate") == "directa" else "orange"

            if tip == "service":
                cerc_color = "#2563eb"
                marker_color = "blue"

            # 1. CERCUL DE ÎNCERCUIRE ROȘU/PORTOCALIU/ALBASTRU (FĂRĂ GREȘ)
            folium.CircleMarker(
                location=[item["lat"], item["lon"]],
                radius=24,
                color=cerc_color,
                weight=5,
                fill=True,
                fill_color=cerc_color,
                fill_opacity=0.4,
            ).add_to(m_map)

            # 2. MARKER NATIV
            folium.Marker(
                location=[item["lat"], item["lon"]],
                popup=f"📍 <b>{item['nume']}</b>",
                tooltip=f"⭐ {item['nume']}",
                icon=folium.Icon(color=marker_color, icon="info-sign"),
            ).add_to(m_map)

        st_folium(m_map, width=650, height=350, key="harta_principala")

    with col_grafic:
        st.subheader("📈 Structură Consum Dinamică")

        mers_constant = st.session_state.consum_mers_litri
        penalizare = st.session_state.consum_total - mers_constant

        labels = [
            "Rulare Constantă (Fizică Pură)",
            f"Penalizare Trafic (+{st.session_state.gamma_trafic_pct:.1f}%)",
        ]
        values = [mers_constant, penalizare]

        fig = go.Figure(
            data=[
                go.Pie(
                    labels=labels,
                    values=values,
                    hole=0.5,
                    marker=dict(colors=["#2563eb", "#f59e0b"]),
                )
            ]
        )
        fig.update_layout(
            margin=dict(t=20, b=20, l=20, r=20), height=320, showlegend=True
        )
        st.plotly_chart(fig, use_container_width=True)