import asyncio

from app.assistant import synthese

INDICATEURS = {
    "periode": {"debut": "2026-09-21", "fin": "2026-09-27"},
    "volumes": {"reservations": 42, "heures_utilisation": 96.5, "utilisateurs_actifs": 17, "taux_annulation": 9.5,
                "delai_moyen_validation_h": 30.2, "demandes_en_attente_48h": 3},
    "occupation_equipements": [{"nom": "Microscope confocal", "taux_occupation": 81.0}],
    "heures_de_pointe": [{"jour": "Mardi", "heure": "10h", "reservations": 8}],
    "recommandations": [{"titre": "Microscope confocal est saturé", "message": "Occupé à 81.0 %."}],
    "synthese_regles": "Synthèse par règles.",
}


def test_chiffre_invente_rejete():
    faits = synthese._faits(INDICATEURS)
    assert synthese.chiffres_verifies("42 réservations pour 96,5 heures, microscope à 81 %.", faits)
    assert not synthese.chiffres_verifies("Il y a eu 57 réservations cette semaine.", faits)


def test_repli_sur_les_regles_si_hallucination(monkeypatch):
    async def generer_faux(*_args, **_kwargs):
        return "Cette semaine, 999 réservations ont été faites et tout va très bien dans les laboratoires."

    monkeypatch.setattr(synthese, "modele_disponible", lambda: True)
    monkeypatch.setattr(synthese, "generer", generer_faux)
    texte, source = asyncio.run(synthese.rediger_synthese(INDICATEURS))
    assert (texte, source) == ("Synthèse par règles.", "regles")


def test_synthese_du_modele_acceptee(monkeypatch):
    async def generer_fidele(*_args, **_kwargs):
        return ("Du 2026-09-21 au 2026-09-27, 42 réservations ont totalisé 96.5 heures pour 17 utilisateurs. "
                "Le Microscope confocal est saturé à 81 %, avec un pic le mardi à 10h. Les demandes attendent "
                "30.2 heures en moyenne et 3 sont en attente depuis plus de 48 heures.")

    monkeypatch.setattr(synthese, "modele_disponible", lambda: True)
    monkeypatch.setattr(synthese, "generer", generer_fidele)
    texte, source = asyncio.run(synthese.rediger_synthese(INDICATEURS))
    assert source == "modele"


def test_modele_indisponible(monkeypatch):
    monkeypatch.setattr(synthese, "modele_disponible", lambda: False)
    assert asyncio.run(synthese.rediger_synthese(INDICATEURS))[1] == "regles"


def test_indicateurs_incomplets_repli_sur_les_regles(monkeypatch):
    async def generer_inutile(*_args, **_kwargs):
        raise AssertionError("Le modèle ne doit pas être appelé sans faits complets.")

    monkeypatch.setattr(synthese, "modele_disponible", lambda: True)
    monkeypatch.setattr(synthese, "generer", generer_inutile)
    texte, source = asyncio.run(synthese.rediger_synthese({"synthese_regles": "Synthèse par règles."}))
    assert (texte, source) == ("Synthèse par règles.", "regles")
