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
SOURCES = {"axefor": "axefor.fr", "srp-eure": "stage-recuperation-points.com", "pdv-louviers": "stage-pointsdevue.fr",
           "pdv-vernon": "stage-pointsdevue.fr", "anper": "permis-a-points-anper.com",
           "actiroute": "actiroute.com", "mobilite": "mobiliteclub.fr"}
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
                                      "places": None, "number": None})
            if r.get("number") and not m["number"]:
                m["number"] = r["number"]  # le n° vient de l'agrégateur ou du site de l'organisateur
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
    site = SOURCES.get(e["source"], e["source"])
    num = f", n° {e['number']}" if e.get("number") else ""
    qui = f"{e['organiser']}, {e['city']}, {d} (sur {site}{num})"
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


def prix_supports(prix, gras=True):
    """« 199 € sur stage-pointsdevue.fr · 235 € sur stage-recuperation-points.com »"""
    items = sorted(prix.items(), key=lambda x: (x[1], x[0]))
    b0, b1 = ("<b>", "</b>") if gras else ("", "")
    return " · ".join(f"{b0}{v} €{b1} sur {esc(k)}" for k, v in items)


def puce(org, texte, gras=True):
    c = couleur(org)
    return (f'<span style="display:inline-block;background:{c};color:#fff;border-radius:10px;'
            f'padding:1px 8px;font-size:12px;font-weight:{"bold" if gras else "normal"}">{esc(texte)}</span>')


def ligne_stage(m):
    c = couleur(m["organiser"])
    statut = STATUTS.get(m["statut"], "")
    if m["places"] is not None and m["statut"] in ("ouvert",):
        statut = f"{m['places']} places restantes"
    return (f'<tr><td style="border-left:6px solid {c};padding:6px 10px;background:#fafafa">'
            f'<span style="color:{c};font-weight:bold">{esc(m["organiser"])}</span> '
            f'· {esc(m["city"])}<br><span style="font-size:13px">{prix_supports(m["prix"])}</span>'
            + (f'<br><span style="color:#555;font-size:12px">{statut}</span>' if statut else "")
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
            det = prix_supports(m["prix"], gras=False) + f" (écart {max(m['prix'].values()) - min(m['prix'].values())} €)"
            h.append(f'<tr><td style="border-left:6px solid {c};padding:5px 10px;background:#fafafa">'
                     f'<b>{esc(m["organiser"])}</b> · {esc(m["city"])} · {m["start"].day} '
                     f'{MOIS_C[m["start"].month]}<br><span style="font-size:12px;color:#555">{det}'
                     f'</span></td></tr><tr><td style="height:4px"></td></tr>')
        h.append("</table>")

    h.append('<div style="margin-top:18px;color:#777;font-size:12px">Sources : '
             'stage-recuperation-points.com, sites d\'Axéfor, de Points De Vue, d\'Anper et d\'Actiroute. '
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
            f"{emo('Points De Vue')} <b>Prix en baisse</b> · Points De Vue, Louviers, 7 déc. (sur stage-recuperation-points.com) : 235 → 199 € <i>(détecté le 26/11 07h10)</i>",
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
                st = STATUTS.get(m["statut"], "")
                if m["places"] is not None and m["statut"] == "ouvert":
                    st = f"{m['places']} places restantes"
                rows.append(f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b> · {esc(m['city'])} · "
                            f"{prix_supports(m['prix'])}" + (f" · {st}" if st else "")
                            + (f" · n° {esc(m['number'])}" if m["number"] else ""))
            h.append(f"<p><b>{esc(label_jours(s_, e_))} = {len(g)} stage{'s' if len(g) != 1 else ''}</b><br>"
                     + "<br>".join(rows) + "</p>")

    ecarts = [m for m in stages if len(set(m["prix"].values())) > 1]
    if ecarts:
        h.append("<hr><h3>Écarts de prix entre sites</h3><p>Un même stage vendu à des prix différents selon le support.<br>")
        h.append("<br>".join(
            f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b> · {esc(m['city'])} · {m['start'].day} "
            f"{MOIS_C[m['start'].month]} : {prix_supports(m['prix'], gras=False)} "
            f"(écart {max(m['prix'].values()) - min(m['prix'].values())} €)"
            for m in sorted(ecarts, key=lambda m: m["start"])) + "</p>")
    h.append("<hr><p><i>Sources : stage-recuperation-points.com, sites d'Axéfor, de Points De Vue, d'Anper et d'Actiroute. "
             "Mobilité Club n'est pas encore suivi.</i></p>")
    return "\n".join(h)


LARGEUR = 40  # caractères par ligne dans un cadre (lisible sur iPhone en portrait)


def _wrap(texte, prefixe="", cont="   "):
    """Découpe un texte brut en lignes courtes (le gras est ajouté après)."""
    import textwrap
    return textwrap.wrap(texte, width=LARGEUR - 2 - len(prefixe), subsequent_indent=cont) or [""]


def cadre(titre_html, lignes_html):
    """Un encadré : coin haut avec titre, barre à gauche, coin bas (pas de bord droit : les émojis
    n'ont pas la largeur d'un caractère, un bord droit serait décalé)."""
    corps = "\n".join(("┃ " + l) if l else "┃" for l in lignes_html)
    return f"<pre>┏━ {titre_html}\n{corps}\n┗{'━' * (LARGEUR - 6)}</pre>"


JOURS_C = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


def label_jours_court(start, end):
    def j(d):
        return f"{JOURS_C[d.weekday()]} {'1er' if d.day == 1 else d.day}"
    if start == end:
        return j(start).capitalize()
    a, b = j(start), j(end)
    if start.month != end.month:
        a += f" {MOIS_C[start.month]}"
        b += f" {MOIS_C[end.month]}"
    return f"{a} + {b}".capitalize()


def lignes_stage(m):
    plein = f"{emo(m['organiser'])} {m['organiser']} · {m['city']}"
    if len(plein) <= LARGEUR - 2:
        out = [f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b> · {esc(m['city'])}"]
    else:  # trop long : la ville passe à la ligne
        out = [f"{emo(m['organiser'])} <b>{esc(m['organiser'])}</b>", f"   {esc(m['city'])}"]
    prix = sorted(m["prix"].items(), key=lambda x: (x[1], x[0]))
    for k, v in prix:
        out.append(f"   <b>{v} €</b> · {esc(k)}")
    if len(set(m["prix"].values())) > 1:
        out.append(f"   ⇄ écart de {max(m['prix'].values()) - min(m['prix'].values())} € entre supports")
    # on n'affiche le statut que s'il est notable (« places disponibles » = bruit)
    st = ""
    if m["statut"] in ("dernieres", "complet", "clos"):
        st = STATUTS[m["statut"]]
    elif m["places"] is not None and m["statut"] == "ouvert":
        st = f"{m['places']} places restantes"
    if st:
        out.append(f"   ⚠ {esc(st)}")
    return out


def construire_email_cadres(data, nb_semaines, demo, test, now):
    """Mise en page « un encadré par semaine » : uniquement des balises acceptées par l'envoi d'e-mail
    (h2, p, pre, b). La couleur de chaque organisme = un carré d'émoji."""
    today = now.date()
    stages = [m for m in charger(data) if m["end"] >= today]
    lundi0 = today - timedelta(days=today.weekday())
    semaines = [lundi0 + timedelta(weeks=i) for i in range(nb_semaines)]
    fin = semaines[-1] + timedelta(days=6)
    stages = [m for m in stages if m["start"] <= fin]
    h = []
    if test:
        h.append("<p><b>TEST</b> : nouvelle mise en page (un encadré par semaine).</p>")
    h.append(f"<h2>Veille stages Eure</h2><p>Point du {jour(today)} {MOIS[today.month]} {today.year} · "
             f"{nb_semaines} semaines à venir</p>")

    cnt = defaultdict(int)
    for m in stages:
        cnt[m["organiser"]] += 1
    ordre = sorted(cnt, key=lambda o: (o != "Axéfor", -cnt[o], o))
    n_ax = cnt.get("Axéfor", 0)
    brief = [f"<b>{len(stages)} stages</b> sur la période",
             f"dont {n_ax} Axéfor, {len(stages) - n_ax} concurrents", ""]
    brief += [f"{emo(o)} {esc(o)} : <b>{cnt[o]}</b>" for o in ordre]
    h.append(cadre("<b>EN BREF</b>", brief))

    # événements des dernières 24 h
    lignes = []
    evs = evenements(data, now - timedelta(hours=24))
    items = []
    for e in evs:
        t, txt = phrase_evenement(e)
        ts = datetime.fromisoformat(e["ts"]).astimezone(TZ).strftime("%d/%m %Hh%M")
        items.append((e["organiser"], t, txt, ts, False))
    if demo:
        items += [
            ("Points De Vue", "Prix en baisse", "Points De Vue, Louviers, 7 déc. (sur stage-recuperation-points.com) : 235 → 199 €", "26/11 07h10", True),
            ("Point Permis Prevention", "Nouveau stage", "Point Permis Prevention, Bernay, 25 janv. : 262 €", "26/11 11h42", True),
            ("Automobile Club Association", "Stage supprimé", "Automobile Club Association, Évreux, 15 déc. à J-19 (dernier statut : places disponibles)", "26/11 15h05", True),
        ]
    for org, t, txt, ts, fictif in items:
        brut = f"{emo(org)} {t} · {txt} (détecté le {ts}){' [exemple]' if fictif else ''}"
        ls = [esc(x) for x in _wrap(brut)]
        ls[0] = ls[0].replace(esc(t), f"<b>{esc(t)}</b>", 1)
        lignes += ls + [""]
    if not lignes:
        lignes = ["Aucun changement détecté."]
    else:
        lignes = lignes[:-1]
    titre_ev = "<b>ÉVÉNEMENTS DES DERNIÈRES 24 H</b>"
    h.append(cadre(titre_ev, lignes))
    if demo:
        h.append("<p><i>Les 3 événements marqués [exemple] sont fictifs, uniquement pour montrer la mise en forme.</i></p>")

    h.append("<h3>Semaine par semaine</h3>")
    for lundi in semaines:
        dim = lundi + timedelta(days=6)
        sem = [m for m in stages if lundi <= m["start"] <= dim]
        titre = f"<b>{esc(label_semaine(lundi).upper())}</b>"
        if not sem:
            h.append(cadre(titre, ["Aucun stage."]))
            continue
        n_ax = sum(1 for m in sem if m["own"])
        n_co = len(sem) - n_ax
        corps = [f"<b>{len(sem)} stage{'s' if len(sem) != 1 else ''}</b> · {n_ax} Axéfor, "
                 f"{n_co} concurrent{'s' if n_co > 1 else ''}"]
        ordre_sem = sorted(sem, key=lambda m: (not m["own"], m["organiser"], m["start"]))
        carres = [emo(m["organiser"]) for m in ordre_sem]
        for i in range(0, len(carres), 12):
            corps.append("".join(carres[i:i + 12]))
        groupes = defaultdict(list)
        for m in sem:
            groupes[(m["start"], m["end"])].append(m)
        for (s_, e_) in sorted(groupes):
            g = sorted(groupes[(s_, e_)], key=lambda m: (not m["own"], m["organiser"], m["city"]))
            j = (s_ - today).days
            corps.append("")
            lab = label_jours_court(s_, e_)
            suite = (f"{len(g)} stage{'s' if len(g) != 1 else ''}"
                     + (f" · J-{j}" if j > 0 else " · aujourd'hui" if j == 0 else ""))
            if len(lab) + 3 + len(suite) <= LARGEUR - 2:
                corps.append(f"<b>{esc(lab)}</b> · {suite}")
            else:
                corps += [f"<b>{esc(lab)}</b>", suite]
            for m in g:
                corps += lignes_stage(m)
        h.append(cadre(titre, corps))

    h.append("<p><i>Sources : stage-recuperation-points.com, sites d'Axéfor, de Points De Vue, d'Anper et "
             "d'Actiroute. Mobilité Club n'est pas encore suivi.</i></p>")
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
    ap.add_argument("--style", choices=["cadres", "simple"], default="simple",
                    help="mise en page de la version e-mail (défaut : simple = version n°2 validée)")
    a = ap.parse_args()
    now = datetime.now(TZ)
    if a.email:
        build = construire_email_cadres if a.style == "cadres" else construire_email
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(build(a.data, a.weeks, a.demo, a.test, now))
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
