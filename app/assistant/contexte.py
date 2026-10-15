import re
from dataclasses import dataclass
from typing import Any

from app.core.security import Utilisateur
from app.nlu.extraction import Extraction
from app.nlu.texte import normaliser_texte
from app.schemas.chat import ChatOption

REPONSES_OUI = {"oui", "yes", "ok", "okay", "d'accord", "daccord", "confirme", "confirmer", "je confirme", "valider", "vas-y"}
REPONSES_NON = {"non", "no", "annule", "annuler", "stop", "laisse tomber", "pas maintenant", "non merci"}
# Premiers mots d'une réponse et mots qui peuvent l'accompagner sans en
# changer le sens (« oui merci », « oui je confirme », « ok c'est bon »).
DEBUTS_OUI = {"oui", "ouais", "yes", "ok", "okay", "d'accord", "daccord", "confirme", "confirmer", "valider", "valide",
              "vas-y", "parfait", "go", "absolument", "exactement"}
DEBUTS_NON = {"non", "no", "nan", "stop", "annule", "annuler"}
MOTS_NEUTRES = {"merci", "beaucoup", "c'est", "cest", "bon", "parfait", "tres", "bien", "je", "allez", "svp", "stp",
                "s'il", "te", "vous", "plait", "d'accord", "daccord", "tout", "a", "fait", "ca", "marche", "absolument",
                "exactement", "c", "est", "ce", "sera", "finalement"}
# « Oui, confirmer » / « Non, annuler » (libellés des boutons, retapés).
NEUTRES_OUI = MOTS_NEUTRES | {"oui", "ok", "confirme", "confirmer", "valide", "valider", "vas-y", "go"}
NEUTRES_NON = MOTS_NEUTRES | {"non", "annule", "annuler", "pas", "maintenant", "laisse", "tomber"}


@dataclass
class Contexte:
    """
    Tout ce dont un traitement a besoin, regroupé dans un seul objet : chaque
    fonction du dialogue a ainsi la même signature `(ctx) -> ChatResponse`,
    ce qui permet de les ranger dans de simples tables de routage.
    """

    user: Utilisateur
    session: dict[str, Any]
    message: str
    message_normalise: str
    extraction: Extraction | None = None
    # Le message relance la question précédente (« et jeudi ? ») : les
    # traitements peuvent reprendre l'équipement mémorisé dans la session.
    relance: bool = False

    @classmethod
    def creer(cls, user: Utilisateur, session: dict[str, Any], message: str) -> "Contexte":
        return cls(user=user, session=session, message=message, message_normalise=normaliser_texte(message))

    @property
    def token(self) -> str:
        return self.user.token

    @property
    def contexte_etape(self) -> dict[str, Any]:
        return self.session["contexte"]

    @property
    def equipement_du_contexte(self) -> dict | None:
        """Équipement dont on parlait (validé par Django), si le message est une relance."""
        return self.session["memoire"].get("equipement") if self.relance else None


def _sans_ponctuation(texte: str) -> str:
    return re.sub(r"[^\w' -]", "", texte).strip()


def _reponse_simple(message_normalise: str, debuts: set[str], exacts: set[str], neutres: set[str]) -> bool:
    texte = _sans_ponctuation(message_normalise)
    if texte in exacts:
        return True
    mots = texte.split()
    return bool(mots) and mots[0] in debuts and all(m in neutres for m in mots[1:])


def est_oui(message_normalise: str) -> bool:
    """
    Confirmation EXPLICITE et sans réserve : « oui », « oui je confirme »,
    « ok merci ». « Oui mais plutôt jeudi » n'en est PAS une : avant, tout
    message commençant par « oui » confirmait la réservation initiale
    (vendredi), à l'opposé de ce que demandait l'utilisateur.
    """
    return _reponse_simple(message_normalise, DEBUTS_OUI, REPONSES_OUI, NEUTRES_OUI)


def est_non(message_normalise: str) -> bool:
    return _reponse_simple(message_normalise, DEBUTS_NON, REPONSES_NON, NEUTRES_NON)


def commence_par_oui_ou_non(message_normalise: str) -> bool:
    """« Oui mais... », « non, plutôt... » : une réponse nuancée, à clarifier."""
    mots = _sans_ponctuation(message_normalise).split()
    return bool(mots) and (mots[0] in DEBUTS_OUI or mots[0] in DEBUTS_NON)


# Relance de la question précédente : « et jeudi après-midi ? », « et le
# lendemain ? », « plutôt vendredi ». L'intention et l'équipement viennent
# alors du contexte de la conversation.
_RELANCE = re.compile(r"^(?:et|plutot|sinon|ou alors|ou bien|et si|et pour|et le|et la|et l')\b")
_POLITESSE = re.compile(r"\b(?:merci|au revoir|a demain|a bientot|bonne (?:journee|soiree))\b")


def est_relance(message_normalise: str) -> bool:
    texte = _sans_ponctuation(message_normalise)
    if _POLITESSE.search(texte):
        return False
    return bool(_RELANCE.match(texte)) or len(texte.split()) <= 3


def options_confirmation() -> list[ChatOption]:
    return [
        ChatOption(label="Oui, confirmer", value="oui"),
        ChatOption(label="Non, annuler", value="non"),
    ]


def lire_choix(message_normalise: str, prefixe: str) -> int | None:
    """
    Lit la valeur technique d'un bouton (« equip_12 » -> 12). Renvoie None si
    le message n'est pas un clic sur ce type de bouton.
    """
    correspondance = re.fullmatch(rf"{prefixe}_(\d+)", message_normalise.strip())
    return int(correspondance.group(1)) if correspondance else None


ORDINAUX = {"premier": 1, "premiere": 1, "1er": 1, "deuxieme": 2, "second": 2, "seconde": 2, "2e": 2,
            "troisieme": 3, "3e": 3, "quatrieme": 4, "4e": 4, "cinquieme": 5, "5e": 5}
_ORDINAL = re.compile(r"^(?:le |la |l')?(?:choix |creneau |option |numero )?(\w+)(?: choix| creneau| option)?$")


def lire_numero(message_normalise: str, nombre_choix: int) -> int | None:
    """
    Permet aussi de répondre au clavier par « 1 », « 2 »... ou « le premier »,
    « le deuxième », « le dernier » (index 0-based renvoyé).
    """
    texte = _sans_ponctuation(message_normalise)
    correspondance = _ORDINAL.match(texte)
    mot = correspondance.group(1) if correspondance else texte
    if mot == "dernier" or mot == "derniere":
        return nombre_choix - 1 if nombre_choix else None
    numero = int(mot) if mot.isdigit() else ORDINAUX.get(mot)
    if numero is not None and 1 <= numero <= nombre_choix:
        return numero - 1
    return None
