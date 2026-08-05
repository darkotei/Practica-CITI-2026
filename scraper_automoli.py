import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.automoli.com"
START_URL = "https://www.automoli.com/ro/vehicles/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

IGNORA_CUVINTE = {
    "română",
    "romana",
    "vehicles",
    "vehicule",
    "despre",
    "contact",
    "termeni",
    "politica",
    "autentificare",
    "conectare",
    "limba",
    "english",
    "deutsch",
    "home",
    "acasa",
}


def curata_text(text):
    if not text:
        return ""
    text = re.sub(
        r"Imagine\s*indisponibil[ăa]?", "", text, flags=re.IGNORECASE
    )
    return re.sub(r"\s+", " ", text).strip()


def parse_float(val_str):
    if not val_str:
        return 0.0
    match = re.search(r"(\d+([.,]\d+)?)", str(val_str))
    return float(match.group(1).replace(",", ".")) if match else 0.0


def extrage_detalii_motorizare(url_motorizare):
    try:
        res = requests.get(url_motorizare, headers=HEADERS, timeout=6)
        if res.status_code != 200:
            return None
        soup = BeautifulSoup(res.text, "html.parser")
        text_full = soup.get_text()

        combustibil = "Benzină"
        if re.search(r"Diesel|Motorin[ăa]", text_full, re.I):
            combustibil = "Diesel"
        elif re.search(r"Hibrid|Hybrid", text_full, re.I):
            combustibil = "Hibrid"
        elif re.search(r"Electric", text_full, re.I):
            combustibil = "Electric"

        cp, cc, c_urban, c_extra, c_mixt, masa, cutie = (
            0,
            0,
            0.0,
            0.0,
            0.0,
            1350,
            "Manuală",
        )
        if re.search(r"Automat|DSG|Steptronic|Tiptronic", text_full, re.I):
            cutie = "Automată"

        for r in soup.find_all(["tr", "li", "div"]):
            t = r.get_text()
            if ("CP" in t or "hp" in t or "Putere" in t) and cp == 0:
                m_cp = re.search(r"(\d+)\s*(CP|hp|kW)", t, re.I)
                if m_cp:
                    val = int(m_cp.group(1))
                    if "kW" in m_cp.group(2):
                        val = int(val * 1.36)
                    if 30 <= val <= 1200:
                        cp = val

            if ("cm³" in t or "cc" in t or "Capacitate" in t) and cc == 0:
                m_cc = re.search(r"(\d{3,4})\s*(cm³|cc)", t, re.I)
                if m_cc:
                    cc = int(m_cc.group(1))

            if "urban" in t.lower() and "l/100" in t.lower():
                c_urban = parse_float(t)
            elif "extraurban" in t.lower() and "l/100" in t.lower():
                c_extra = parse_float(t)
            elif (
                "mixt" in t.lower() or "combined" in t.lower()
            ) and "l/100" in t.lower():
                c_mixt = parse_float(t)

            if "kg" in t.lower() and ("masă" in t.lower() or "masa" in t.lower()):
                m_m = re.search(r"(\d{3,4})\s*kg", t, re.I)
                if m_m:
                    masa = int(m_m.group(1))

        if c_mixt == 0.0 and c_urban > 0 and c_extra > 0:
            c_mixt = round((c_urban + c_extra) / 2, 1)

        if c_mixt == 0.0:
            c_mixt = 5.5
        if c_urban == 0.0:
            c_urban = round(c_mixt * 1.25, 1)
        if c_extra == 0.0:
            c_extra = round(c_mixt * 0.8, 1)

        return {
            "combustibil": combustibil,
            "cc": cc if cc > 0 else 1498,
            "cp": cp if cp > 0 else 110,
            "cutie": cutie,
            "masa": masa,
            "cx": 0.30,
            "aria": 2.2,
            "bsfc": 210 if combustibil == "Diesel" else 245,
            "c_urban": c_urban,
            "c_extraurban": c_extra,
            "c_mixt": c_mixt,
        }
    except Exception:
        return None


def Reia_scrape():
    # 1. Încarcă baza de date salvată anterior (dacă există)
    db = {}
    fisier_json = "vehicule_database.json"
    if os.path.exists(fisier_json):
        try:
            with open(fisier_json, "r", encoding="utf-8") as f:
                db = json.load(f)
            print(f"📂 S-au încărcat {len(db)} mărci salvate anterior.")
        except Exception as e:
            print(f"⚠️ Nu s-a putut citi fișierul JSON vechi: {e}")
            db = {}

    try:
        res = requests.get(START_URL, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as e:
        print(f"❌ Conexiunea la site nu a reușit: {e}")
        return

    marci_list = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/ro/vehicles/" in href and href != "/ro/vehicles/":
            full_url = href if href.startswith("http") else BASE_URL + href
            nume_marca = curata_text(a.get_text())

            if (
                nume_marca
                and len(nume_marca) > 1
                and nume_marca.lower() not in IGNORA_CUVINTE
                and (nume_marca, full_url) not in marci_list
            ):
                marci_list.append((nume_marca, full_url))

    print(f"📋 S-au găsit {len(marci_list)} mărci în total pe site.")

    for idx, (marca_nume, marca_url) in enumerate(marci_list, 1):
        # 2. VERIFICARE: Dacă marca există deja și are modele salvate, o sărim!
        if marca_nume in db and isinstance(db[marca_nume], dict) and len(db[marca_nume]) > 0:
            print(f"⏩ [{idx}/{len(marci_list)}] Sărit: {marca_nume} (deja salvată)")
            continue

        print(f"\n🔄 [{idx}/{len(marci_list)}] Descărcare Marcă Lipsă: {marca_nume}...")

        try:
            res_m = requests.get(marca_url, headers=HEADERS, timeout=6)
            if res_m.status_code != 200:
                continue
            soup_m = BeautifulSoup(res_m.text, "html.parser")

            modele_links = []
            for a_mod in soup_m.find_all("a", href=True):
                href_mod = a_mod["href"]
                if (
                    "/ro/vehicles/" in href_mod
                    and href_mod != marca_url
                    and href_mod != "/ro/vehicles/"
                ):
                    nume_model = curata_text(a_mod.get_text())
                    url_model = (
                        href_mod
                        if href_mod.startswith("http")
                        else BASE_URL + href_mod
                    )
                    if (
                        nume_model
                        and nume_model.lower() not in IGNORA_CUVINTE
                        and (nume_model, url_model) not in modele_links
                    ):
                        modele_links.append((nume_model, url_model))

            if not modele_links:
                continue

            db[marca_nume] = {}

            for model_nume, model_url in modele_links[:8]:
                db[marca_nume][model_nume] = {}
                try:
                    res_sub = requests.get(model_url, headers=HEADERS, timeout=6)
                    soup_sub = BeautifulSoup(res_sub.text, "html.parser")

                    motorizari_links = []
                    for a_mot in soup_sub.find_all("a", href=True):
                        href_mot = a_mot["href"]
                        if (
                            "/ro/vehicles/" in href_mot
                            and href_mot not in [model_url, marca_url]
                        ):
                            label_mot = curata_text(a_mot.get_text())
                            url_mot = (
                                href_mot
                                if href_mot.startswith("http")
                                else BASE_URL + href_mot
                            )
                            if (
                                label_mot
                                and (label_mot, url_mot) not in motorizari_links
                            ):
                                motorizari_links.append((label_mot, url_mot))

                    if motorizari_links:
                        for mot_nume, mot_url in motorizari_links[:4]:
                            spec = extrage_detalii_motorizare(mot_url)
                            if spec:
                                nume_final_mot = (
                                    f"{mot_nume} ({spec['cp']} CP)"
                                    if f"{spec['cp']} CP" not in mot_nume
                                    else mot_nume
                                )
                                db[marca_nume][model_nume][nume_final_mot] = spec
                            time.sleep(0.2)
                    else:
                        spec = extrage_detalii_motorizare(model_url)
                        if spec:
                            db[marca_nume][model_nume][f"Standard ({spec['cp']} CP)"] = spec
                except Exception:
                    pass

            # 3. Salvare Imediată pe disc după fiecare marcă nouă adăugată
            with open(fisier_json, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False, indent=4)
            print(f"  ✓ [{marca_nume}] adăugată cu succes ({len(db[marca_nume])} modele)")

            # Pauză scurtă de protecție
            time.sleep(1.0)

        except Exception as e:
            print(f"  ❌ Eroare la {marca_nume}: {e}")

    print("\n🎉 Proces complet încheiat! Toate mările au fost completate.")


if __name__ == "__main__":
    Reia_scrape()