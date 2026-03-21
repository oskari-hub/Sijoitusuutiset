import feedparser
import requests
import os
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom
# ─────────────────────────────────────────
# RSS-SYÖTTEET
# ─────────────────────────────────────────
RSS_FEEDS = [
    "https://www.nature.com/npjdigitalmed.rss",
    "https://endpts.com/feed",
    "https://statnews.com/feed",
    "https://feeds.kauppalehti.fi/rss/topic/osakkeet",
    "https://feeds.kauppalehti.fi/rss/main",
    "https://di.se/rss",
    "https://borsen.dk/rss",
]

GOOGLE_NEWS_QUERY = (
    "Canatu+OR+ChemoMetec+OR+Nanexa+OR+Sectra"
    "+OR+Stille+OR+%22Detection+Technology%22+OR+Aiforia"
)

RSS_FEEDS += [
    f"https://news.google.com/rss/search?q={GOOGLE_NEWS_QUERY}&hl=sv&gl=SE&ceid=SE:sv",
    f"https://news.google.com/rss/search?q={GOOGLE_NEWS_QUERY}&hl=da&gl=DK&ceid=DK:da",
    f"https://news.google.com/rss/search?q={GOOGLE_NEWS_QUERY}&hl=fi&gl=FI&ceid=FI:fi",
]

# ─────────────────────────────────────────
# CLAUDE-PROMPTI
# ─────────────────────────────────────────
PROMPT_TEMPLATE = """Olet sijoittajan uutisassistentti. Tehtäväsi on analysoida
uutinen ja päättää, onko se relevantti seuraavien yhtiöiden
kannalta:
- Canatu (Suomi)
- ChemoMetec (Tanska)
- Nanexa (Ruotsi)
- Sectra B (Ruotsi)
- Stille (Ruotsi)
- Detection Technology Oyj (Suomi)
- Aiforia Technologies Oyj (Suomi)
Uutinen:
TITLE: {title}
CONTENT: {content}
LANGUAGE: {language}
URL: {url}
Tee seuraavat asiat:
1. Päätä, liittyykö uutinen johonkin yllä listatuista yhtiöistä
tai seuraaviin toimialoihin:
- Korkealaatuiset uudelleenkäytettävät kirurgiset käsi-
instrumentit (sakset, pinsettit, luunpuristimet, kyreetit)
sekä kirurgiset kuvantamispöydät: radiolukentit C-kaari-
pöydät verisuoni-, interventio- ja urologisiin toimenpiteisiin,
TrueFreeFloat-teknologia, matalanannoksen fluoroskopia
- Lääketieteellinen kuvantaminen ja terveydenhuollon IT:
enterprise-kuvantamisratkaisut (PACS, VNA), radiologia,
rintasyöpäseulonta, digitaalinen patologia, kardiologia,
oftalmologia ja ortopedia; pilvipohjaiset SaaS-ratkaisut
terveydenhuollolle; tekoälyn integrointi kliinisiin
kuvantamistyönkulkuihin
- Tekoälyavusteinen digitaalinen patologia:
syväoppimismallit histologisten kudosnäytteiden analyysiin,
tumoreiden luokittelu ja gradeeraus, biomarkkereiden
kvantifiointi, CE-IVD-merkityt kliiniset AI-sovellukset,
patologian työnkulkujen automatisointi
- Fluorenssikuvantamiseen perustuva automatisoitu solunlaskenta
ja soluelinkelpoisuuden määritys: kasettipohjainen
näytteenkäsittely, image cytometry, solulaskenta
nisäkässoluille, CAR-T-soluille, kantasoluille ja
hyönteissoluille; GMP/21 CFR Part 11 -yhteensopivat
soluanalyysiratkaisut bioprosessoinnissa ja soluterapiassa
- Pitkävaikutteisten injektioiden lääkeaineen vapautumisen
hallinta: teknologiat jotka hyödyntävät atomitason
kerrostusta (ALD), nanohiukkasia tai muita menetelmiä
lääkeaineen hitaan ja kontrolloidun vapautumisen
mahdollistamiseksi (long-acting injectables,
depot-formulaatiot, biologisten lääkkeiden subkutaaninen
annostelu)
- Röntgenkuvantaminen ja ilmaisinteknologia: CT-ilmaisimet,
fotonilaskentailmaisimet, litteät paneeli-ilmaisimet sekä
röntgenkomponentit lääketieteellisiin (CT, mammografia,
hammasröntgen), turvallisuus- (tavarantarkastus,
tullisovellukset) ja teollisuussovelluksiin
(laadunvalvonta, materiaalilajittelu)
2. Jos EI liity → vastaa ainoastaan: SKIP
3. Jos LIITTYY → vastaa tässä muodossa:
YHTIÖ: [yhtiön nimi tai "Toimiala" jos yleinen alan uutinen]
TOIMIALA: [mikä yllä listatuista toimialoista on kyseessä]
OTSIKKO: [käännetty otsikko suomeksi]
TIIVISTELMÄ: [2-3 lausetta suomeksi, olennaisin tieto]
SÄVY: [Positiivinen / Negatiivinen / Neutraali]
LINKKI: [alkuperäinen URL]"""
# ─────────────────────────────────────────
# APUFUNKTIOT
# ─────────────────────────────────────────
def get_article_age_hours(entry):
    """Palauttaa artikkelin iän tunteina. Palauttaa 999 jos aikaa ei löydy."""
    for field in ["published", "updated"]:
        date_str = entry.get(field)
        if date_str:
            try:
                pub_time = parsedate_to_datetime(date_str)
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - pub_time
                return age.total_seconds() / 3600
            except Exception:
                pass
    return 999

def clean_html(text):
    """Poistaa HTML-tagit tekstistä."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def detect_language(feed_url):
    """Arvailee kielen syöte-URL:n perusteella."""
    if "hl=fi" in feed_url or "kauppalehti" in feed_url:
        return "fi"
    if "hl=sv" in feed_url or "di.se" in feed_url:
        return "sv"
    if "hl=da" in feed_url or "borsen.dk" in feed_url:
        return "da"
    return "en"


def analyze_with_claude(title, content, language, url):
    """Lähettää uutisen Claudelle analysoitavaksi. Palauttaa vastauksen tekstinä."""
    prompt = PROMPT_TEMPLATE.format(
        title=title,
        content=content[:2000],
        language=language,
        url=url,
    )
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


def parse_claude_response(text):
    """Poimii kentät Clauden vastauksesta. Palauttaa dict tai None jos SKIP."""
    if text.strip().upper().startswith("SKIP"):
        return None
    fields = {}
    for key in ["YHTIÖ", "TOIMIALA", "OTSIKKO", "TIIVISTELMÄ", "SÄVY", "LINKKI"]:
        match = re.search(rf"{key}:\s*(.+?)(?=\n[A-ZÄÖÅ]{{2,}}:|$)", text, re.DOTALL)
        if match:
            fields[key] = match.group(1).strip()
    if "OTSIKKO" not in fields or "TIIVISTELMÄ" not in fields:
        return None
    return fields


def build_rss_feed(articles):
    """Rakentaa RSS-syötteen Clauden tuottamista artikkeleista."""
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "Sijoitusuutiset – Claude-analyysi"
    SubElement(channel, "link").text = "https://github.com"
    SubElement(channel, "description").text = (
        "Automaattisesti suodatetut ja analysoidut sijoitusuutiset"
    )
    SubElement(channel, "language").text = "fi"
    SubElement(channel, "lastBuildDate").text = datetime.now(timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    for article in articles:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = article.get("OTSIKKO", "Otsikko puuttuu")
        SubElement(item, "link").text = article.get("LINKKI", "")
        description = (
            f"<b>Yhtiö:</b> {article.get('YHTIÖ', '')}<br/>"
            f"<b>Toimiala:</b> {article.get('TOIMIALA', '')}<br/>"
            f"<b>Sävy:</b> {article.get('SÄVY', '')}<br/><br/>"
            f"{article.get('TIIVISTELMÄ', '')}"
        )
        SubElement(item, "description").text = description
        SubElement(item, "guid").text = article.get("LINKKI", "")

    xml_str = minidom.parseString(tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    lines = xml_str.split("\n")
    return "\n".join(lines[1:]) if lines[0].startswith("<?xml") else xml_str

# ─────────────────────────────────────────
# PÄÄOHJELMA
# ─────────────────────────────────────────
def main():
print(f"Aloitetaan ajoa {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
seen_urls = set()
relevant_articles = []
for feed_url in RSS_FEEDS:
print(f"\nHaetaan: {feed_url}")
try:
feed = feedparser.parse(feed_url)
except Exception as e:
print(f" Virhe haettaessa syötettä: {e}")
continue
language = detect_language(feed_url)
for entry in feed.entries:
url = entry.get("link", "")
title = clean_html(entry.get("title", ""))
content = clean_html(entry.get("summary", entry.get("description", "")))
# Ohita jos ei otsikkoa tai URL:ia
if not title or not url:
continue
# Ohita duplikaatit
if url in seen_urls:
continue
seen_urls.add(url)
# Ohita yli 24h vanhat
age = get_article_age_hours(entry)
if age > 24:
continue
print(f" Analysoidaan: {title[:70]}...")
try:
response = analyze_with_claude(title, content, language, url)
except Exception as e:
print(f" Claude API -virhe: {e}")
time.sleep(5)
continue
parsed = parse_claude_response(response)
if parsed:
relevant_articles.append(parsed)
print(f" → Relevantti: {parsed.get('YHTIÖ')} | {parsed.get('SÄVY')}")
else:
print(" → SKIP")
# Pieni viive API-kutsujen välillä
time.sleep(1)
print(f"\nLöydettiin {len(relevant_articles)} relevanttia artikkelia.")
# Rakennetaan ja tallennetaan RSS-tiedosto
feed_xml = build_rss_feed(relevant_articles)
output_path = os.path.join(os.path.dirname(__file__), "feed.xml")
with open(output_path, "w", encoding="utf-8") as f:
f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
f.write(feed_xml)
print(f"RSS-tiedosto tallennettu: {output_path}")
if __name__ == "__main__":
main()