"""
Évaluation avec le MODÈLE QWEN RÉEL (lent : ~2 min de chargement, ~10 s par
appel sur CPU ; ~3,5 Gio de mémoire).

    SENLAB_QWEN=1 HF_HUB_OFFLINE=1 venv/bin/python -m pytest tests/evaluation_qwen -v -s

1. La campagne complète (tests/campagne) rejouée avec le modèle chargé :
   les règles déterministes doivent rester justes quand le modèle intervient.
2. Le modèle SEUL, sur des formulations que les règles ne comprennent pas :
   JSON valide, intention attendue, aucune heure inventée.
"""
import asyncio
import json
import os
import time

import pytest

from tests.campagne.moteur import executer
from tests.campagne.scenarios import TOUS

pytestmark = pytest.mark.skipif(os.environ.get("SENLAB_QWEN") != "1", reason="évaluation Qwen : lancer avec SENLAB_QWEN=1")

# Formulations sans aucun mot-clé des règles, avec l'intention attendue
# (plusieurs acceptées quand la phrase l'autorise).
FORMULATIONS_INDIRECTES = [
    ("Je compte sur la centrifugeuse pour mon protocole demain", {"reserver", "consulter_disponibilite"}),
    ("Tu pourrais me caser sur le thermocycleur jeudi entre 10h et midi ?", {"reserver"}),
    ("J'ai un créneau de prévu sur le microscope ?", {"consulter_mes_reservations"}),
    ("Le spectro, quelqu'un l'a pris mercredi matin ?", {"consulter_disponibilite"}),
    ("Ça sert à quoi un thermocycleur ?", {"autre", "aide", "conseil_equipement"}),
    ("Retire-moi de la centrifugeuse de vendredi", {"annuler"}),
    ("Quand est-ce qu'on révise la centrifugeuse ?", {"maintenance"}),
    ("J'aimerais savoir combien de fois j'ai utilisé le PCR", {"statistiques", "bilan"}),
]


@pytest.fixture(scope="module")
def modele():
    from app.assistant.synthese import CONSIGNES
    from app.nlu import model
    from app.nlu.extractor import SYSTEM_PROMPT
    if not model.modele_disponible():
        model.charger_modele([SYSTEM_PROMPT, CONSIGNES])
    assert model.modele_disponible(), "modèle Qwen introuvable (HF_HUB_OFFLINE=1 exige le cache local)"
    return model


@pytest.mark.parametrize("cas", TOUS, ids=[c.id for c in TOUS])
def test_campagne_avec_qwen(modele, cas):
    resultat = executer(cas)
    if resultat.statut == "bloque":
        pytest.skip("; ".join(resultat.raisons))
    assert resultat.statut == "reussi", "\n".join(resultat.raisons + [str(r) for r in resultat.reponses])


@pytest.mark.parametrize("message,attendues", FORMULATIONS_INDIRECTES, ids=[m[:40] for m, _ in FORMULATIONS_INDIRECTES])
def test_modele_seul_json_valide_et_intention(modele, message, attendues):
    from app.nlu.extractor import SYSTEM_PROMPT, extraire_json
    debut = time.perf_counter()
    brut = asyncio.run(modele.generer([{"role": "system", "content": SYSTEM_PROMPT},
                                       {"role": "user", "content": f"Message : {message}"}], max_new_tokens=90))
    duree = time.perf_counter() - debut
    donnees = extraire_json(brut)  # lève une erreur si la sortie n'est pas un objet JSON
    print(f"\n{duree:5.1f} s  {message!r} -> {json.dumps(donnees, ensure_ascii=False)}")
    assert donnees.get("intention") in attendues, brut


def test_modele_n_invente_pas_d_heure(modele):
    """Garde-fou de fusion : une heure proposée par le modèle et absente du message est ignorée."""
    from app.nlu.extractor import extraire
    extraction = asyncio.run(extraire("Je compte sur la centrifugeuse pour mon protocole demain"))
    assert extraction.heure_debut is None and extraction.heure_fin is None
