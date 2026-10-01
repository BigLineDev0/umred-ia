"""Scénarios de conversation complets, de la requête HTTP jusqu'aux appels Django."""
import json

from tests.conftest import DEMAIN, fabriquer_token


def test_reservation_complete_en_un_message(envoyer, django):
    rep = envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    assert rep["necessite_confirmation"] is True
    assert rep["details_confirmation"]["equipement"] == "Centrifugeuse Eppendorf"

    rep = envoyer("oui")
    assert "confirmé" in rep["reponse"]
    envoye = json.loads(django.appels_post("/reservations/")[0])
    assert envoye == {"laboratoire": 10, "equipements": [3], "date": DEMAIN,
                      "heure_debut": "14:00", "heure_fin": "16:00", "motif": "Réservation via l'assistant UMRED"}


def test_reservation_en_attente_de_validation(envoyer, django):
    django.reponse_creation = (201, {"id": 201, "statut": "EN_ATTENTE"})
    envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    assert "attente de validation" in envoyer("oui")["reponse"]


def test_informations_manquantes_completees_au_tour_suivant(envoyer, django):
    rep = envoyer("Réserve la centrifugeuse demain à 10h")
    assert "l'heure de fin" in rep["reponse"]

    rep = envoyer("jusqu'à 12h")
    assert rep["necessite_confirmation"] is True
    assert (rep["details_confirmation"]["heure_debut"], rep["details_confirmation"]["heure_fin"]) == ("10:00", "12:00")


def test_equipement_ambigu_puis_selection(envoyer, django):
    rep = envoyer("Réserve le microscope demain de 14h à 16h")
    assert [o["value"] for o in rep["options"]] == ["equip_1", "equip_2"]

    rep = envoyer("equip_2")
    assert rep["details_confirmation"]["equipement"] == "Microscope électronique"


def test_identifiant_forge_refuse(envoyer, django):
    envoyer("Réserve le microscope demain de 14h à 16h")
    # L'équipement 3 existe mais n'a PAS été proposé : le choix est rejeté.
    rep = envoyer("equip_3")
    assert rep["necessite_confirmation"] is False
    assert "plus valable" in rep["reponse"]


def test_equipement_en_panne_non_reservable(envoyer, django):
    rep = envoyer("Réserve le spectrophotomètre demain de 14h à 16h")
    assert "pas réservable" in rep["reponse"]


def test_date_passee_refusee(envoyer, django):
    rep = envoyer("Réserve la centrifugeuse le 01/01/2020 de 10h à 12h")
    assert "déjà passée" in rep["reponse"]


def test_conflit_propose_des_alternatives(envoyer, django):
    django.reponse_creation = (409, {
        "conflit": True,
        "conflits": [{"equipement_id": 1, "equipement": "Centrifugeuse Eppendorf",
                      "heure_debut": "14:00", "heure_fin": "16:00"}],
        "alternatives": {"creneaux": [{"date": DEMAIN, "heure_debut": "16:00", "heure_fin": "18:00",
                                       "type": "plus_tard", "message": "Le créneau sera disponible à partir de 16h00"}],
                         "equipements_equivalents": []},
    })
    envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    rep = envoyer("oui")
    assert rep["options"][0]["value"] == "alt_0"

    rep = envoyer("alt_0")
    assert rep["details_confirmation"]["heure_debut"] == "16:00"


def test_non_annule_la_demande(envoyer, django):
    envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    envoyer("non")
    assert django.appels_post("/reservations/") == []


def test_annulation_appelle_bien_l_annulation(envoyer, django):
    """Régression : confirmer une annulation créait une réservation."""
    rep = envoyer("Annule ma prochaine réservation")
    assert rep["necessite_confirmation"] is True

    rep = envoyer("oui")
    assert "annulée" in rep["reponse"]
    assert django.appels_post("/reservations/100/annuler/") == [b""]
    assert django.appels_post("/reservations/") == []


def test_session_isolee_par_utilisateur(envoyer, django):
    envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    # Un autre utilisateur réutilise le même session_id : il ne récupère
    # pas la réservation en attente de confirmation du premier.
    envoyer("oui", token=fabriquer_token(user_id=2))
    assert django.appels_post("/reservations/") == []


def test_mes_reservations(envoyer, django):
    rep = envoyer("Quelles sont mes réservations ?")
    assert "Centrifugeuse Eppendorf" in rep["reponse"]
    assert "validée" in rep["reponse"]


def test_disponibilites_puis_reservation(envoyer, django):
    rep = envoyer("Quels équipements sont disponibles demain ?")
    valeurs = [o["value"] for o in rep["options"]]
    assert "dispo_4" not in valeurs  # l'équipement en panne n'est pas proposé

    rep = envoyer("dispo_3")
    assert "l'heure de début" in rep["reponse"]
    rep = envoyer("de 10h à 12h")
    assert rep["details_confirmation"]["equipement"] == "Centrifugeuse Eppendorf"


def test_changement_de_sujet_pendant_une_confirmation(envoyer, django):
    envoyer("Réserve la centrifugeuse demain de 14h à 16h")
    rep = envoyer("Quelles sont mes réservations ?")
    assert "prochaines réservations" in rep["reponse"]
    envoyer("oui")  # la confirmation précédente a été abandonnée
    assert django.appels_post("/reservations/") == []


def test_salutation_personnalisee(envoyer, django):
    assert envoyer("bonjour")["reponse"].startswith("Bonjour Awa")


def test_django_indisponible_message_clair(envoyer, django):
    django.panne = True
    rep = envoyer("Quelles sont mes réservations ?")
    assert "joindre" in rep["reponse"]


def test_accueil(client, django):
    resp = client.get("/api/chat/accueil", headers={"Authorization": f"Bearer {fabriquer_token()}"})
    assert resp.status_code == 200
    assert resp.json()["reponse"].startswith("Bonjour Awa")


def test_message_trop_long_refuse(client, django):
    resp = client.post("/api/chat", json={"session_id": "session-test-1", "message": "a" * 1001},
                       headers={"Authorization": f"Bearer {fabriquer_token()}"})
    assert resp.status_code == 422


def test_limite_de_debit(client, django):
    headers = {"Authorization": f"Bearer {fabriquer_token()}"}
    codes = [client.post("/api/chat", json={"session_id": "session-test-1", "message": "bonjour"}, headers=headers).status_code
             for _ in range(25)]
    assert codes[-1] == 429
