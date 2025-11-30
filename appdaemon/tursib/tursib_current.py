# ===============================================================
#  TursibStationSensorMulti – AppDaemon Sensor pentru Tursib
#  Versiune: v0.36
#  Fix: corectare selecție "next" și calcul minute (inclusiv peste noapte)
# ===============================================================

import datetime
import copy
import json
import os
import requests
from bs4 import BeautifulSoup

import hassapi as hass


class TursibStationSensorMulti(hass.Hass):
    VERSION = "v0.36"

    def initialize(self):
        self.stations = self.args.get("stations", {})
        self.cache_path = "/config/tursib_cache.json"
        self.changelog_path = "/config/tursib_changelog.json"

        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w") as f:
                json.dump({}, f)

        self.log(f"🚌 Tursib AppDaemon Sensor {self.VERSION} pornit ({len(self.stations)} stații)")

        # scraping complet la start și la 6h
        self.run_in(self.update_all_stations, 5)
        self.run_every(self.update_all_stations, datetime.datetime.now(), 6 * 3600)

        # refresh la minut pentru next_1/2/3 și minutes
        self.run_every(self.refresh_next_departures, datetime.datetime.now(), 60)

    # -------------------------
    # helper: minute până la următoarea apariție a HH:MM
    # -------------------------
    def _minutes_until(self, now_dt: datetime.datetime, hhmm: str):
        try:
            h, m = map(int, hhmm.split(":"))
        except Exception:
            return None, None
        dep_dt = datetime.datetime.combine(now_dt.date(), datetime.time(h, m))
        # dacă ora a trecut azi, considerăm următoarea apariție (mâine)
        if dep_dt < now_dt:
            dep_dt = dep_dt + datetime.timedelta(days=1)
        minutes = int((dep_dt - now_dt).total_seconds() / 60)
        return minutes, dep_dt

    # ============================
    def update_all_stations(self, kwargs):
        for station_id, name in self.stations.items():
            try:
                self.log(f"🔄 Actualizare completă stație {name} ({station_id})")
                self.update_station(station_id, name)
            except Exception as e:
                self.log(f"❌ Eroare la stația {name}: {e}")
                self.write_changelog(name, 0, f"EROARE: {e}")

    def update_station(self, station_id, station_name):
        url = f"https://tursib.ro/s/{station_id}?arrivals=on"
        self.log(f"🌐 Preiau date noi din {url} ...")

        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
        except Exception as e:
            self.log(f"⚠️ Nu pot accesa {url}: {e}")
            self.write_changelog(station_name, 0, f"Conexiune eșuată: {e}")
            return

        data = self.parse_html_to_json(response.text)
        if not data:
            self.log(f"⚠️ Nu am putut extrage date valide pentru {station_name}")
            self.write_changelog(station_name, 0, "Date invalide")
            return

        # alege programul corespunzător zilei curente
        weekday = datetime.datetime.now().weekday()
        if weekday < 5:
            program_key = "luni-vineri"
            program_label = "Luni–Vineri"
        elif weekday == 5:
            program_key = "sambata"
            program_label = "Sâmbătă"
        else:
            program_key = "duminica"
            program_label = "Duminică"

        departures = data.get(program_key, [])
        if not departures:
            self.log(f"⚠️ Nu există plecări pentru {station_name} ({program_label})")
            self.write_changelog(station_name, 0, "Fără plecări")
            return

        # construim lista de 'occurrences' = (dep_dt, item, minutes)
        now = datetime.datetime.now()
        occurrences = []
        for d in departures:
            minutes, dep_dt = self._minutes_until(now, d["departure"])
            if minutes is None:
                continue
            # clonăm obiectul pentru a nu modifica originalul sursă
            item = {"line": d.get("line", "?"), "destination": d.get("destination", "?"), "departure": d["departure"]}
            item["minutes"] = minutes  # prima estimare
            occurrences.append((dep_dt, item, minutes))

        # sortăm după timestamp (viitor)
        occurrences.sort(key=lambda x: x[0])

        # pregătim lista 'departures_sorted' (aceasta va fi atributul public)
        departures_sorted = [occ[1] for occ in occurrences]

        # următoarele 3 plecări (dacă nu sunt, luăm primele disponibile)
        next_three = departures_sorted[:3] if departures_sorted else []

        # pregătim atributele next_1..3
        next_attrs = {}
        for i, dep in enumerate(next_three):
            next_attrs[f"next_{i+1}"] = dep["departure"]
            next_attrs[f"line_{i+1}"] = dep["line"]
            next_attrs[f"destination_{i+1}"] = dep["destination"]
            next_attrs[f"minutes_to_next_{i+1}"] = dep["minutes"]

        attributes = {
            "version": self.VERSION,
            "station": station_name,
            "program": program_label,
            "departures": departures_sorted,
            "last_cache_update": datetime.datetime.now().isoformat(),
            **next_attrs,
        }

        state = next_three[0]["departure"] if next_three else departures_sorted[0]["departure"]
        entity_id = f"sensor.tursib_station_{station_id}"
        self.set_state(entity_id, state=state, attributes=attributes)

        self.log(f"✅ {entity_id} actualizat ({len(departures_sorted)} plecări - program {program_label})")
        self.save_to_cache(station_id, attributes)
        self.write_changelog(station_name, len(departures_sorted))

    # ============================
    def refresh_next_departures(self, kwargs):
        """Actualizează la minut: recalculează minutes pentru fiecare departure și alege primele 3 reale."""
        now = datetime.datetime.now()
        for station_id in self.stations.keys():
            entity_id = f"sensor.tursib_station_{station_id}"
            try:
                # get_state(..., attribute="all") returnează dict cu 'attributes' sau similar
                raw = self.get_state(entity_id, attribute="all")
                if not raw:
                    continue
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        self.log(f"⚠️ Nu pot decodifica atributele JSON pentru {entity_id}")
                        continue
                # dacă raw conține cheia 'attributes'
                if isinstance(raw, dict) and "attributes" in raw:
                    attributes = raw["attributes"]
                elif isinstance(raw, dict):
                    attributes = raw
                else:
                    continue

                departures = attributes.get("departures", [])
                if not isinstance(departures, list) or not departures:
                    continue

                # recalculăm minutes și dep_dt pentru fiecare
                occurrences = []
                for d in departures:
                    minutes, dep_dt = self._minutes_until(now, d.get("departure", ""))
                    if minutes is None:
                        continue
                    # update obiect afișat
                    d["minutes"] = minutes
                    occurrences.append((dep_dt, d))

                # sortăm după dep_dt și alegem primele 3
                occurrences.sort(key=lambda x: x[0])
                next_three = [occ[1] for occ in occurrences[:3]]

                if not next_three:
                    continue

                # setăm atribute dinamice next_1..3
                for i, dep in enumerate(next_three):
                    attributes[f"next_{i+1}"] = dep["departure"]
                    attributes[f"line_{i+1}"] = dep["line"]
                    attributes[f"destination_{i+1}"] = dep["destination"]
                    attributes[f"minutes_to_next_{i+1}"] = dep["minutes"]

                # actualizăm departures (cu minutes actualizate) și starea
                attributes["departures"] = [occ[1] for occ in occurrences]
                new_state = next_three[0]["departure"]
                self.set_state(entity_id, state=new_state, attributes=attributes)
            except Exception as e:
                self.log(f"⚠️ Eroare în refresh_next_departures pentru {entity_id}: {e}")

    # ============================
    def parse_html_to_json(self, html):
        soup = BeautifulSoup(html, "html.parser")
        data = {"luni-vineri": [], "sambata": [], "duminica": []}
        sections = soup.find_all("div", class_="program")

        for sec in sections:
            header = sec.find("h4")
            if not header:
                continue
            title = header.text.strip().lower()
            if "luni" in title:
                key = "luni-vineri"
            elif "sâmbătă" in title or "sambata" in title:
                key = "sambata"
            elif "duminică" in title or "duminica" in title:
                key = "duminica"
            else:
                continue

            plecari = sec.find_all("div", class_="card-body")
            for p in plecari:
                line_el = p.find("a", class_="traseu-link")
                dir_el = p.find("span", class_="headsign-info")
                times = [t.text.strip() for t in p.find_all("span", class_="h") if ":" in t.text]

                if not times:
                    continue

                line = line_el.text.strip() if line_el else "?"
                direction = dir_el.text.strip() if dir_el else "?"

                for t in times:
                    if len(t) == 5 and ":" in t:
                        data[key].append({"line": line, "destination": direction, "departure": t})

        return data if any(data.values()) else None

    # ============================
    def save_to_cache(self, station_id, data):
        try:
            with open(self.cache_path, "r") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
        cache[str(station_id)] = data
        with open(self.cache_path, "w") as f:
            json.dump(cache, f, indent=2)

    # ============================
    def write_changelog(self, station_name, departures_count, status="OK"):
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "version": self.VERSION,
            "station": station_name,
            "departures_found": departures_count,
            "status": status,
        }

        try:
            if os.path.exists(self.changelog_path):
                with open(self.changelog_path, "r") as f:
                    data = json.load(f)
            else:
                data = {"version": self.VERSION, "log": []}

            data["version"] = self.VERSION
            data["log"].append(entry)
            data["log"] = data["log"][-30:]
            with open(self.changelog_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.log(f"⚠️ Eroare la salvarea changelog: {e}")
