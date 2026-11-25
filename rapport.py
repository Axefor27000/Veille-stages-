#!/usr/bin/env python3
"""Génère le rapport HTML (compatible e-mail) à partir de l'historique.

Usage : python rapport.py [--data DOSSIER] [--out FICHIER] [--weeks 8] [--demo] [--test]
  --demo  ajoute 3 événements FICTIFS, clairement signalés, pour montrer la mise en forme
  --test  ajoute un bandeau « test » en haut du rapport
"""
import argparse
import csv
import html
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
MOIS_C = ["", "janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
          "sept.", "oct.", "nov.", "déc."]

# une couleur par organisme ; Axéfor en orange
COULEURS = {
    "Axéfor": "#F28C28",
    "Points De Vue": "#2F6FDE",
    "Point Permis Prevention": "#2E9E5B",
    "Automobile Club Association": "#8E4EC6",
    "Blanchard": "#0E9AA7",
    "Anper": "#D6336C",
    "ActiROUTE": "#8D6E63",
}
GRIS = "#6B7280"
# couleur sous forme d'émoji : survit à l'e-mail (les styles CSS y sont interdits)
EMOJIS = {
    "Axéfor": "🟧",
    "Points De Vue": "🟦",
    "Point Permis Prevention": "🟩",
    "Automobile Club Association": "🟪",
    "Blanchard": "🟨",
    "Anper": "🟥",
    "ActiROUTE": "🟫",
}
SOURCES = {"srp-eure": "stage-recuperation-points.com", "pdv-louviers": "site Points De Vue",
           "pdv-vernon": "site Points De Vue", "anper": "site Anper",
           "actiroute": "site Actiroute"}
STATUTS = {"dispo": "places disponibles", "dernieres": "dernières places", "complet": "complet",
           "clos": "inscriptions closes", "ouvert": "inscriptions ouvertes", "?": ""}


def esc(s):
    return html.escape(str(s), quote=False)


def jour(d):
    n = "1er" if d.day == 1 else str(d.day)
    return f"{JOURS[d.weekday()]} {n}"


def label_jours(start, end):
    if start == end:
        return jour(start).capitalize()
    a, b = jour(start), jour(end)
    if start.month != end.month:
        a += f" {MOIS_C[start.month]}"
        b += f" {MOIS_C[end.month]}"
    return f"{a} et {b}".capitalize()


def label_semaine(lundi):
    dim = lundi + timedelta(days=6)
    if lundi.month == dim.month:
        return f"Semaine du {lundi.day} au {dim.day} {MOIS[dim.month]}"
    return f"Semaine du {lundi.day} {MOIS[lundi.month]} au {dim.day} {MOIS[dim.month]}"


def couleur(org):
    return COULEURS.get(org, GRIS)


def charger(data):
    with open(os.path.join(data, "state.json"), encoding="utf-8") as f:
        state = json.load(f)
    merged = {}
    for sid, s in state["sources"].items():
        for r in s["stages"].values():
            if not r.get("start"):
                continue
            k = (r.get("agrement") or r["organiser"], r["city"], r["start"])
            m = merged.setdefault(k, {"organiser": r["organiser"], "city": r["city"],
                                      "start": date.fromisoformat(r["start"]),
                                      "end": date.fromisoformat(r["end"] or r["start"]),
                                      "own": r["own"], "prix": {}, "statut": "?",
                                      "places": None})
            m["prix"][SOURCES.get(sid, sid)] = r["price"]
            if sid == "srp-eure" and r["status"] != "?":
                m["statut"] = r["status"]
            if r.get("places") is not None:
                m["places"] = r["places"]
                if m["statut"] == "?":
                    m["statut"] = r["status"]
    return list(merged.values())


def evenements(data, depuis):
    path = os.path.join(data, "events.csv")
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["type"] in ("BASELINE", "TERMINE", "PLACES"):
                continue
            if datetime.fromisoformat(row["ts"]) >= depuis:
                out.append(row)
    return out


def phrase_evenement(e):
    d = ""
    if e["start"]:
        s = date.fromisoformat(e["start"])
        d = f"{s.day} {MOIS_C[s.month]}"
    qui = f"{e['organiser']}, {e['city']}, {d}"
    t = e["type"]
    if t == "NOUVEAU":
        return "Nouveau stage", f"{qui} : {e['price_new']} €"
    if t == "SUPPRIME":
        j = f" à J-{e['days_before']}" if e["days_before"] != "" else ""
        return "Stage supprimé", f"{qui}{j} (dernier statut : {STATUTS.get(e['status_old'], e['status_old'])})"
    if t == "PRIX":
        sens = "baisse" if float(e["price_new"]) < float(e["price_old"]) else "hausse"
        return f"Prix en {sens}", f"{qui} : {e['price_old']} → {e['price_new']} €"
    if t == "STATUT":
        return "Statut", (f"{qui} : {STATUTS.get(e['status_old'], e['status_old'])} → "
                          f"{STATUTS.get(e['status_new'], e['status_new'])}")
    return t, qui


def puce(org, texte, gras=True):
    c = couleur(org)
    return (f'<span style="display:inline-block;background:{c};color:#fff;border-radius:10px;'
            f'padding:1px 8px;font-size:12px;font-weight:{"bold" if gras else "normal"}">{esc(texte)}</span>')


def ligne_stage(m):
    c = couleur(m["organiser"])
    prix = m["prix"]
    p_min = min(prix.values())
    detail = ""
    if len(set(prix.values())) > 1:
        detail = " · ".join(f"{v} € ({esc(k)})" for k, v in sorted(prix.items(), key=lambda x: x[1]))
        prix_txt = f"<b>{p_min} €</b>"
    else:
        prix_txt = f"<b>{p_min} €</b>"
    statut = STATUTS.get(m["statut"], "")
    if m["places"] is not None and m["statut"] in ("ouvert",):
        statut = f"{m['places']} places restantes"
    extra = " · ".join(x for x in [statut, detail] if x)
    return (f'<tr><td style="border-left:6px solid {c};padding:6px 10px;background:#fafafa">'
            f'<span style="color:{c};font-weight:bold">{esc(m["organiser"])}</span> '
            f'· {esc(m["city"])} · {prix_txt}'
            + (f'<br><span style="color:#555;font-size:12px">{extra}</span>' if extra else "")
            + "</td></tr>"
            '<tr><td style="height:4px;line-height:4px;font-size:4px">&nbsp;</td></tr>')


def construire(data, nb_semaines, demo, test, now):
    today = now.date()
    stages = [m for m in charger(data) if m["end"] >= today]
    lundi0 = today - timedelta(days=today.weekday())
    semaines = [lundi0 + timedelta(weeks=i) for i in range(nb_semaines)]
    fin = semaines[-1] + timedelta(days=6)
    stages = [m for m in stages if m["start"] <= fin]

    h = []
    h.append('<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:640px;'
             'margin:0 auto;color:#111">')
    if test:
        h.append('<div style="background:#FFF4CC;border:1px solid #E6C200;padding:8px 12px;'
                 'border-radius:6px;margin-bottom:12px"><b>TEST</b> : première version du rapport, '
                 'pour valider la mise en forme.</div>')
    h.append(f'<h2 style="margin:0 0 4px">Veille stages Eure</h2>'
             f'<div style="color:#555;margin-bottom:12px">Point du {jour(today)} {MOIS[today.month]} '
             f'{today.year}, {nb_semaines} semaines à venir</div>')

    # légende + totaux
    cnt = defaultdict(int)
    for m in stages:
        cnt[m["organiser"]] += 1
    ordre = sorted(cnt, key=lambda o: (o != "Axéfor", -cnt[o], o))
    h.append('<div style="margin-bottom:14px"><b>En bref</b> : %d stages sur la période, dont %d chez '
             'Axéfor.<br>' % (len(stages), cnt.get("Axéfor", 0)))
    h.append(" ".join(puce(o, f"{o} {cnt[o]}") for o in ordre))
    h.append("</div>")

    # événements
    evs = evenements(data, now - timedelta(hours=24))
    demo_rows = []
    if demo:
        demo_rows = [
            ("Prix en baisse", "Points De Vue, Louviers, 7 déc. : 235 → 199 €", "Points De Vue",
             "26/11 07h10"),
            ("Nouveau stage", "Point Permis Prevention, Bernay, 25 janv. : 262 €",
             "Point Permis Prevention", "26/11 11h42"),
            ("Stage supprimé", "Automobile Club Association, Évreux, 15 déc. à J-19 "
             "(dernier statut : places disponibles)", "Automobile Club Association", "26/11 15h05"),
        ]
    h.append('<h3 style="margin:16px 0 6px">Événements des dernières 24 h</h3>')
    if not evs and not demo_rows:
        h.append('<div style="color:#555">Aucun changement détecté.</div>')
    else:
        if demo_rows:
            h.append('<div style="color:#B45309;font-size:12px;margin-bottom:4px">Exemples fictifs, '
                     'uniquement pour montrer la mise en forme.</div>')
        h.append('<table style="border-collapse:collapse;width:100%">')
        for e in evs:
            t, txt = phrase_evenement(e)
            ts = datetime.fromisoformat(e["ts"]).astimezone(TZ).strftime("%d/%m %Hh%M")
            c = couleur(e["organiser"])
            h.append(f'<tr><td style="border-left:6px solid {c};padding:5px 10px;background:#fafafa">'
                     f'<b>{esc(t)}</b> · {esc(txt)}<br><span style="color:#777;font-size:12px">'
                     f'détecté le {ts}</span></td></tr><tr><td style="height:4px"></td></tr>')
        for t, txt, org, ts in demo_rows:
            c = couleur(org)
            h.append(f'<tr><td style="border-left:6px solid {c};padding:5px 10px;background:#fafafa">'
                     f'<b>{esc(t)}</b> · {esc(txt)}<br><span style="color:#777;font-size:12px">'
                     f'détecté le {ts} (exemple)</span></td></tr><tr><td style="height:4px"></td></tr>')
        h.append("</table>")

    # semaines
    h.append('<h3 style="margin:18px 0 6px">Semaine par semaine</h3>')
    for lundi in semaines:
        dim = lundi + timedelta(days=6)
        sem = [m for m in stages if lundi <= m["start"] <= dim]
        n_ax = sum(1 for m in sem if m["own"])
        h.append(f'<div style="margin:14px 0 4px;padding:6px 10px;background:#111;color:#fff;'
                 f'border-radius:6px"><b>{esc(label_semaine(lundi))}</b> : {len(sem)} stage'
                 f'{"s" if len(sem) != 1 else ""}'
                 f' <span style="color:#bbb">({n_ax} Axéfor, {len(sem) - n_ax} concurrent{'s' if len(sem) - n_ax > 1 else ''})</span></div>')
        if not sem:
            h.append('<div style="color:#777;padding:4px 10px">Aucun stage.</div>')
            continue
        groupes = defaultdict(list)
        for m in sem:
            groupes[(m["start"], m["end"])].append(m)
        for (s, e) in sorted(groupes):
            g = sorted(groupes[(s, e)], key=lambda m: (not m["own"], m["organiser"], m["city"]))
            h.append(f'<div style="margin:8px 0 3px;font-weight:bold">{esc(label_jours(s, e))} = '
                     f'{len(g)} stage{"s" if len(g) != 1 else ""}</div>')
            h.append('<table style="border-collapse:collapse;width:100%">')
            h += [ligne_stage(m) for m in g]
            h.append("</table>")

    # écarts de prix entre sites
    ecarts = [m for m in stages if len(set(m["prix"].values())) > 1]
    if ecarts:
        h.append('<h3 style="margin:18px 0 6px">Écarts de prix entre sites</h3>'
                 '<div style="color:#555;font-size:12px;margin-bottom:4px">Un même stage affiché '
                 'à des prix différents selon le site.</div>')
        h.append('<table style="border-collapse:collapse;width:100%;font-size:14px">')
        for m in sorted(ecarts, key=lambda m: m["start"]):
            c = couleur(m["organiser"])
            det = " · ".join(f"{v} € ({esc(k)})" for k, v in sorted(m["prix"].items(), key=lambda x: x[1]))
            h.append(f'<tr><td style="border-left:6px solid {c};padding:5px 10px;background:#fafafa">'
                     f'<b>{esc(m["organiser"])}</b> · {esc(m["city"])} · {m["start"].day} '
                     f'{MOIS_C[m["start"].month]}<br><span style="font-size:12px;color:#555">{det}'
                     f'</span></td></tr><tr><td style="height:4px"></td></tr>')
        h.append("</table>")

    h.append('<div style="margin-top:18px;color:#777;font-size:12px">Sources : '
             'stage-recuperation-points.com, sites de Points De Vue, d\'Anper et d\'Actiroute. '
             'Mobilité Club n\'est pas encore suivi.</div></div>')
    return "\n".join(h)


def emo(org):
    return EMOJIS.get(org, "⬜")


def construire_email(data, nb_semaines, demo, test, now):
    """Même contenu, mais uniquement avec les balises acceptées par l'envoi d'e-mail."""
    today = now.date()
    stages = [m for m in charger(data) if m["end"] >= today]
    lundi0 = today - timedelta(days=today.weekday())
    semaines = [lundi0 + timedelta(weeks=i) for i in range(nb_semaines)]
    fin = semaines[-1] + timedelta(days=6)
    stages = [m for m in stages if m["start"] <= fin]
    h = []
    if test:
        h.append("<p><b>TEST</b> : première version du rapport, pour valider la mise en forme.</p>")
    h.append(f"<h2>Veille stages Eure</h2><p>Point du {jour(today)} {MOIS[today.month]} {today.year}, "
             f"{nb_semaines} semaines à venir</p>")
    cnt = defaultdict(int)
    for m in stages:
        cnt[m["organiser"]] += 1
    ordre = sorted(cnt, key=lambda o: (o != "Axéfor", -cnt[o], o))
    h.append(f"<p><b>En bref</b> : {len(stages)} stages sur la période, dont {cnt.get('Axéfor', 0)} chez Axéfor.<br>"
             + "<br>".join(f"{emo(o)} {esc(o)} : {cnt[o]}" for o in ordre) + "</p>")

    evs = evenements(data, now - timedelta(hours=24))
    h.append("<h3>Événements des dernières 24 h</h3>")
    lignes = []
    for e in evs:
        t, txt = phrase_evenement(e)
        ts = datetime.fromisoformat(e["ts"]).astimezone(TZ).strftime("%d/%m %Hh%M")
        lignes.append(f"{emo(e['organiser'])} <b>{esc(t)}</b> · {esc(txt)} <i>(détecté le {ts})</i>")
    if demo:
        lignes.append("<i>Exemples fictifs, uniquement pour montrer la mise en forme :</i>")
        lignes += [
            f"{emo('Points De Vue')} <b>Prix en baisse</b> · Points De Vue, Louviers, 7 déc. : 235 → 199 € <i>(détecté le 26/11 07h10)</i>",
            f"{emo('Point Permis Prevention')} <b>Nouveau stage</b> · Point Permis Prevention, Bernay, 25 janv. : 262 € <i>(détecté le 26/11 11h42)</i>",
            f"{emo('Automobile Club Association')} <b>Stage supprimé</b> · Automobile Club Association, Évreux, 15 déc. à J-19 (dernier statut : places disponibles) <i>(détecté le 26/11 15h05)</i>",
        ]
    h.append("<p>" + ("<br>".join(lignes) if lignes else "Aucun changement détecté.") + "</p>")

    h.append("<h3>Semaine par semaine</h3>")
    for lundi in semaines:
        dim = lundi + timedelta(days=6)
        sem = [m for m in stages if lundi <= m["start"] <= dim]
        n_ax = sum(1 for m in sem if m["own"])
        h.append("<hr>")
        h.append(f"<h3>{esc(label_semaine(lundi))} : {len(sem)} stage{'s' if len(sem) != 1 else ''} "
                 f"({n_ax} Axéfor, {len(sem) - n_ax} concurrent{'s' if len(sem) - n_ax > 1 else ''})</h3>")
        if not sem:
            h.append("<p>Aucun stage.</p>")
            continue
        groupes = defaultdict(list)
        for m in sem:
            groupes[(m["start"], m["end"])].append(m)
        for (s_, e_) in sorted(groupes):
            g = sorted(groupes[(s_, e_)], key=lambda m: (not m["own"], m["organiser"], m["city"]))
            rows = []
            for m in g:
                prix = m["prix"]
                st = STATUTS.get(m["statut"], "")
                if m["places"] is not None and m["statut"] == "ouvert":
                    st = f"{m['places']} places restantes"
                det = ""
                if len(set(prix.values())) > 1:
                    det = " [" + " / ".join(f"{v} € {esc(k)}" for k, v in
                                            sorted(prix.items(), key=lambda x: x[1])) + "]"
                rows.append(f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b> · {esc(m['city'])} · "
                            f"<b>{min(prix.values())} €</b>" + (f" · {st}" if st else "") + det)
            h.append(f"<p><b>{esc(label_jours(s_, e_))} = {len(g)} stage{'s' if len(g) != 1 else ''}</b><br>"
                     + "<br>".join(rows) + "</p>")

    ecarts = [m for m in stages if len(set(m["prix"].values())) > 1]
    if ecarts:
        h.append("<hr><h3>Écarts de prix entre sites</h3><p>Un même stage affiché à des prix différents selon le site.<br>")
        h.append("<br>".join(
            f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b> · {esc(m['city'])} · {m['start'].day} "
            f"{MOIS_C[m['start'].month]} : " + " / ".join(f"{v} € ({esc(k)})" for k, v in
                                                          sorted(m['prix'].items(), key=lambda x: x[1]))
            for m in sorted(ecarts, key=lambda m: m["start"])) + "</p>")
    h.append("<hr><p><i>Sources : stage-recuperation-points.com, sites de Points De Vue, d'Anper et d'Actiroute. "
             "Mobilité Club n'est pas encore suivi.</i></p>")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"))
    ap.add_argument("--out", default="rapport.html")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--email", action="store_true",
                    help="version e-mail (émojis de couleur, balises autorisées uniquement)")
    a = ap.parse_args()
    now = datetime.now(TZ)
    if a.email:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(construire_email(a.data, a.weeks, a.demo, a.test, now))
        print(f"rapport e-mail écrit : {a.out}")
        return
    body = construire(a.data, a.weeks, a.demo, a.test, now)
    page = ('<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body>'
            f'{body}</body></html>')
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"rapport écrit : {a.out}")


if __name__ == "__main__":
    main()
