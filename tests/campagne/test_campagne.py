"""
Campagne de scénarios, SANS modèle de langage (règles seules, faux Django
de la campagne, date figée au lundi 12 octobre 2026). Chaque scénario est
un test : un échec indique un comportement incorrect de l'assistant.
Avec le modèle réel : scripts/campagne_assistant.py --mode qwen.
"""
import pytest

from .moteur import executer
from .scenarios import TOUS


@pytest.mark.parametrize("cas", TOUS, ids=[c.id for c in TOUS])
def test_scenario(cas):
    resultat = executer(cas)
    if resultat.statut == "bloque":
        pytest.skip(f"bloqué : {'; '.join(resultat.raisons)}")
    assert resultat.statut == "reussi", "\n".join(resultat.raisons + [f"→ {r}" for r in resultat.reponses])
