import os
import json
import tempfile
import logging
import subprocess
import asyncio
import threading
import time
import urllib.parse

from flask import Flask, render_template, jsonify, request
import requests
from bs4 import BeautifulSoup
from shazamio import Shazam
import imageio_ffmpeg as ffmpeg_static  # substitui ffmpeg_static

# Configurações
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

RADIO_URL = "https://onlineradiobox.com/ao/"
ON_VERCEL = os.environ.get("VERCEL") == "1"

if ON_VERCEL:
    BASE_DIR = tempfile.gettempdir()  # Vercel: /tmp
else:
    BASE_DIR = os.path.dirname(__file__)

STATIONS_FILE = os.path.join(BASE_DIR, "stations.json")
TMP_TEMPLATE = os.path.join(BASE_DIR, "tmp_{safe}.mp3")

# Monitores ativos
monitors = {}
monitors_lock = threading.Lock()

FFMPEG_BIN = ffmpeg_static.get_ffmpeg_exe()  # caminho para ffmpeg

# ---------- Scraping ----------
def normalize_img(img):
    if not img:
        return None
    img = img.strip()
    if img.startswith("//"):
        return "https:" + img
    if img.startswith("http://"):
        return img.replace("http://", "https://")
    return img

def scrape_radios():
    logging.info("Scraping %s ...", RADIO_URL)
    stations = []
    try:
        r = requests.get(RADIO_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for btn in soup.select("button.b-play.station_play, button.station_play"):
            name = btn.get("radioname") or btn.get("radioName")
            stream = btn.get("stream")
            img = btn.get("radioimg") or btn.get("radioImg")
            if name and stream:
                stations.append({"name": name.strip(), "stream": stream.strip(), "img": normalize_img(img)})
    except Exception as e:
        logging.exception("Scrape error: %s", e)

    # Salva stations.json
    try:
        os.makedirs(os.path.dirname(STATIONS_FILE), exist_ok=True)
        with open(STATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(stations, f, ensure_ascii=False, indent=2)
        logging.info("stations.json saved (%d stations) -> %s", len(stations), STATIONS_FILE)
    except Exception as e:
        logging.exception("Write stations.json error: %s", e)

    return stations

# ---------- Helpers ----------
def safe_name(stream):
    return urllib.parse.quote_plus(stream)[:120]

def record_stream(stream_url, out_file, duration=12):
    cmd = [
        FFMPEG_BIN, "-y", "-i", stream_url,
        "-t", str(duration), "-acodec", "libmp3lame", "-ar", "44100", "-ac", "2",
        out_file, "-loglevel", "error"
    ]
    try:
        subprocess.run(cmd, check=True, timeout=duration + 25)
        return True
    except Exception as e:
        logging.error("ffmpeg record failed: %s", e)
        return False

async def shazam_identify(file_path):
    shazam = Shazam()
    try:
        out = await shazam.recognize_song(file_path)
        track = out.get("track")
        if not track:
            return None
        title = track.get("title") or ""
        artist = track.get("subtitle") or ""
        return {"title": title.strip(), "artist": artist.strip()}
    except Exception as e:
        logging.exception("Shazam identify error: %s", e)
        return None

def itunes_cover(artist, title):
    try:
        q = f"{artist} {title}".strip()
        if not q:
            return None
        params = {"term": q, "limit": 1, "media": "music"}
        url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("resultCount", 0) > 0:
            art = j["results"][0].get("artworkUrl100")
            if art:
                return art.replace("100x100", "600x600") if "100x100" in art else art
    except Exception as e:
        logging.debug("iTunes lookup failed: %s", e)
    return None

# ---------- Monitor Loop ----------
def monitor_loop(stream, station_name, stop_event, info):
    safe = safe_name(stream)
    tmp = TMP_TEMPLATE.format(safe=safe)
    prev_key = None
    logging.info("Monitor started for %s", stream)
    while not stop_event.is_set():
        info["station_name"] = station_name
        ok = record_stream(stream, tmp, duration=12)
        if not ok:
            info.update({"found": False, "title": None, "artist": None, "cover": None})
        else:
            try:
                result = asyncio.run(shazam_identify(tmp))
            except Exception as e:
                logging.exception("identify run error: %s", e)
                result = None

            if result:
                title = result.get("title", "")
                artist = result.get("artist", "")
                key = f"{artist} - {title}"
                if key != prev_key:
                    cover = itunes_cover(artist, title) or None
                    info.update({"found": True, "title": title, "artist": artist, "cover": cover})
                    prev_key = key
                else:
                    info["found"] = True
                    info["title"] = title
                    info["artist"] = artist
                    if not info.get("cover"):
                        info["cover"] = itunes_cover(artist, title)
            else:
                info.update({"found": False, "title": None, "artist": None, "cover": None})

        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except:
            pass

        for _ in range(30):
            if stop_event.is_set():
                break
            time.sleep(1)

    logging.info("Monitor stopped for %s", stream)
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except:
        pass

# ---------- Routes ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/stations")
def get_stations():
    if os.path.exists(STATIONS_FILE):
        try:
            with open(STATIONS_FILE, "r", encoding="utf-8") as f:
                stations = json.load(f)
                stations = [s for s in stations if s.get("name") and s.get("stream")]
                return jsonify(stations)
        except Exception:
            logging.exception("Failed reading stations.json; scraping instead.")
    return jsonify(scrape_radios())

@app.route("/monitor/start", methods=["POST"])
def monitor_start():
    data = request.get_json() or {}
    stream = data.get("stream")
    station_name = data.get("station_name", "") or ""
    if not stream:
        return jsonify({"error": "stream required"}), 400

    with monitors_lock:
        if stream in monitors:
            return jsonify({"started": False, "message": "monitor already running", "info": monitors[stream]["info"]})
        stop = threading.Event()
        info = {"found": False, "title": None, "artist": None, "cover": None, "station_name": station_name}
        t = threading.Thread(target=monitor_loop, args=(stream, station_name, stop, info), daemon=True)
        monitors[stream] = {"thread": t, "stop": stop, "info": info}
        t.start()
        return jsonify({"started": True, "info": info})

@app.route("/monitor/stop", methods=["POST"])
def monitor_stop():
    data = request.get_json() or {}
    stream = data.get("stream")
    if not stream:
        return jsonify({"error": "stream required"}), 400
    with monitors_lock:
        item = monitors.get(stream)
        if not item:
            return jsonify({"stopped": False, "message": "not running"})
        item["stop"].set()
        del monitors[stream]
    return jsonify({"stopped": True})

@app.route("/nowplaying")
def nowplaying():
    stream = request.args.get("stream")
    if not stream:
        return jsonify({"error": "stream required"}), 400
    with monitors_lock:
        item = monitors.get(stream)
        if item:
            return jsonify(item["info"])
    return jsonify({"found": False, "title": None, "artist": None, "cover": None, "station_name": None})

# ---------- Run ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
