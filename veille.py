#!/usr/bin/env python3
"""Veille des stages de récupération de points (Eure).

Lit plusieurs pages publiques, compare avec l'état précédent, journalise les
événements (création, suppression, prix, statut, places) dans le dépôt de
données, et envoie des notifications ntfy.

Variables d'environnement :
  NTFY_TOPIC   nom du canal ntfy (secret)
  DATA_DIR     dossier du dépôt de données (défaut : data)
  NOTIFY_OWN   "1" pour être aussi notifié des changements sur vos propres stages
"""
import csv
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Paris")
DATA = os.environ.get("DATA_DIR", "data")
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NOTIFY_OWN = os.environ.get("NOTIFY_OWN", "0") == "1"
OWN_AGREMENT = "R2102700010"  # Axéfor
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

AGREMENTS = {
    "R2102700010": "Axéfor",
    "R1502700050": "Points De Vue",
    "R1502700010": "Point Permis Prevention",
    "R1302700120": "Automobile Club Association",
    "R1702700040": "Blanchard",
    "R1902700010": "Anper",
    "R1302700060": "ActiROUTE",
}

PDV = "https://www.stage-pointsdevue.fr/index.php?option=com_eventbooking&view=search&layout=table&filter_city={}&Itemid=181"

SOURCES = [
    {"id": "srp-eure", "label": "stage-recuperation-points.com",
     "url": "https://www.stage-recuperation-points.com/stage-eure-27/", "parser": "srp"},
    {"id": "pdv-louviers", "label": "Points De Vue (Louviers)",
     "url": PDV.format("Louviers"), "parser": "pdv", "agrement": "R1502700050"},
    {"id": "pdv-vernon", "label": "Points De Vue (Vernon)",
     "url": PDV.format("VERNON"), "parser": "pdv", "agrement": "R1502700050"},
    {"id": "anper", "label": "Anper",
     "url": "https://www.permis-a-points-anper.com/stages-eure-27/", "parser": "anper",
     "agrement": "R1902700010"},
    {"id": "actiroute", "label": "Actiroute",
     "url": "https://www.actiroute.com/stage-permis-a-points-eure-27/", "parser": "actiroute",
     "agrement": "R1302700060", "browser": True},
]
# "http" : sites simples ; "browser" : sites qui chargent leur liste en JavaScript (Actiroute)
MODE = os.environ.get("MODE", "http")

CITIES = ["Évreux", "Louviers", "Vernon", "Bernay", "Pont-Audemer", "Les Andelys",
          "Le Neubourg", "Verneuil d'Avre et d'Iton", "Vironvay", "Gisors", "Gaillon",
          "Val-de-Reuil", "Brionne", "Beaumont-le-Roger", "Saint-André-de-l'Eure"]

# ----------------------------------------------------------------- utilitaires


def norm(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


MONTHS = {"janv": 1, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7,
          "aout": 8, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
MON_RE = r"(janv|fevr|mars|avr|mai|juin|juil|aout|sept|oct|nov|dec)[a-z]*\.?"
WD_RE = r"(?:lun|mar|mer|jeu|ven|sam|dim)[a-z]*\.?"
MONTH_LABELS = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
                "sept.", "oct.", "nov.", "déc."]
PRICE = re.compile(r"(\d{2,3})\s?€")


def parse_dates(text, today=None):
    """Retourne (début, fin) ou (None, None). Gère '26 et 27 nov.', '30 Nov - 1 Déc'."""
    today = today or datetime.now(TZ).date()
    t = norm(text)
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]

    def build(d1, m1, d2, m2):
        try:
            y1 = years[0] if years else today.year
            if not years and date(y1, m1, d1) < today - timedelta(days=90):
                y1 += 1
            return date(y1, m1, d1), date(y1 + (1 if m2 < m1 else 0), m2, d2)
        except ValueError:
            return None, None

    r2 = re.search(r"\b(\d{1,2})\s+" + MON_RE + r"\s*[-\u2013]\s*(\d{1,2})\s+" + MON_RE, t)
    if r2:
        return build(int(r2.group(1)), MONTHS[r2.group(2)], int(r2.group(3)), MONTHS[r2.group(4)])
    r1 = re.search(r"\b(\d{1,2})\s*[-\u2013]\s*(\d{1,2})\s+" + MON_RE, t)
    if r1:
        mo = MONTHS[r1.group(3)]
        return build(int(r1.group(1)), mo, int(r1.group(2)), mo)
    days = [(m.start(), int(m.group(1))) for m in re.finditer(WD_RE + r"\s+(\d{1,2})\b", t)]
    if not days:
        days = [(m.start(), int(m.group(1)))
                for m in re.finditer(r"\b(\d{1,2})\s+(?=" + MON_RE + ")", t)]
    if not days:
        return None, None
    months = [(m.start(), MONTHS[m.group(1)]) for m in re.finditer(MON_RE, t)]
    m1 = next((mo for pos, mo in months if pos > days[0][0]), None)
    if m1 is None:
        return None, None
    d1 = days[0][1]
    if len(days) >= 2:
        d2 = days[1][1]
        m2 = next((mo for pos, mo in months if pos > days[1][0]), m1)
    else:
        d2, m2 = d1, m1
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
    try:
        if years:
            y1 = years[0]
        else:
            y1 = today.year
            if date(y1, m1, d1) < today - timedelta(days=90):
                y1 += 1
        start = date(y1, m1, d1)
        end = date(y1 + (1 if m2 < m1 else 0), m2, d2)
    except ValueError:
        return None, None
    return start, end


def fmt_range(start, end):
    if not start:
        return "?"
    s, e = date.fromisoformat(start), date.fromisoformat(end or start)
    if s == e:
        return f"{s.day} {MONTH_LABELS[s.month]}"
    if s.month == e.month:
        return f"{s.day}-{e.day} {MONTH_LABELS[s.month]}"
    return f"{s.day} {MONTH_LABELS[s.month]}-{e.day} {MONTH_LABELS[e.month]}"


def find_city(text):
    n = norm(text)
    for c in CITIES:
        if norm(c) in n:
            return c
    return "?"


def fetch(url):
    last = None
    for attempt in range(2):
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"},
                             timeout=25)
        except requests.RequestException as e:
            last = e
            time.sleep(3)
            continue
        if r.status_code in (401, 403, 429, 503):
            raise RuntimeError(f"accès refusé (HTTP {r.status_code})")
        r.raise_for_status()
        if len(r.content) < 30000 and b"captcha" in r.content[:8000].lower():
            raise RuntimeError("page de vérification anti-robot")
        return r.content
    raise RuntimeError(f"réseau : {last}")


def fetch_browser(url, more_text="Voir plus de stages", card_sel='a[href*="/stage-recuperation-de-points-"]'):
    """Ouvre la page dans un vrai navigateur et clique sur « Voir plus » jusqu'à tout afficher."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=UA, locale="fr-FR")
            page.goto(url, wait_until="networkidle", timeout=45000)
            last = -1
            for _ in range(15):
                n = len(set(page.eval_on_selector_all(
                    card_sel, "els => els.map(e => e.getAttribute('href'))")))
                if n == last:
                    break
                last = n
                btns = page.get_by_text(more_text, exact=False)
                clicked = False
                for i in range(btns.count()):
                    if btns.nth(i).is_visible():
                        try:
                            btns.nth(i).click(timeout=5000)
                            clicked = True
                        except Exception:  # noqa: BLE001
                            pass
                        break
                if not clicked:
                    break
                page.wait_for_timeout(2500)
            content = page.content()
        finally:
            browser.close()
    if len(content) < 30000 and "captcha" in content.lower()[:8000]:
        raise RuntimeError("page de vérification anti-robot")
    return content.encode("utf-8")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    txt = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == txt:
                return
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)


# --------------------------------------------------------------------- parseurs


def make_rec(src, key, city, start, end, price, status, places, agrement, number, serial, url,
             agr):
    org = agr.get(agrement) if agrement else None
    if not org:
        org = f"Inconnu ({agrement})" if agrement else "Inconnu"
    return {
        "key": key, "source": src["id"], "city": city,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "price": price, "status": status, "places": places,
        "agrement": agrement, "organiser": org, "number": number, "serial": serial,
        "url": url, "own": agrement == OWN_AGREMENT,
    }


def parse_srp(content, src, agr):
    soup = BeautifulSoup(content, "html.parser")
    pat = re.compile(r"stageid=(\d+)|stage-point-(\d+)-")

    def ids_of(node):
        found = set()
        for a in node.find_all("a", href=True):
            for m in pat.finditer(a["href"]):
                found.add(m.group(1) or m.group(2))
        return found

    out = {}
    for a in soup.find_all("a", href=pat):
        m = pat.search(a["href"])
        sid = m.group(1) or m.group(2)
        if sid in out:
            continue
        best, node = None, a
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
            node = node.parent
            if ids_of(node) - {sid}:
                break
            txt = clean(node.get_text(" "))
            if PRICE.search(txt) and parse_dates(txt)[0]:
                best = node
        if best is None:
            continue
        txt = clean(best.get_text(" "))
        start, end = parse_dates(txt)
        price = int(PRICE.search(txt).group(1))
        num = re.search(r"\b(\d{2})R(\d{14,16})\b", txt)
        agrement = serial = number = None
        if num:
            number = num.group(0)
            agrement = "R" + num.group(2)[:10]
            serial = int(num.group(2)[10:] or 0)
        detail = next((x["href"] for x in best.find_all("a", href=True)
                       if re.search(r"stage-point-\d+-", x["href"])), None)
        city = None
        if detail:
            slug = re.search(r"stage-point-\d+-([a-z0-9\-]+)", detail)
            if slug:
                city = next((c for c in CITIES
                             if norm(c).replace(" ", "-").replace("'", "-") == slug.group(1)), None)
        city = city or find_city(txt)
        url = detail or a["href"]
        if url.startswith("/"):
            url = "https://www.stage-recuperation-points.com" + url
        n = norm(txt)
        if "complet" in n and "incomplet" not in n:
            status = "complet"
        elif "dernieres places" in n or "derniere place" in n:
            status = "dernieres"
        elif "places disponibles" in n or "place disponible" in n:
            status = "dispo"
        else:
            status = "?"
        out["srp:" + sid] = make_rec(src, "srp:" + sid, city, start, end, price, status, None,
                                     agrement, number, serial, url, agr)
    return list(out.values())


def parse_pdv(content, src, agr):
    soup = BeautifulSoup(content, "html.parser")
    out = {}
    for tr in soup.find_all("tr"):
        txt = clean(tr.get_text(" "))
        if "€" not in txt:
            continue
        start, end = parse_dates(txt)
        if not start:
            continue
        hrefs = [a["href"] for a in tr.find_all("a", href=True)]
        eid = None
        for h in hrefs:
            m = re.search(r"(?:event_id|[?&]id)=(\d+)", h) or re.search(r"/(\d{3,6})(?:[-/.]|$)", h)
            if m:
                eid = m.group(1)
                break
        key = "pdv:" + (eid or f"{find_city(txt)}|{start.isoformat()}")
        cells = [clean(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        places = None
        for c in cells:
            if re.fullmatch(r"\d{1,3}", c):
                places = int(c)
        n = norm(txt)
        closed = "clos" in n
        if closed or places == 0:
            status = "clos" if closed else "complet"
        else:
            status = "ouvert"
        price = int(PRICE.search(txt).group(1))
        out[key] = make_rec(src, key, find_city(txt), start, end, price, status, places,
                            src["agrement"], None, None, src["url"], agr)
    return list(out.values())


def generic_cards(soup):
    cands = []
    for node in soup.find_all(["tr", "li", "article", "div", "section", "a"]):
        txt = clean(node.get_text(" "))
        if len(txt) > 700 or not PRICE.search(txt) or not parse_dates(txt)[0]:
            continue
        cands.append((node, txt))
    ids = {id(n) for n, _ in cands}
    return [(n, t) for n, t in cands
            if not any(id(d) in ids for d in n.find_all(True) if d is not n)]


def parse_anper(content, src, agr):
    soup = BeautifulSoup(content, "html.parser")
    out = {}
    for node, txt in generic_cards(soup):
        start, end = parse_dates(txt)
        city = find_city(txt)
        blob = " ".join([x.get("href", "") for x in node.find_all("a")] +
                        [str(v) for v in node.attrs.values() if isinstance(v, str)])
        m = re.search(r"(?:id|stage|stageid|session)[=/_-]?(\d{3,6})", blob)
        key = "anper:" + (m.group(1) if m else f"{city}|{start.isoformat()}")
        price = int(PRICE.search(txt).group(1))
        out[key] = make_rec(src, key, city, start, end, price, "?", None, src["agrement"],
                            None, None, src["url"], agr)
    return list(out.values())


def parse_actiroute(content, src, agr):
    soup = BeautifulSoup(content, "html.parser")
    pat = re.compile(r"/stage-recuperation-de-points-([0-9a-z]+)/?")

    def ids_of(node):
        return {pat.search(a["href"]).group(1) for a in node.find_all("a", href=pat)}

    out = {}
    for a in soup.find_all("a", href=pat):
        sid = pat.search(a["href"]).group(1)
        if sid in out:
            continue
        best, node = None, a
        while node.parent is not None and node.parent.name not in ("body", "html", "[document]"):
            node = node.parent
            if ids_of(node) - {sid}:
                break
            txt = clean(node.get_text(" "))
            if PRICE.search(txt) and parse_dates(txt)[0]:
                best = node
        if best is None:
            continue
        txt = clean(best.get_text(" "))
        start, end = parse_dates(txt)
        price = int(PRICE.search(txt).group(1))
        ag = re.search(r"\bR\s?(\d{2})\s?(\d{3})\s?(\d{4})\s?(\d)\b", txt)
        agrement = "R" + "".join(ag.groups()) if ag else src["agrement"]
        url = a["href"]
        if url.startswith("/"):
            url = "https://www.actiroute.com" + url
        key = "actiroute:" + sid
        out[key] = make_rec(src, key, find_city(txt), start, end, price, "?", None, agrement,
                            None, None, url, agr)
    announced = re.search(r"(\d+)\s+stages?\s+de\s+recuperation", norm(soup.get_text(" ")))
    if announced and len(out) < int(announced.group(1)):
        raise RuntimeError(f"lecture incomplète : {len(out)} stages sur {announced.group(1)} annoncés")
    return list(out.values())


PARSERS = {"srp": parse_srp, "pdv": parse_pdv, "anper": parse_anper, "actiroute": parse_actiroute}


def learn_organiser(agr, code, url):
    """Lit la page de détail pour apprendre le nom d'un organisateur inconnu."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        text = clean(BeautifulSoup(r.content, "html.parser").get_text(" "))
        m = re.search(r"d[ée]livr[ée]e? [àa]\s+(.{2,80}?)\s+par\s+le\s+Pr", text)
        if m:
            agr[code] = m.group(1).strip()
            return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------ événements et ntfy

EVENT_COLS = ["ts", "source", "key", "type", "organiser", "city", "start", "end", "price_old",
              "price_new", "status_old", "status_new", "places_old", "places_new",
              "days_before", "own"]


def event(now, etype, rec, **kw):
    e = {c: "" for c in EVENT_COLS}
    e.update({"ts": now.isoformat(timespec="seconds"), "source": rec["source"],
              "key": rec["key"], "type": etype, "organiser": rec["organiser"],
              "city": rec["city"], "start": rec["start"] or "", "end": rec.get("end") or "",
              "own": int(rec["own"])})
    e.update(kw)
    return e


def diff(now, prev, cur):
    today = now.date()
    ev = []
    for k, c in cur.items():
        p = prev.get(k)
        if p is None:
            c["first_seen"] = now.isoformat(timespec="seconds")
            ev.append(event(now, "NOUVEAU", c, price_new=c["price"], status_new=c["status"],
                            places_new="" if c["places"] is None else c["places"],
                            days_before=(date.fromisoformat(c["start"]) - today).days
                            if c["start"] else ""))
            continue
        c["first_seen"] = p.get("first_seen", "")
        if p["price"] != c["price"]:
            ev.append(event(now, "PRIX", c, price_old=p["price"], price_new=c["price"]))
        if p["status"] != c["status"]:
            ev.append(event(now, "STATUT", c, status_old=p["status"], status_new=c["status"]))
        if p.get("places") != c.get("places"):
            ev.append(event(now, "PLACES", c, places_old="" if p.get("places") is None else p["places"],
                            places_new="" if c["places"] is None else c["places"]))
    for k, p in prev.items():
        if k in cur:
            continue
        # un stage qui disparaît le jour de son début, ou après, n'est pas une annulation
        began = date.fromisoformat(p["start"]) if p.get("start") else None
        etype = "TERMINE" if began and began <= today else "SUPPRIME"
        days = (date.fromisoformat(p["start"]) - today).days if p.get("start") else ""
        ev.append(event(now, etype, p, price_old=p["price"], status_old=p["status"],
                        days_before=days))
    return ev


def should_notify(e):
    if e["own"] and not NOTIFY_OWN:
        return False
    if e["type"] in ("NOUVEAU", "SUPPRIME", "PRIX", "STATUT"):
        return True
    return e["type"] == "PLACES" and str(e["places_new"]) == "0"


SITE = {"srp-eure": "stage-recuperation-points.com", "pdv-louviers": "stage-pointsdevue.fr",
        "pdv-vernon": "stage-pointsdevue.fr", "anper": "permis-a-points-anper.com",
        "actiroute": "actiroute.com"}


def message(e):
    who = f"{e['organiser']} {e['city']} (sur {SITE.get(e['source'], e['source'])})"
    d = fmt_range(e["start"] or None, e["end"] or None)
    t = e["type"]
    if t == "NOUVEAU":
        return f"Nouveau stage : {who}, {d}, {e['price_new']} €", 3
    if t == "PRIX":
        down = e["price_new"] < e["price_old"]
        return f"Prix {'en baisse' if down else 'en hausse'} : {who}, {d} : {e['price_old']} → {e['price_new']} €", 4
    if t == "STATUT":
        return f"Statut : {who}, {d} : {e['status_old']} → {e['status_new']}", 3
    if t == "SUPPRIME":
        return (f"Stage supprimé : {who}, {d}, à J-{e['days_before']} "
                f"(dernier statut : {e['status_old']})"), 4
    if t == "PLACES":
        return f"Plus de places : {who}, {d}", 3
    return f"{t} : {who}", 3


def notify(title, body, priority=3):
    if not TOPIC:
        print("NTFY_TOPIC absent : notification non envoyée")
        return
    try:
        r = requests.post("https://ntfy.sh/", json={"topic": TOPIC, "title": title,
                                                    "message": body, "priority": priority},
                          timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"échec ntfy : {type(e).__name__}")


def append_events(events):
    if not events:
        return
    path = os.path.join(DATA, "events.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_COLS)
        if new:
            w.writeheader()
        w.writerows(events)


# ------------------------------------------------------------------------ main


def main():
    now = datetime.now(TZ)
    os.makedirs(DATA, exist_ok=True)
    state = load_json(os.path.join(DATA, "state.json"), {"_meta": {}, "sources": {}})
    learned = load_json(os.path.join(DATA, "agrements.json"), {})
    agr = {**AGREMENTS, **learned}
    all_events, notifs, baselines = [], [], []
    stats = load_json(os.path.join(DATA, "last_run.json"), {})
    learn_budget = 3

    for src in SOURCES:
        if bool(src.get("browser")) != (MODE == "browser"):
            continue
        s = state["sources"].setdefault(src["id"], {"stages": {}, "fail": 0, "alerted": False,
                                                    "baseline": False})
        prev = s["stages"]
        content, err = None, None
        try:
            content = fetch_browser(src["url"]) if src.get("browser") else fetch(src["url"])
            recs = PARSERS[src["parser"]](content, src, agr)
            if src["parser"] == "srp":
                for r in recs:
                    if r["agrement"] and r["agrement"] not in agr and learn_budget > 0:
                        learn_budget -= 1
                        if learn_organiser(agr, r["agrement"], r["url"]):
                            learned[r["agrement"]] = agr[r["agrement"]]
                            notifs.append(("Nouvel organisateur identifié",
                                           f"{agr[r['agrement']]} ({r['agrement']})", 3))
                for r in recs:
                    r["organiser"] = agr.get(r["agrement"], r["organiser"])
            cur = {r["key"]: r for r in recs}
            if not cur:
                raise RuntimeError("aucun stage lu (structure de la page changée ?)")
            if len(prev) >= 4 and len(cur) < 0.5 * len(prev):
                raise RuntimeError(f"lecture suspecte : {len(cur)} stages au lieu de {len(prev)}")
        except Exception as e:  # noqa: BLE001
            err = str(e)

        n_ok = len(prev) if err else len(cur)
        stats[src["id"]] = {"ok": err is None, "error": err, "count": n_ok}
        if err is None:
            sample = next(iter(cur.values()))
            stats[src["id"]]["parse_quality"] = {
                "n": len(cur), "price": sum(1 for r in cur.values() if r["price"]),
                "start": sum(1 for r in cur.values() if r["start"]),
                "city_known": sum(1 for r in cur.values() if r["city"] != "?"),
                "agrement": sum(1 for r in cur.values() if r["agrement"]),
                "status_known": sum(1 for r in cur.values() if r["status"] != "?"),
                "own": sum(1 for r in cur.values() if r["own"]),
            }
            stats[src["id"]]["sample"] = sample

        if err:
            s["fail"] = min(s["fail"] + 1, 3)
            print(f"[{src['id']}] ERREUR : {err}")
            snap = os.path.join(DATA, "snapshots", f"{src['id']}.html")
            if content is not None:
                os.makedirs(os.path.dirname(snap), exist_ok=True)
                with open(snap, "wb") as f:
                    f.write(content)
            if s["fail"] >= 3 and not s["alerted"]:
                notifs.append((f"Veille : {src['label']} illisible",
                               f"{err}. Dernier état connu conservé.", 4))
                s["alerted"] = True
            continue

        if s["alerted"]:
            notifs.append((f"Veille : {src['label']} de nouveau lisible", "", 3))
        s["fail"], s["alerted"] = 0, False

        if not s["baseline"]:
            for r in cur.values():
                r["first_seen"] = now.isoformat(timespec="seconds")
            s["stages"], s["baseline"] = cur, True
            all_events += [event(now, "BASELINE", r, price_new=r["price"], status_new=r["status"])
                           for r in cur.values()]
            baselines.append(f"{src['label']} : {len(cur)} stages")
            snap = os.path.join(DATA, "snapshots", f"{src['id']}.html")
            os.makedirs(os.path.dirname(snap), exist_ok=True)
            with open(snap, "wb") as f:
                f.write(content)
            print(f"[{src['id']}] état initial : {len(cur)} stages")
            continue

        ev = diff(now, prev, cur)
        s["stages"] = cur
        all_events += ev
        for e in ev:
            if should_notify(e):
                body, prio = message(e)
                notifs.append(("Veille stages", body, prio))
        print(f"[{src['id']}] {len(cur)} stages, {len(ev)} événement(s)")

    if baselines:
        notifs.append(("Veille initialisée", "\n".join(baselines), 3))

    # pas d'inondation de notifications : au-delà de 6, un seul récapitulatif
    if len(notifs) > 6:
        body = "\n".join(b for _, b, _ in notifs[:12])
        notifs = [("Veille stages : %d changements" % len(notifs), body, 4)]
    for title, body, prio in notifs:
        notify(title, body, prio)

    today = now.date().isoformat()
    if state["_meta"].get("heartbeat") != today:
        state["_meta"]["heartbeat"] = today
    append_events([e for e in all_events])
    save_json(os.path.join(DATA, "state.json"), state)
    save_json(os.path.join(DATA, "last_run.json"), stats)
    if learned:
        save_json(os.path.join(DATA, "agrements.json"), learned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
