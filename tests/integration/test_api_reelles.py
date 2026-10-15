"""
Intégration avec les vraies API (voir conftest.py). Chaque test vérifie
l'ÉTAT RÉEL de la base Django, pas seulement le texte de la réponse : une
opération interdite doit être impossible, pas seulement refusée en paroles.
Les tests partagent une base : leur ordre compte (pytest suit l'ordre du fichier).
"""
from collections import Counter
from datetime import date

import httpx

from .conftest import Conversation, django_get, toutes_les_reservations

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]


def jour(env) -> str:
    d = date.fromisoformat(env["donnees"]["jour"])
    return f"le {d.day} {MOIS[d.month - 1]}"


def statut(env, reservation_id: int) -> str:
    return next(r["statut"] for r in toutes_les_reservations(env) if r["id"] == reservation_id)


def entetes(env, utilisateur: str) -> dict:
    return {"Authorization": f"Bearer {env['jetons'][utilisateur]}"}


def test_disponibilite_lue_dans_django(env):
    rep = Conversation(env, "awa", "integration-dispo").dire(f"Le microscope optique est-il disponible {jour(env)} ?")
    assert rep["type"] == "availability", rep
    creneaux = rep["data"]["creneaux"]
    # La réservation VALIDÉE de Fatou (9h-11h) occupe ce créneau : aucun créneau libre ne le chevauche.
    assert creneaux and all(c["fin"] <= "09:00" or c["debut"] >= "11:00" for c in creneaux), creneaux


def test_reservation_etudiante_de_bout_en_bout(env):
    avant = {r["id"] for r in toutes_les_reservations(env)}
    conv = Conversation(env, "awa", "integration-reservation")
    rep = conv.dire(f"Réserve la centrifugeuse {jour(env)} de 14h à 16h")
    assert rep["necessite_confirmation"], rep
    # Une proposition n'est pas une réservation : rien n'est encore en base.
    assert {r["id"] for r in toutes_les_reservations(env)} == avant

    rep = conv.dire("oui")
    assert rep["type"] == "reservation" and rep["data"]["statut"] == "EN_ATTENTE", rep  # règle Django : étudiante
    nouvelles = [r for r in toutes_les_reservations(env) if r["id"] not in avant]
    assert len(nouvelles) == 1
    r = nouvelles[0]
    assert r["demandeur_nom"].startswith("Awa") and r["statut"] == "EN_ATTENTE"
    assert (r["heure_debut"], r["heure_fin"]) == ("14:00:00", "16:00:00")
    assert r["equipements"] == [env["donnees"]["equipements"]["centrifugeuse"]]

    # Ancienne confirmation : un nouveau « oui » ne crée rien.
    assert "aucune action en attente" in conv.dire("oui")["reponse"]
    # Même demande renvoyée : Django refuse le doublon, l'assistant le dit.
    rep = conv.dire(f"Réserve la centrifugeuse {jour(env)} de 14h à 16h")
    assert "déjà une demande" in rep["reponse"], rep
    assert len([r for r in toutes_les_reservations(env) if r["id"] not in avant]) == 1


def test_conflit_detecte_par_django(env):
    avant = len(toutes_les_reservations(env))
    rep = Conversation(env, "chercheur", "integration-conflit").dire(f"Réserve le microscope optique {jour(env)} de 9h à 11h")
    assert not rep.get("necessite_confirmation"), rep
    assert "déjà réservé" in rep["reponse"], rep
    assert len(toutes_les_reservations(env)) == avant


def test_etudiante_ne_peut_pas_annuler_la_reservation_d_autrui(env):
    rid = env["donnees"]["reservations"]["fatou"]
    conv = Conversation(env, "awa", "integration-autrui")
    conv.dire(f"Annule la réservation {rid}")
    conv.dire("oui")
    assert statut(env, rid) == "VALIDEE"
    # Appel direct à l'API avec le jeton de l'étudiante : Django refuse aussi.
    r = httpx.post(f"{env['django']}/reservations/{rid}/annuler/", headers=entetes(env, "awa"))
    assert r.status_code in (403, 404), r.text
    assert statut(env, rid) == "VALIDEE"


def test_etudiante_ne_peut_pas_valider(env):
    rid = env["donnees"]["reservations"]["awa"]
    rep = Conversation(env, "awa", "integration-valider").dire("Valide ma propre réservation.")
    assert rep["type"] == "denied", rep
    r = httpx.post(f"{env['django']}/reservations/{rid}/valider/", headers=entetes(env, "awa"))
    assert r.status_code == 403, r.text
    assert statut(env, rid) == "EN_ATTENTE"


def test_chercheur_voit_les_demandes_de_son_etudiante(env):
    rid = env["donnees"]["reservations"]["awa"]
    rep = Conversation(env, "chercheur", "integration-file").dire("Quelles réservations nécessitent encore une décision ?")
    assert "Awa" in rep["reponse"], rep
    assert rid in {d["id"] for d in rep["data"]}


def test_etudiante_n_a_pas_acces_a_la_file(env):
    rep = Conversation(env, "fatou", "integration-file-etu").dire("Quelles réservations nécessitent encore une décision ?")
    assert "validateurs" in rep["reponse"], rep
    assert django_get(env, "/reservations/file_attente/", "fatou").status_code == 403


def test_isolation_des_donnees(env):
    rep = Conversation(env, "fatou", "integration-isolation").dire("Quelles sont mes prochaines réservations ?")
    assert {d["id"] for d in rep["data"]} == {env["donnees"]["reservations"]["fatou"]}


def test_bilan_identique_aux_donnees_django(env):
    rep = Conversation(env, "awa", "integration-bilan").dire("Fais-moi le bilan des réservations du mois.")
    periode = rep["data"]["periode"]
    reelles = django_get(env, "/reservations/", "awa", date_debut=periode["debut"], date_fin=periode["fin"],
                         archivees="true").json()
    assert rep["data"]["total"] == len(reelles)
    assert rep["data"]["par_statut"] == {s: n for s, n in Counter(r["statut"] for r in reelles).items()}


def test_annulation_reelle_apres_confirmation(env):
    rid = env["donnees"]["reservations"]["awa"]
    conv = Conversation(env, "awa", "integration-annulation")
    rep = conv.dire("Annule ma réservation du spectrophotomètre")
    choix = f"annul_{rid}"
    if rep.get("options"):  # plusieurs réservations actives : on choisit celle du spectrophotomètre
        assert choix in [o["value"] for o in rep["options"]], rep
        rep = conv.dire(choix)
    assert rep["necessite_confirmation"], rep
    assert statut(env, rid) == "EN_ATTENTE"  # rien n'est annulé avant le « oui »
    conv.dire("oui")
    assert statut(env, rid) == "ANNULEE"


def test_aucun_jeton_dans_les_journaux(env):
    journaux = env["journal_ia"].read_text() + env["journal_django"].read_text()
    for utilisateur, jeton in env["jetons"].items():
        assert jeton not in journaux, f"jeton de {utilisateur} journalisé"


def test_django_arrete_aucune_donnee_fictive(env):
    env["arreter_django"]()
    rep = Conversation(env, "awa", "integration-panne").dire("Le microscope optique est-il disponible demain ?")
    assert rep["type"] == "error" and "joindre" in rep["reponse"], rep
    assert not rep.get("data")
