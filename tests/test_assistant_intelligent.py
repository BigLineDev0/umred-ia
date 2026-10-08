"""
Scénarios réels SenLab : navigation selon le rôle, réservation guidée,
contexte conversationnel, permissions, ambiguïtés et erreurs. Comme les
autres tests, ils tournent sans modèle de langage (règles seules) et avec
un faux Django.
"""
import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.assistant.contexte import lire_numero
from app.core.constantes import Role
from app.core.navigation import PAGES, page_demandee, pages_accessibles
from app.nlu.regles import extraire_par_regles
from app.nlu.texte import normaliser_texte
from app.schemas.chat import ChatAction
from app.services.disponibilite import decouper_en_creneaux, fenetre_moment, jours_de_la_periode, restreindre
from tests.conftest import AUJOURD_HUI, DEMAIN

ROUTES_ANGULAR = {  # routes réellement déclarées dans le frontend (app.routes.ts, Features/*/*.routes.ts)
    "/equipements", "/equipements/ajouter", "/laboratoires", "/laboratoires/ajouter", "/consommables",
    "/consommables/ajouter", "/reservations/ajouter", "/reservations/a-valider", "/maintenances",
    "/maintenances/pannes", "/utilisateurs", "/journal-activite", "/rapports", "/pilotage", "/etablissement",
    "/notifications", "/profil", "/plateforme", "/admin/dashboard", "/technicien/dashboard", "/enseignant/dashboard",
    "/etudiant/dashboard", "/admin/reservations", "/technicien/reservations", "/enseignant/reservations",
    "/etudiant/mes-demandes",
}


def routes(rep):
    return [a["route"] for a in rep.get("actions") or []]


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_admin_ajouter_un_equipement(envoyer, django):
    rep = envoyer("Je veux ajouter un équipement.", role="ADMIN")
    assert rep["type"] == "navigation"
    assert rep["actions"] == [{"type": "navigate", "label": "Ajouter un équipement", "route": "/equipements/ajouter"}]
    assert django.appels == []  # une navigation n'interroge pas Django


def test_etudiant_ajouter_un_equipement_refuse(envoyer, django):
    rep = envoyer("Je veux ajouter un équipement.", role="ETUDIANT")
    assert rep["type"] == "denied"
    assert "administrateurs et techniciens" in rep["reponse"]
    assert "/equipements/ajouter" not in routes(rep)
    assert routes(rep) == ["/equipements"]  # orientation adaptée : la liste reste consultable


def test_gerer_les_laboratoires(envoyer, django):
    assert routes(envoyer("Je veux gérer les laboratoires.", role="CHERCHEUR")) == ["/laboratoires"]


def test_creer_un_laboratoire_selon_le_role(envoyer, django):
    assert routes(envoyer("Je veux créer un nouveau laboratoire.", role="ADMIN")) == ["/laboratoires/ajouter"]
    rep = envoyer("Je veux créer un nouveau laboratoire.", role="TECHNICIEN")
    assert rep["type"] == "denied" and "/laboratoires/ajouter" not in routes(rep)


def test_voir_les_maintenances_selon_le_role(envoyer, django):
    assert routes(envoyer("Je veux voir les maintenances.", role="TECHNICIEN")) == ["/maintenances"]
    rep = envoyer("Je veux voir les maintenances.", role="ETUDIANT")
    assert rep["type"] == "denied" and routes(rep) == []


def test_gerer_les_reservations_selon_le_role(envoyer, django):
    assert routes(envoyer("Je veux gérer les réservations", role="TECHNICIEN")) == ["/reservations/a-valider"]
    # Un étudiant ne valide rien : il est orienté vers SES demandes.
    assert routes(envoyer("Je veux gérer les réservations", role="ETUDIANT")) == ["/etudiant/mes-demandes"]


def test_journal_reserve_aux_admins(envoyer, django):
    assert routes(envoyer("Ouvre le journal d'activité", role="ADMIN")) == ["/journal-activite"]
    assert envoyer("Ouvre le journal d'activité", role="CHERCHEUR")["type"] == "denied"


def test_catalogue_n_utilise_que_des_routes_angular_existantes():
    for page in PAGES:
        for role in Role:
            route = page.route_pour(role)
            assert route is None or route in ROUTES_ANGULAR, (page.cle, route)


def test_etudiant_ne_se_voit_proposer_aucune_page_d_administration():
    accessibles = {p.cle for p in pages_accessibles(Role.ETUDIANT)}
    assert accessibles.isdisjoint({"equipement_ajouter", "laboratoire_ajouter", "maintenances", "utilisateurs",
                                   "journal", "rapports", "pilotage", "etablissement", "reservations_a_valider"})


@pytest.mark.parametrize("route", ["https://evil.example", "//evil.example", "javascript:alert(1)", "equipements", "/a b"])
def test_seules_les_routes_internes_sont_acceptees(route):
    with pytest.raises(ValidationError):
        ChatAction(label="x", route=route)


def test_sortie_du_modele_ne_peut_pas_injecter_de_route(envoyer, django, monkeypatch):
    """Le modèle est une donnée non fiable : il ne choisit qu'une intention, jamais une route."""
    from app.nlu import extractor

    async def modele_malveillant(*_args, **_kwargs):
        return '{"intention": "naviguer", "route": "https://evil.example", "equipement": null}'

    monkeypatch.setattr(extractor, "generer", modele_malveillant)
    rep = envoyer("J'aimerais m'occuper de tout ça", role="ETUDIANT")
    assert rep["type"] == "navigation"
    assert routes(rep) and all(r in ROUTES_ANGULAR for r in routes(rep))
    assert "/utilisateurs" not in routes(rep)


# ---------------------------------------------------------------------------
# Réservations et disponibilités
# ---------------------------------------------------------------------------

def test_reserver_le_pcr_demain_matin(envoyer, django):
    rep = envoyer("Je veux réserver le PCR demain matin.")
    assert rep["type"] == "clarification"
    assert [o["label"] for o in rep["options"]] == ["8h – 10h", "10h – 12h"]  # seulement le matin
    assert rep["data"]["creneaux"] == [{"debut": "08:00", "fin": "10:00"}, {"debut": "10:00", "fin": "12:00"}]

    rep = envoyer("creneau_1")
    assert rep["type"] == "confirmation"
    assert rep["details_confirmation"]["equipement"] == "Thermocycleur PCR"
    assert (rep["details_confirmation"]["heure_debut"], rep["details_confirmation"]["heure_fin"]) == ("10:00", "12:00")
    # Django a vérifié la demande AVANT la confirmation, sans rien créer.
    assert len(django.appels_post("/reservations/verifier/")) == 1
    assert django.appels_post("/reservations/") == []

    rep = envoyer("oui")
    assert rep["type"] == "reservation" and rep["data"]["id"] == 200
    assert json.loads(django.appels_post("/reservations/")[0])["equipements"] == [5]


def test_conflit_detecte_avant_confirmation_avec_alternatives_django(envoyer, django):
    django.reponse_verification = (200, {
        "disponible": False,
        "conflits": [{"equipement_id": 5, "equipement": "Thermocycleur PCR", "heure_debut": "09:00", "heure_fin": "11:00"}],
        "alternatives": {"creneaux": [
            {"date": DEMAIN, "heure_debut": "11:00", "heure_fin": "13:00", "type": "plus_tard", "message": "Libre de 11h à 13h"},
            {"date": DEMAIN, "heure_debut": "14:00", "heure_fin": "16:00", "type": "plus_tard", "message": "Libre de 14h à 16h"},
        ], "equipements_equivalents": []},
    })
    rep = envoyer("Réserve le PCR demain de 9h à 11h")
    assert rep["necessite_confirmation"] is False
    assert "Thermocycleur PCR est déjà réservé" in rep["reponse"]
    assert [o["value"] for o in rep["options"]] == ["alt_0", "alt_1"]
    assert django.appels_post("/reservations/") == []

    django.reponse_verification = (200, {"disponible": True, "conflits": [], "statut_prevu": "VALIDEE"})
    rep = envoyer("alt_0")
    assert rep["details_confirmation"]["heure_debut"] == "11:00"


def test_statut_prevu_annonce_avant_confirmation(envoyer, django):
    django.reponse_verification = (200, {"disponible": True, "conflits": [], "statut_prevu": "EN_ATTENTE",
                                         "raison_statut": "Votre encadrant traitera votre demande."})
    rep = envoyer("Réserve la centrifugeuse demain de 14h à 16h", role="ETUDIANT")
    assert rep["necessite_confirmation"] is True
    assert "soumise à validation" in rep["reponse"] and "encadrant" in rep["reponse"]


def test_regle_django_non_respectee(envoyer, django):
    django.reponse_verification = (400, ["La durée maximale d'une réservation est de 4 heures."])
    rep = envoyer("Réserve la centrifugeuse demain de 8h à 18h")
    assert rep["type"] == "error"
    assert "4 heures" in rep["reponse"]


def test_microscope_disponible_vendredi(envoyer, django):
    rep = envoyer("Est-ce que le microscope est disponible vendredi ?")
    assert rep["type"] == "availability"
    assert rep["data"]["disponible"] is True and rep["data"]["creneaux"]


def test_pcr_libre_demain_matin_consulte_django(envoyer, django):
    django.creneaux_occupes = [{"date": DEMAIN, "heure_debut": "08:00:00", "heure_fin": "10:00:00", "statut": "VALIDEE"}]
    rep = envoyer("Le PCR est-il libre demain matin ?")
    assert rep["type"] == "availability"
    assert rep["data"]["creneaux"] == [{"date": DEMAIN, "debut": "10:00", "fin": "12:00"}]
    assert any(c == "/reservations/creneaux_occupes/" for _, c, _ in django.appels)


def test_creneau_precis_libre_puis_reservation(envoyer, django):
    rep = envoyer("Le PCR est-il libre demain de 9h à 11h ?")
    assert rep["reponse"].startswith("Oui")
    rep = envoyer(rep["options"][0]["value"])
    assert (rep["details_confirmation"]["heure_debut"], rep["details_confirmation"]["heure_fin"]) == ("09:00", "11:00")


def test_montre_moi_mes_reservations(envoyer, django):
    rep = envoyer("Montre-moi mes réservations.", role="ETUDIANT")
    assert rep["type"] == "reservations"
    assert [r["id"] for r in rep["data"]] == [100]
    assert rep["data"][0]["equipements"] == ["Centrifugeuse Eppendorf"]
    assert routes(rep) == ["/etudiant/mes-demandes"]  # la route dépend du rôle


def test_mes_reservations_cette_semaine_filtrees_par_django(envoyer, django):
    rep = envoyer("C'est quoi mes réservations cette semaine ?")
    assert rep["type"] == "reservations"
    params = django.appels_get("/reservations/")[-1]
    assert params["date_debut"] == AUJOURD_HUI.isoformat() and "date_fin" in params


def test_annule_ma_reservation_de_demain(envoyer, django):
    rep = envoyer("Annule ma réservation de demain.")
    assert rep["necessite_confirmation"] is True
    assert rep["details_confirmation"]["equipement"] == "Centrifugeuse Eppendorf"


def test_pourquoi_ma_reservation_a_ete_refusee(envoyer, django):
    rep = envoyer("Pourquoi ma réservation a été refusée ?")
    assert "réservé à un TP" in rep["reponse"] and "Moussa Ndiaye" in rep["reponse"]
    assert django.appels_get("/reservations/")[-1]["statut"] == "REFUSEE"
    assert django.appels_post("/reservations/95/annuler/") == []  # surtout pas une annulation


def test_meme_equipement_que_la_derniere_fois(envoyer, django):
    rep = envoyer("Je veux réserver le même équipement que la dernière fois.")
    assert "Thermocycleur PCR" in rep["reponse"]  # retrouvé dans l'historique Django
    assert "Pour quelle date" in rep["reponse"]


# ---------------------------------------------------------------------------
# Contexte conversationnel
# ---------------------------------------------------------------------------

def test_conversation_pcr_demain_le_matin_le_premier(envoyer, django):
    rep = envoyer("Je veux réserver le PCR.")
    assert "Pour quelle date" in rep["reponse"] and "Thermocycleur PCR" in rep["reponse"]

    rep = envoyer("Demain.")
    assert "Quel équipement" not in rep["reponse"]
    assert rep["options"][0]["value"] == "creneau_0"

    rep = envoyer("Le matin.")
    assert [o["label"] for o in rep["options"]] == ["8h – 10h", "10h – 12h"]

    rep = envoyer("Le premier")
    assert rep["details_confirmation"]["equipement"] == "Thermocycleur PCR"
    assert (rep["details_confirmation"]["heure_debut"], rep["details_confirmation"]["heure_fin"]) == ("08:00", "10:00")


def test_reserve_le_apres_une_consultation(envoyer, django):
    envoyer("Le PCR est-il libre demain matin ?")
    rep = envoyer("Je veux le réserver")
    assert "Thermocycleur PCR" in rep["reponse"]
    assert rep["options"][0]["value"] == "creneau_0"  # date reprise : seul l'horaire est demandé


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_statistiques_globales_non_demandees_a_django_pour_un_technicien(envoyer, django):
    rep = envoyer("Combien de réservations au total sur la plateforme ?", role="TECHNICIEN")
    assert "réservées aux administrateurs" in rep["reponse"]
    assert all("all" not in p for p in django.appels_get("/reservations/"))


def test_refus_django_explique_clairement(envoyer, django):
    django.reponse_annulation = (403, {"detail": "Vous ne pouvez annuler que vos propres réservations."})
    envoyer("Annule ma réservation de demain.")
    rep = envoyer("oui")
    assert rep["type"] == "denied"
    assert "rôle actuel" in rep["reponse"]


# ---------------------------------------------------------------------------
# Ambiguïtés et erreurs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", ["Je veux réserver quelque chose.", "Je veux réserver demain."])
def test_equipement_manquant(envoyer, django, message):
    rep = envoyer(message)
    assert rep["type"] == "clarification"
    assert rep["reponse"].startswith("Quel équipement souhaitez-vous réserver ?")


def test_equipement_inexistant(envoyer, django):
    rep = envoyer("Je veux réserver le XYZ-999.")
    assert "aucun équipement correspondant à « XYZ-999 »" in rep["reponse"]
    assert django.appels_post("/reservations/") == []


def test_django_indisponible_reponse_typee(envoyer, django):
    django.panne = True
    assert envoyer("Montre-moi mes réservations.")["type"] == "error"


# ---------------------------------------------------------------------------
# Briques unitaires
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message, cle", [
    ("Je veux ajouter un équipement", "equipement_ajouter"),
    ("je veux gérer les laboratoires", "laboratoires"),
    ("Je veux voir les maintenances", "maintenances"),
    ("quels équipements sont en panne ?", "pannes"),
    ("Quels équipements sont disponibles ?", None),        # disponibilité, pas navigation
    ("prochaine maintenance du microscope", None),         # question sur UN équipement
    ("Est-ce que je peux avoir le microscope du labo ?", None),  # « avoir » ne contient pas le verbe « voir »
    ("Quelles sont mes demandes en attente ?", None),      # mes demandes, pas la file de validation
])
def test_detection_de_page(message, cle):
    page = page_demandee(normaliser_texte(message))
    assert (page.cle if page else None) == cle


@pytest.mark.parametrize("message, attendu", [
    ("le premier", 0), ("Le deuxième.", 1), ("2", 1), ("le dernier", 2), ("le matin", None), ("le quatrième", None),
])
def test_lire_numero_ordinal(message, attendu):
    assert lire_numero(normaliser_texte(message), 3) == attendu


@pytest.mark.parametrize("message, moment, periode", [
    ("demain matin", "matin", "jour"), ("vendredi après-midi", "apres_midi", "jour"),
    ("toute la journée", "journee", "jour"), ("en début de semaine", None, "debut_semaine"),
    ("la semaine prochaine", None, "semaine_prochaine"),
])
def test_moments_et_periodes(message, moment, periode):
    ext = extraire_par_regles(message)
    assert (ext.moment, ext.periode) == (moment, periode)


def test_debut_de_semaine():
    assert jours_de_la_periode("debut_semaine", date(2026, 10, 7)) == ["2026-10-12", "2026-10-13", "2026-10-14"]
    assert jours_de_la_periode("debut_semaine", date(2026, 10, 6)) == ["2026-10-06", "2026-10-07"]


def test_creneaux_proposes_dans_la_fenetre():
    libres = [("08:00", "09:00"), ("10:00", "19:00")]
    assert decouper_en_creneaux(restreindre(libres, fenetre_moment("matin"))) == [("08:00", "09:00"), ("10:00", "12:00")]
    assert fenetre_moment("matin", ouverture="07:30", fermeture="17:00") == ("07:30", "12:00")


def test_fusion_conserve_le_moment_et_l_equipement_des_regles():
    from app.nlu.extractor import fusionner
    message = "je voudrais réserver le PCR demain matin"
    resultat = fusionner({"intention": "reserver", "equipement": None}, extraire_par_regles(message), message)
    assert (resultat.equipement, resultat.moment) == ("PCR", "matin")


def test_heure_de_l_etablissement_et_non_du_serveur():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.core import temps
    attendu = datetime.now(ZoneInfo("Africa/Dakar")).replace(tzinfo=None)
    assert abs(temps.maintenant() - attendu) < timedelta(seconds=5)


def test_reponses_concises_details_dans_les_boutons(envoyer, django):
    """Le texte ne répète pas la liste : les détails sont en sous-titre des boutons."""
    rep = envoyer("Réserve le microscope demain de 14h à 16h")
    assert "\n" not in rep["reponse"]
    assert [o["description"] for o in rep["options"]] == ["Labo Biologie · disponible"] * 2

    rep = envoyer("Quels équipements sont disponibles demain ?", session_id="session-test-2")
    assert "\n" not in rep["reponse"]
    assert all("Labo" in o["description"] for o in rep["options"])


def test_aide_propose_des_reponses_rapides(envoyer, django):
    rep = envoyer("Je ne comprends pas le labo")
    assert [o["label"] for o in rep["options"]][:1] == ["Réserver un équipement"]
