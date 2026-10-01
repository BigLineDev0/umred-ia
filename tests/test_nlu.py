from datetime import date, datetime

import pytest

from app.nlu.extractor import extraire_json, fusionner
from app.nlu.intentions import Intention
from app.nlu.normalisation import normaliser_date, normaliser_heure
from app.nlu.regles import extraire_date, extraire_heures, extraire_par_regles
from app.services.disponibilite import calculer_creneaux_libres, chevauche
from app.services.matching import equipements_mentionnes

DIMANCHE = date(2026, 9, 27)


@pytest.mark.parametrize("message, attendu", [
    ("demain de 9h à 11h", ("09:00", "11:00")),
    ("de 10 à 12h", ("10:00", "12:00")),
    ("entre 9h30 et 11h", ("09:30", "11:00")),
    ("à 14h30 pendant 2h", ("14:30", "16:30")),
    ("à midi pendant une heure", ("12:00", "13:00")),
    ("le 12 à 14h", ("14:00", None)),  # « le 12 » est une date, pas une heure
    ("10h-12h", ("10:00", "12:00")),
    ("à 14:30", ("14:30", None)),
    ("jusqu'à 12h", (None, "12:00")),
])
def test_extraire_heures(message, attendu):
    assert extraire_heures(message) == attendu


@pytest.mark.parametrize("expression, attendu", [
    ("demain", "2026-09-28"),
    ("après-demain", "2026-09-29"),
    ("lundi prochain", "2026-09-28"),
    ("dimanche", "2026-10-04"),  # jamais aujourd'hui : la prochaine occurrence
    ("le 12", "2026-10-12"),     # le 12 septembre est passé -> octobre
    ("2026-12-01", "2026-12-01"),
    ("null", None),
])
def test_normaliser_date(expression, attendu):
    assert normaliser_date(expression, aujourd_hui=DIMANCHE) == attendu


def test_extraire_date_avec_mois():
    assert extraire_date("le 5 octobre à 10h").endswith("-10-05")


@pytest.mark.parametrize("brut, attendu", [("9h", "09:00"), ("14:30:00", "14:30"), ("25h", None), ("null", None)])
def test_normaliser_heure(brut, attendu):
    assert normaliser_heure(brut) == attendu


@pytest.mark.parametrize("message, intention", [
    ("Quelles sont mes réservations ?", Intention.CONSULTER_MES_RESERVATIONS),  # et non « reserver »
    ("Annule ma réservation de demain", Intention.ANNULER),
    ("reserver le microscope", Intention.RESERVER),  # sans accent
    ("Comment faire une réservation ?", Intention.AUTRE),
    ("Combien de réservations ce mois-ci ?", Intention.STATISTIQUES),
    ("le microscope est dispo demain ?", Intention.CONSULTER_DISPONIBILITE),
    ("bonjour", Intention.SALUTATION),
])
def test_intention_par_regles(message, intention):
    assert extraire_par_regles(message).intention == intention


def test_fusion_ignore_une_heure_inventee_par_le_modele():
    message = "Réserve le microscope demain"
    modele = {"intention": "reserver", "equipement": "microscope", "date": "demain", "heure_debut": "14:00", "heure_fin": "16:00"}
    resultat = fusionner(modele, extraire_par_regles(message), message)
    assert resultat.intention == Intention.RESERVER
    assert resultat.equipement == "microscope"
    assert (resultat.heure_debut, resultat.heure_fin) == (None, None)


def test_fusion_intention_hors_catalogue():
    message = "bonjour"
    resultat = fusionner({"intention": "pirater"}, extraire_par_regles(message), message)
    assert resultat.intention == Intention.SALUTATION


def test_extraire_json_tolere_le_bruit():
    assert extraire_json('Voici : ```json\n{"intention": "reserver"}\n```') == {"intention": "reserver"}
    with pytest.raises(ValueError):
        extraire_json('["pas", "un", "objet"]')


def test_creneaux_libres():
    occupes = [{"date": "2026-10-01", "heure_debut": "09:00:00", "heure_fin": "10:00:00"},
               {"date": "2026-10-01", "heure_debut": "09:30:00", "heure_fin": "12:00:00"}]
    libres = calculer_creneaux_libres(occupes, ["2026-10-01"], maintenant=datetime(2026, 9, 27, 8))
    assert libres["2026-10-01"] == [("08:00", "09:00"), ("12:00", "19:00")]


def test_creneaux_libres_aujourd_hui_ne_propose_pas_le_passe():
    libres = calculer_creneaux_libres([], ["2026-09-27"], maintenant=datetime(2026, 9, 27, 15, 30))
    assert libres["2026-09-27"] == [("15:30", "19:00")]


def test_creneaux_libres_selon_les_horaires_de_l_etablissement():
    libres = calculer_creneaux_libres([], ["2026-10-01"], maintenant=datetime(2026, 9, 27, 8),
                                      ouverture="07:30", fermeture="17:00")
    assert libres["2026-10-01"] == [("07:30", "17:00")]


def test_chevauchement():
    occupes = [{"heure_debut": "10:00:00", "heure_fin": "12:00:00"}]
    assert chevauche(occupes, "11:00", "13:00")
    assert not chevauche(occupes, "12:00", "13:00")  # bord à bord : pas de conflit


def test_matching():
    equipements = [{"id": 1, "nom": "Microscope optique"}, {"id": 2, "nom": "Microscope électronique"},
                   {"id": 3, "nom": "Thermocycleur PCR"}]
    assert [e["id"] for e in equipements_mentionnes("le microscope", equipements)["microscope"]] == [1, 2]
    assert [e["id"] for e in equipements_mentionnes("microscope electronique", equipements)["microscope"]] == [2]
    assert list(equipements_mentionnes("réserve le PCR", equipements)) == ["thermocycleur"]
    assert equipements_mentionnes("merci beaucoup", equipements) == {}


def test_modele_non_sollicite_quand_les_regles_suffisent(monkeypatch):
    import asyncio

    from app.nlu import extractor

    async def interdit(*args, **kwargs):
        raise AssertionError("le modèle n'aurait pas dû être appelé")

    monkeypatch.setattr(extractor, "generer", interdit)
    assert asyncio.run(extractor.extraire("Réserve le microscope demain à 10h")).intention == Intention.RESERVER
    resultat = asyncio.run(extractor.extraire("demain de 10h à 12h", reservation_en_cours=True))
    assert (resultat.heure_debut, resultat.heure_fin) == ("10:00", "12:00")
