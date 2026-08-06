import math
from datetime import time
import re
import WazeRouteCalculator
import folium
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation
from auto_data_catalog import render_vehicle_selector


# ---------------------------------------------------------
# CONFIGURARE PAGINĂ & STILIZARE CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title= "OptiFuel - Universal Fuel & Cost Optimizer",
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


# Geocodare avansată cu curățare de text și Fallback
def get_coords(location_name):
    raw_text = location_name.strip()

    # Dacă textul conține deja coordonate (ex: "45.15170, 26.82130" venite din GPS)
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
            headers = {"User-Agent": "OptiFuelSmartAddress-17.0 - contact darkotrifan62 at gmail.com"}
            res = requests.get(
                url, params=params, headers=headers, timeout=10
            ).json()

            if res:
                return float(res[0]["lat"]), float(res[0]["lon"])
        except Exception:
            pass

    return None, None


# OSRM Routing Engine
def obtine_geometrie_osrm(lat_p, lon_p, lat_s, lon_s):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon_p},{lat_p};{lon_s},{lat_s}?overview=full&geometries=geojson&steps=true"
        headers = {"User-Agent": "OptiFuel_Practica_2026/1.0"}
        response = requests.get(url, headers=headers,timeout=10)
        if response.status_code != 200:
            print(f"⚠️ OSRM a respins cererea. Status: {response.status_code} | Mesaj: {response.text}")
            return None, None, [], []
        res=response.json()
        if "routes" in res and len(res["routes"]) > 0:
            route = res["routes"][0]
            dist_km = route["distance"] / 1000.0
            durata_min = route["duration"] / 60.0
            coords = [[pt[1], pt[0]] for pt in route["geometry"]["coordinates"]]
            segmente=[]
            for step in route["legs"][0]["steps"]:
                segmente.append({
                    "distanta_m": step["distance"],
                    "durata_s": step["duration"],
                    "nume": step.get("name", "segment")
                })
            return dist_km, durata_min, coords, segmente
    except Exception as e:
        print(f"❌ Eroare tehnică la interogarea OSRM: {e}")
    return None, None, [], []


# Căutare exhaustivă benzinării (Rază strictă de 5 km)
def obtine_benzinarii_pe_traseu(puncte_traseu):
    if not puncte_traseu or len(puncte_traseu) < 2:
        return []

    # 1. Calculăm Bounding Box pentru întreg traseul (+ puffer de ~5km în grade GPS: ~0.045)
    lats = [p[0] for p in puncte_traseu]
    lons = [p[1] for p in puncte_traseu]

    min_lat, max_lat = min(lats) - 0.045, max(lats) + 0.045
    min_lon, max_lon = min(lons) - 0.045, max(lons) + 0.045

    # 2. Interogare Overpass pentru TOATE stațiile din zona traseului
    overpass_url = "https://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json][timeout:15];
    (
      node["amenity"="fuel"]({min_lat},{min_lon},{max_lat},{max_lon});
      way["amenity"="fuel"]({min_lat},{min_lon},{max_lat},{max_lon});
    );
    out body center;
    """

    try:
        response = requests.post(
            overpass_url, data={"data": overpass_query}, timeout=10
        )
        data = response.json()

        benzinarii = []
        elemente_vazute = set()

        # Eșantionăm traseul pentru verificare matematică rapidă
        pas = max(1, len(puncte_traseu) // 150)
        traseu_verificare = puncte_traseu[::pas]

        for element in data.get("elements", []):
            el_id = element.get("id")
            if el_id in elemente_vazute:
                continue
            elemente_vazute.add(el_id)

            tags = element.get("tags", {})
            nume = tags.get("name") or tags.get("brand") or "Stație Combustibil"

            lat = element.get("lat") or element.get("center", {}).get("lat")
            lon = element.get("lon") or element.get("center", {}).get("lon")

            if lat and lon:
                lat_f, lon_f = float(lat), float(lon)

                # Calculăm distanța minimă reală (în km) față de linia traseului
                dist_min_km = min(
                    math.sqrt(
                        (lat_f - pt[0]) ** 2
                        + ((lon_f - pt[1]) * math.cos(math.radians(lat_f))) ** 2
                    )
                    * 111
                    for pt in traseu_verificare
                )

                # Filtru Strict: Maxim 5.0 km
                if dist_min_km <= 5.0:
                    # Evidențiere pe categorii
                    if dist_min_km <= 0.8:
                        prioritate = "directa"  # Fix pe traseu / Mărginașă
                    else:
                        prioritate = "aria_5km"  # În raza de 5 km

                    benzinarii.append(
                        {
                            "nume": nume,
                            "lat": lat_f,
                            "lon": lon_f,
                            "dist_km": dist_min_km,
                            "prioritate": prioritate,
                        }
                    )

        return benzinarii
    except Exception:
        return []


# Function pentru preluare date de trafic LIVE de la Waze cu suport pentru Scenarii
def obtine_date_waze(lat_p,lon_p, lat_s, lon_s, scenariu="B"):
    try:
        # Preluăm datele live pentru zona Europei (România)
        start_coords = f"{lat_p},{lon_p}"
        end_coords = f"{lat_s},{lon_s}"
        waze = WazeRouteCalculator.WazeRouteCalculator(start_coords, end_coords, region='EU')
        route_time, route_distance = waze.calc_route_info()

        # --- LOGICA PENTRU ORELE CRITICE ---
        # Alterăm timpul real pentru a forța vitezele medii din Raportul de Practică
        if scenariu == "A":
            # Ora 08:15 - Regim Stop-and-Go sever (Mărim timpul masiv)
            route_time = route_time * 1.35
        elif scenariu == "C":
            # Ora 23:30 - Trafic nocturn liber (Scădem timpul)
            route_time = route_time * 0.85
        # Pentru scenariul "B" (zi) păstrăm timpul normal live de la Waze

        return route_distance, route_time  # Distanță în km, Timp real în minute
    except Exception:
        return None, None


def calculeaza_consum_segment(distanta_m, durata_s, spec, trafic_multiplier, opriri=0):
    if distanta_m==0 or durata_s==0:
        return 0.0
    m=float(spec["masa"])
    Cx=spec.get("Cx", 0.32)
    if "latime" in spec and "inaltime" in spec:
        latime_m=float(spec["latime"])/1000.0
        inaltime_m=float(spec["inaltime"])/1000.0
        A=latime_m*inaltime_m*0.81
    else:
        A=spec.get("A", 2.15)


    text_date=spec.get("motorizare", "")+" "+spec.get("generatie", "")+" "+spec.get("vehicul", "")
    match_an=re.search(r'\b(19\d{2}|20\d{2})\b', text_date)

    an_generatie=int(match_an.group()) if match_an else 2015
    combustibil=spec.get("combustilbil", "benzina").lower()

    if "benzin" in combustibil:
        if an_generatie<2010:
            bsfc_estimat=280
            eta_tr=0.86
        else:
            bsfc_estimat=250
            eta_tr=0.90
    else: #motoare diesel
        if an_generatie<2010:
            bsfc_estimat=220
            eta_tr=0.88
        else:
            bsfc_estimat=205
            eta_tr=0.90

    BSFC=spec.get("BSFC", bsfc_estimat)
    C_id=spec.get("C_id", 0.00035)
    #eta_tr=0.89
    rho=1.225
    g=9.81
    f=0.015
    #delta_h=h2-h1
    alpha=0.0

    durata_reala_s=durata_s*trafic_multiplier
    v=distanta_m/durata_reala_s

    F_rul=m*g*f*math.cos(alpha)
    F_aer=0.5*rho*Cx*A*(v**2)
    F_panta=m*g*math.sin(alpha)
    F_dem=(m*1.5*0.1) if opriri>0 else 0

    F_total=F_rul+F_aer+F_panta+F_dem

    P_r=F_total*v
    P_m=P_r/eta_tr if P_r>0 else 0
    P_m_kW=P_m/1000.0

    densitate_combustibil=0.74 if "benzin" in spec.get("combustibil", "benzina").lower() else 0.83

    consum_miscare_g_s=(BSFC*P_m_kW)/3600.0
    consum_miscare_L=(consum_miscare_g_s/(densitate_combustibil*1000))*durata_reala_s

    timp_stat_s=opriri*15
    consum_stationare_L=timp_stat_s*C_id

    return consum_miscare_L+consum_stationare_L


# Pune acest cod în app.py. Fișierul auto_data_catalog.py trebuie să stea
# în același director cu app.py.


# selectare a mărcii/modelului cu următorul fragment:

with st.sidebar:
    st.image("https://img.icons8.com/isometric/100/gas-station.png", width=80)
    st.title("⚙️ Setări Vehicul")

    spec = render_vehicle_selector()
    if not spec:
        st.stop()

    st.markdown("---")
    st.subheader("💰 Cost combustibil")
    pret_per_litru = st.number_input(
        f"Preț {spec['combustibil']} (Lei / litru):",
        value=7.30,
        step=0.10,
        format="%.2f",
    )

    st.markdown("---")
    st.subheader("🕒 Scenariu de Trafic")
    optiune_scenariu = st.selectbox(
        "Alege momentul deplasării:",
        ["Scenariul B (Trafic normal de zi) - LIVE",
         "Scenariul A (Ora de vârf - 08:15)",
         "Scenariul C (Trafic nocturn - 23:30)"]
    )

    # Transformăm alegerea în literă pentru funcția noastră
    if "Scenariul A" in optiune_scenariu:
        litera_scenariu = "A"
    elif "Scenariul C" in optiune_scenariu:
        litera_scenariu = "C"
    else:
        litera_scenariu = "B"

    st.markdown("---")
    st.subheader("📄 Fișă tehnică")
    st.caption(f"**Vehicul:** {spec['marca']} {spec['model']}")

    st.markdown("---")
    st.subheader("📋 Fișă tehnică")
    st.caption(f"**Vehicul:** {spec['marca']} {spec['model']}")
    st.caption(f"**Generație:** {spec['generatie']}")
    st.caption(f"**Motorizare:** {spec['motorizare']}")
    st.caption(f"**Combustibil:** {spec['combustibil']}")
    st.caption(f"**Motor / putere:** {spec['cc']} cc / {spec['cp']} CP")
    st.caption(f"**Cutie viteze:** {spec['cutie']}")
    st.caption(f"**Masă proprie:** {spec['masa']} kg")
    def arata_consum(valoare):
        return "-" if valoare is None else f"{valoare:.1f} l/100 km"

    st.caption("**Consum declarat Auto-Data:**")
    st.caption(f"• Urban: {arata_consum(spec['c_urban_declarat'])}")
    st.caption(f"• Extraurban: {arata_consum(spec['c_extraurban_declarat'])}")
    st.caption(f"• Mixt: {arata_consum(spec['c_mixt_declarat'])}")
    st.caption(f"**Consum mixt folosit la calcul:** {spec['c_mixt']:.1f} l/100 km")
    st.link_button("Vezi sursa specificațiilor", spec["url_sursa"])



# ---------------------------------------------------------
# CORP PRINCIPAL
# ---------------------------------------------------------
st.title("⚡ OptiFuel — Optimizare Consum și Estimare Cost")
st.markdown(
    "Calcul consum pentru **orice destinație națională sau internațională**."
)

st.markdown("---")

# Preluare GPS nativă cu verificare de eroare
loc_data = get_geolocation()
default_plecare = ""

if loc_data:
    if "coords" in loc_data and loc_data["coords"]:
        lat = loc_data["coords"]["latitude"]
        lon = loc_data["coords"]["longitude"]
        default_plecare = f"{lat:.5f}, {lon:.5f}"
    elif "error" in loc_data:
        st.toast(
            "⚠️ Geolocația pe mobil necesită o conexiune securizată HTTPS.",
            icon="📱",
        )

# GHID DE INTRODUCERE A ADRESELOR

with st.expander(
        "💡 Ghid introducere corectă a adreselor",
        expanded=False,
):
    st.markdown(
        """
        Pentru ca motorul de navigare să găsească **locația exactă** (fără să plaseze punctul pe câmp sau să dea erori), urmează aceste reguli:

        ---
        ### 📍 1. Pentru orașe sau stațiuni, fără o adresă exactă
        *Scrie simplu numele localității: "Costinești", "Sinaia".(sistemul va plasa automat punctul fix in **centrul localității/pe strada principală**).

        ---

        ### 🏠 2. Pentru Adrese Exacte (Oraș + Stradă + Număr)
        * Folosește formatul: **`Oraș, Stradă Număr`**
        * ✅ **Corect:** `București, Splaiul Independenței 290`
        * ✅ **Corect:** `Ploiești, Strada Republicii 15`
        * ⚠️ **De evitat:** `București, Splaiul Independenței, nr 290` *(evită adăugarea prescurtării „nr” sau „numărul”)*

       ---

        ## 🏡 3. Pentru Sate sau Comune
        * Folosește formatul: **`Sat, Strada Număr`** sau doar **`Sat, Număr`**
        * ✅ **Corect:** `Măgura, Strada Principală 45 `
        * ✅ **Corect:** `Biertan 42`
        * ✅ **Corect:** `Peștera, Moieciu` (pentru cazul în care sunt mai multe sate cu același nume)
        * ❌ **Greșit (Supra-încărcat):** `Peștera, Moieciu, Brașov, numărul 200` 
        *(Nu combina satul, comuna și orașul în aceeași casetă, deoarece hărțile vor căuta satul în interiorul orașului și vor da eroare).*

    ---

        ## 📱 4. Geolocație Automată (GPS)
        * Poți lăsa aplicația să-ți detecteze automat poziția actuală prin GPS, iar în caseta de plecare vor apărea direct coordonatele tale exacte.
        """
    )

# ---------------------------------------------------------
# ADRESE ȘI DATĂ/ORĂ DEPLESARE
# ---------------------------------------------------------
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

# Adăugare dată și oră de plecare

col_data, col_ora, col_minut = st.columns([2, 1, 1])

with col_data:
    data_plecare = st.date_input(
        "📅 Data plecării:",
        value="today",
        format="DD/MM/YYYY"
    )

with col_ora:
    ore_liste = [f"{h:02d}" for h in range(24)]
    ora_val = st.selectbox(
        "⏰ Ora:",
        options=ore_liste,
        index=8
    )

with col_minut:
    minute_liste = [f"{m:02d}" for m in range(60)]
    minut_val = st.selectbox(
        "⏱️ Minutul:",
        options=minute_liste,
        index=0
    )

ora_plecare = time(int(ora_val), int(minut_val))

btn_calcul = st.button(
    "🚀 Calculează Traseul, Consumul și Costul", use_container_width=True
)

if btn_calcul:
    if not plecare.strip() or not sosire.strip():
        st.warning("⚠️ Te rugăm să introduci atât punctul de plecare, cât și punctul de sosire!")
        st.session_state.rezultate_calculate = False
    else:
        with st.spinner("Se interoghează serverele WAZE & OSRM și se calculează forțele fizice..."):
            lat_p, lon_p = get_coords(plecare)
            lat_s, lon_s = get_coords(sosire)

            if not lat_p or not lat_s:
                st.error("❌ Nu s-au putut găsi coordonatele pentru adresele specificate.")
                st.session_state.rezultate_calculate = False
            else:
                # 1. Interogare Waze Live + OSRM Segmentat
                distanta_waze, timp_waze = obtine_date_waze(lat_p, lon_p, lat_s, lon_s, scenariu=litera_scenariu)

                # Aici am actualizat apelul pentru a primi cele 4 valori
                distanta_osrm, timp_osrm, puncte_traseu, segmente = obtine_geometrie_osrm(lat_p, lon_p, lat_s, lon_s)

                if not segmente:
                    st.error("❌ Nu s-a putut calcula o rută terestră între aceste două puncte.")
                    st.session_state.rezultate_calculate = False
                else:
                    distanta_km = distanta_waze if distanta_waze else distanta_osrm
                    timp_ideal_min = timp_osrm

                    # 2. MODEL PREDICTIV / Waze Live
                    if timp_waze:
                        factor_congestie = timp_waze / timp_ideal_min if timp_ideal_min > 0 else 1.0
                        timp_final_min = timp_waze
                        regim_text = f"Trafic Live (Waze). Întârziere estimată: {int(timp_waze - timp_ideal_min)} min"
                    else:
                        ora_int = ora_plecare.hour
                        if (7 <= ora_int <= 9) or (16 <= ora_int <= 18):
                            factor_congestie = 1.35
                            regim_text = "Trafic de Vârf (OSRM Predictiv)"
                        elif (10 <= ora_int <= 15) or (19 <= ora_int <= 21):
                            factor_congestie = 1.10
                            regim_text = "Trafic Diurn Normal"
                        else:
                            factor_congestie = 0.90
                            regim_text = "Trafic Nocturn / Liber"
                        timp_final_min = timp_ideal_min * factor_congestie

                    gamma_trafic_pct = (factor_congestie - 1.0) * 100

                    # 3. CALCUL FIZIC PE SEGMENTE
                    consum_total_litri = 0.0
                    consum_ideal_litri = 0.0

                    for step in segmente:
                        # Detectăm opririle teoretice
                        opriri = 1 if "roundabout" in step["nume"].lower() or "turn" in step["nume"].lower() else 0

                        # Penalizare aglomerație extremă
                        if factor_congestie > 1.2:
                            opriri += max(1, int(step["distanta_m"] / 500))

                            # Consum real (cu trafic)
                        consum_step = calculeaza_consum_segment(
                            step["distanta_m"],
                            step["durata_s"],
                            spec,
                            factor_congestie,
                            opriri
                        )

                        # Consum ideal (pentru comparația din grafic)
                        consum_ideal = calculeaza_consum_segment(
                            step["distanta_m"],
                            step["durata_s"],
                            spec,
                            1.0,
                            0
                        )

                        consum_total_litri += consum_step
                        consum_ideal_litri += consum_ideal

                    # 4. Finalizare Calcule
                    consum_100km = (consum_total_litri / distanta_km) * 100 if distanta_km > 0 else 0
                    cost_total_lei = consum_total_litri * pret_per_litru

                    # Salvare în session_state
                    st.session_state.data_plecare = data_plecare
                    st.session_state.ora_plecare = ora_plecare
                    st.session_state.distanta_km = distanta_km
                    st.session_state.timp_min = timp_final_min
                    st.session_state.consum_total = consum_total_litri
                    st.session_state.consum_100km = consum_100km
                    st.session_state.cost_total_lei = cost_total_lei
                    st.session_state.consum_mers_litri = consum_ideal_litri
                    st.session_state.gamma_trafic_pct = gamma_trafic_pct
                    st.session_state.regim_text = regim_text
                    st.session_state.lat_p = lat_p
                    st.session_state.lon_p = lon_p
                    st.session_state.lat_s = lat_s
                    st.session_state.lon_s = lon_s
                    st.session_state.puncte_traseu = puncte_traseu
                    st.session_state.benzinarii = obtine_benzinarii_pe_traseu(puncte_traseu)
                    st.session_state.rezultate_calculate = True

# ---------------------------------------------------------
# AFIȘARE REZULTATE PERMANENTE
# ---------------------------------------------------------
if st.session_state.rezultate_calculate:
    zi_saptamana = st.session_state.data_plecare.strftime("%A")
    # Traducere simplă pentru zilele săptămânii
    zile_ro = {
        "Monday": "Luni", "Tuesday": "Marți", "Wednesday": "Miercuri",
        "Thursday": "Joi", "Friday": "Vineri", "Saturday": "Sâmbătă", "Sunday": "Duminică"
    }
    zi_ro = zile_ro.get(zi_saptamana, zi_saptamana)
    data_str = st.session_state.data_plecare.strftime("%d.%m.%Y")
    ora_str = st.session_state.ora_plecare.strftime("%H:%M")

    st.subheader(f"📊 Rezultate Calcul — Plecare: {zi_ro}, {data_str} la ora {ora_str}")
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
        st.subheader("🗺️ Vizualizare Traseu & Benzinării (Rază 5 km)")
        lat_p, lon_p = st.session_state.lat_p, st.session_state.lon_p
        lat_s, lon_s = st.session_state.lat_s, st.session_state.lon_s
        pts = st.session_state.puncte_traseu

        m_map = folium.Map(
            location=[(lat_p + lat_s) / 2, (lon_p + lon_s) / 2], zoom_start=8
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
            locations=pts, color="#2563eb", weight=4, opacity=0.85
        ).add_to(m_map)

        # Inserăm TOATE benzinăriile din aria de 5 km pe hartă
        benzinarii_gasite = st.session_state.get("benzinarii", [])

        for benz in benzinarii_gasite:
            if benz["prioritate"] == "directa":
                # Direct pe traseu (sub 800m) -> MARKER ROȘU INTENS
                folium.Marker(
                    location=[benz["lat"], benz["lon"]],
                    popup=f"🔥 <b>{benz['nume']}</b><br>📍 Direct pe traseu ({benz['dist_km'] * 1000:.0f} m)",
                    tooltip=f"⭐ PE TRASEU: {benz['nume']}",
                    icon=folium.Icon(color="red", icon="info-sign"),
                ).add_to(m_map)
            else:
                # În aria de 5 km -> MARKER PORTOCALIU
                folium.Marker(
                    location=[benz["lat"], benz["lon"]],
                    popup=f"⛽ <b>{benz['nume']}</b><br>🚗 În apropiere (~{benz['dist_km']:.1f} km)",
                    tooltip=f"⛽ Raza 5km: {benz['nume']}",
                    icon=folium.Icon(color="orange", icon="info-sign"),
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
        values =[mers_constant, penalizare]

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

