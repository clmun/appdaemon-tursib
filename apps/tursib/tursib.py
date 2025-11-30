# ===============================================================
#  TursibStationSensorMulti – AppDaemon Sensor pentru Tursib
#  Versiune: v0.36.1
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
    VERSION = "v0.36.1"

    def initialize(self):
        # rulează refresh la fiecare 60 secunde
        self.run_every(self.refresh_next_departures, datetime.datetime.now(), 60)

    # -------------------------
    # helper: minute până la următoarea apariție a HH:MM
    # -------------------------
    def _minutes_until(self, now_dt: datetime.datetime, hhmm: str):
        try:
            dep_time = datetime.datetime.strptime(hhmm, "%H:%M").time()
            dep_dt = datetime.datetime.combine(now_dt.date(), dep_time)
            # dacă ora e deja trecută, mutăm în ziua următoare
            if dep_dt < now_dt:
                dep_dt += datetime.timedelta(days=1)
            minutes = int((dep_dt - now_dt).total_seconds() / 60)
            return minutes, dep_dt
        except Exception as e:
            self.log(f"⚠️ Eroare la calcul minute pentru {hhmm}: {e}")
            return None, None

    # ============================
    def update_all_stations(self, kwargs):
        try:
            for station_id, station_name in self.args.get("stations", {}).items():
                self.update_station(station_id, station_name)
        except Exception as e:
            self.write_changelog("ALL", 0, f"EROARE: {e}")

    def update_station(self, station_id, station_name):
        try:
            # aici faci request către API Tursib
            html = requests.get(f"https://tursib.ro/trasee/statie/{station_id}").text
            data = self.parse_html_to_json(html)
            departures_sorted = []

            if data and "departures" in data:
                now_dt = datetime.datetime.now()
                for dep in data["departures"]:
                    minutes, dep_dt = self._minutes_until(now_dt, dep["departure"])
                    departures_sorted.append({
                        "line": dep["line"],
                        "destination": dep["destination"],
                        "departure": dep["departure"],
                        "minutes": minutes
                    })

            attributes = {
                "friendly_name": f"Tursib {station_name}",
                "departures": departures_sorted
            }
            self.set_state(f"sensor.tursib_station_{station_id}", state="ok", attributes=attributes)
            self.write_changelog(station_name, len(departures_sorted))
        except Exception as e:
            self.write_changelog(station_name, 0, f"EROARE: {e}")

    # ============================
    def refresh_next_departures(self, kwargs):
        try:
            self.update_all_stations(kwargs)
        except Exception as e:
            self.log(f"⚠️ Eroare în refresh_next_departures: {e}")

    # ============================
    def parse_html_to_json(self, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
            departures = []
            for row in soup.select("table tr"):
                cols = [c.get_text(strip=True) for c in row.find_all("td")]
                if len(cols) >= 3:
                    departures.append({
                        "line": cols[0],
                        "destination": cols[1],
                        "departure": cols[2]
                    })
            data = {"departures": departures}
            return data if any(data.values()) else None
        except Exception as e:
            self.log(f"⚠️ Eroare la parse_html_to_json: {e}")
            return None

    # ============================
    def save_to_cache(self, station_id, data):
        try:
            if not hasattr(self, "cache_path"):
                self.cache_path = os.path.join(self.config_dir, "tursib_cache.json")
            try:
                with open(self.cache_path, "r") as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
            cache[str(station_id)] = data
            with open(self.cache_path, "w") as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            self.log(f"⚠️ Eroare la salvarea cache: {e}")

    # ============================
    def write_changelog(self, station_name, departures_count, status="OK"):
        try:
            self.log(f"[{station_name}] {departures_count} plecări – {status}")
        except Exception as e:
            self.log(f"⚠️ Eroare la salvarea changelog: {e}")
