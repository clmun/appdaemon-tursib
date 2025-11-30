# ===============================================================
#  TursibStationSensorMulti – AppDaemon Sensor pentru Tursib
#  Versiune: v0.05 (final pentru carduri)
#  Fix: prag <60s = "Acum" direct in minutes + rotunjire in sus
# ===============================================================

import datetime
import math
import requests
from bs4 import BeautifulSoup
import hassapi as hass


class TursibStationSensorMulti(hass.Hass):
    VERSION = "v0.05"

    def initialize(self):
        self.stations = self.args.get("stations", {})
        self.log(f"[INFO] Tursib AppDaemon Sensor {self.VERSION} pornit ({len(self.stations)} statii)")

        # scraping complet imediat la pornire și apoi la fiecare 6h
        self.update_all_stations({})
        self.run_every(self.update_all_stations, datetime.datetime.now(), 6 * 3600)

        # refresh la minut pentru minutes și ordine
        self.run_every(self.refresh_next_departures, datetime.datetime.now(), 60)

    # -------------------------
    def _minutes_and_dt(self, now_dt: datetime.datetime, hhmm: str):
        try:
            h, m = map(int, hhmm.split(":"))
        except Exception:
            return None, None
        dep_dt = datetime.datetime.combine(now_dt.date(), datetime.time(h, m))
        if dep_dt < now_dt:
            dep_dt += datetime.timedelta(days=1)
        delta = (dep_dt - now_dt).total_seconds()
        if delta < 0:
            return None, None
        if delta < 60:
            minutes = "Acum"
        else:
            minutes = str(math.ceil(delta / 60))
        return minutes, dep_dt

    def _sorted_departures(self, departures, now_dt):
        occ = []
        for d in departures:
            minutes, dep_dt = self._minutes_and_dt(now_dt, d.get("departure", ""))
            if minutes is None:
                continue
            item = {
                "line": d.get("line", "?"),
                "destination": d.get("destination", "?"),
                "departure": d.get("departure", ""),
                "minutes": minutes,   # direct "Acum" sau număr ca string
                "scheduled_time": d.get("departure", "")
            }
            occ.append((dep_dt, item))
        occ.sort(key=lambda x: x[0])
        return [x[1] for x in occ]

    # ============================
    def update_all_stations(self, kwargs):
        for station_id, name in self.stations.items():
            try:
                self.update_station(station_id, name)
            except Exception as e:
                self.log(f"[ERROR] Eroare la statia {name}: {e}")

    def update_station(self, station_id, station_name):
        url = f"https://tursib.ro/s/{station_id}?arrivals=on"
        self.log(f"[INFO] Actualizare statie {station_name} ({station_id})")
        self.log(f"[INFO] Preiau date din {url} ...")

        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
        except Exception as e:
            self.log(f"[WARN] Nu pot accesa {url}: {e}")
            return

        data = self.parse_html_to_json(response.text)
        if not data:
            self.log(f"[WARN] Nu am putut extrage date pentru {station_name}")
            return

        weekday = datetime.datetime.now().weekday()
        if weekday < 5:
            program_key = "luni-vineri"
            program_label = "Luni–Vineri"
        elif weekday == 5:
            program_key = "sambata"
            program_label = "Sambata"
        else:
            program_key = "duminica"
            program_label = "Duminica"

        departures_raw = data.get(program_key, [])
        if not departures_raw:
            self.log(f"[WARN] Nu exista plecari pentru {station_name} ({program_label})")
            return

        now = datetime.datetime.now()
        departures_sorted = self._sorted_departures(departures_raw, now)

        attributes = {
            "version": self.VERSION,
            "station": station_name,
            "program": program_label,
            "departures": departures_sorted,
            "last_update": datetime.datetime.now().isoformat(),
        }

        state = departures_sorted[0]["departure"] if departures_sorted else "n/a"
        entity_id = f"sensor.tursib_station_{station_id}"
        self.set_state(entity_id, state=state, attributes=attributes)

        self.log(f"[INFO] {entity_id} actualizat ({len(departures_sorted)} plecari - program {program_label})")

    # ============================
    def refresh_next_departures(self, kwargs):
        now = datetime.datetime.now()
        for station_id in self.stations.keys():
            entity_id = f"sensor.tursib_station_{station_id}"
            try:
                raw = self.get_state(entity_id, attribute="all")
                if not raw:
                    continue
                attributes = raw.get("attributes", raw)
                old_departures = attributes.get("departures", [])
                if not isinstance(old_departures, list) or not old_departures:
                    continue

                departures_sorted = self._sorted_departures(old_departures, now)

                attributes["departures"] = departures_sorted
                attributes["last_update"] = datetime.datetime.now().isoformat()
                new_state = departures_sorted[0]["departure"] if departures_sorted else "n/a"
                self.set_state(entity_id, state=new_state, attributes=attributes)
            except Exception as e:
                self.log(f"[ERROR] Eroare in refresh_next_departures pentru {entity_id}: {e}")

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
