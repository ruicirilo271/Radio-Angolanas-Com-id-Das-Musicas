import requests
from bs4 import BeautifulSoup
import json

URL = "https://onlineradiobox.com/ao/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def get_stations():
    response = requests.get(URL, headers=HEADERS)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    stations = []
    for button in soup.find_all("button", class_="b-play station_play"):
        name = button.get("radioname")
        stream = button.get("stream")
        stream_type = button.get("streamtype", "mp3")
        image = button.get("radioimg")
        radio_id = button.get("radioid")

        # Corrigir imagens sem protocolo
        if image and image.startswith("//"):
            image = "https:" + image

        if name and stream:
            stations.append({
                "id": radio_id,
                "name": name.strip(),
                "stream": stream.strip(),
                "type": stream_type,
                "image": image,
                "country": "Angola"
            })

    return stations

if __name__ == "__main__":
    stations = get_stations()

    with open("stations.json", "w", encoding="utf-8") as f:
        json.dump(stations, f, indent=4, ensure_ascii=False)

    print(f"✅ Ficheiro 'stations.json' criado com {len(stations)} rádios angolanas!")
