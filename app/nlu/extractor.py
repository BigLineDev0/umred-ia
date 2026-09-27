"""
Compréhension d'un message : approche HYBRIDE modèle + règles.

- Le modèle de langage est bon pour ce qui demande de comprendre le sens :
  l'INTENTION (« je voudrais bloquer le PCR » = réserver) et le nom de
  l'ÉQUIPEMENT, même mal orthographié ou reformulé.
- Les règles sont fiables pour ce qui a un format précis : DATES et
  HEURES. Elles ne peuvent pas inventer une valeur absente du message.

On combine donc les deux, chacun sur ce qu'il fait le mieux, et le
modèle n'est jamais indispensable : s'il tombe, les règles suffisent.
"""
import json
import logging
import re

from app.nlu.extraction import Extraction
from app.nlu.intentions import Intention, affiner_intention, valider_intention
from app.nlu.model import ModeleIndisponible, generer
from app.nlu.normalisation import normaliser_date, normaliser_heure, valeur_ou_none
from app.nlu.regles import extraire_par_regles
from app.nlu.texte import normaliser_texte

logger = logging.getLogger(__name__)

# Prompt « few-shot » : pour un modèle de 1,5 milliard de paramètres, des
# exemples concrets sont bien plus efficaces que de longues consignes.
SYSTEM_PROMPT = f"""Tu es le module de compréhension de l'assistant d'un laboratoire universitaire.
Tu reçois un message d'utilisateur et tu réponds UNIQUEMENT par un objet JSON, sans aucun texte autour.

Intentions possibles :
- reserver : l'utilisateur veut réserver un équipement
- annuler : il veut annuler une de ses réservations
- consulter_mes_reservations : il veut voir ses réservations
- consulter_disponibilite : il demande si un équipement (ou lequel) est libre
- maintenance : il demande la prochaine maintenance d'un équipement
- statistiques : il demande un nombre, un total, une statistique
- creer_equipement : il veut ajouter un nouvel équipement au parc
- mon_nom : il demande qui il est, son nom
- identite : il demande qui tu es, ce que tu sais faire
- salutation : simple salutation, sans autre demande
- autre : tout le reste (question générale, remerciement, hors sujet...)

Règles :
- Recopie la date telle qu'elle est écrite (« demain », « lundi prochain », « 28/09 »). N'invente jamais de date ni d'heure.
- Heures au format HH:MM.
- Une information absente vaut null (le null JSON, sans guillemets).
- "equipement" contient seulement le nom de l'équipement (« microscope optique »), sans article.

Format : {{"intention": "...", "equipement": ..., "date": ..., "heure_debut": ..., "heure_fin": ...}}

Exemples :
Message : Réserve-moi le microscope demain de 14h à 16h
{{"intention": "reserver", "equipement": "microscope", "date": "demain", "heure_debut": "14:00", "heure_fin": "16:00"}}
Message : je voudrais bloquer la centrifugeuse vendredi à 9h
{{"intention": "reserver", "equipement": "centrifugeuse", "date": "vendredi", "heure_debut": "09:00", "heure_fin": null}}
Message : Est-ce que le spectrophotomètre est libre lundi ?
{{"intention": "consulter_disponibilite", "equipement": "spectrophotomètre", "date": "lundi", "heure_debut": null, "heure_fin": null}}
Message : Annule ma réservation du 28/09
{{"intention": "annuler", "equipement": null, "date": "28/09", "heure_debut": null, "heure_fin": null}}
Message : Combien de réservations ai-je faites ce mois-ci ?
{{"intention": "statistiques", "equipement": null, "date": null, "heure_debut": null, "heure_fin": null}}
Message : Comment faire une réservation ?
{{"intention": "autre", "equipement": null, "date": null, "heure_debut": null, "heure_fin": null}}
Message : Ajoute un équipement thermocycleur
{{"intention": "creer_equipement", "equipement": "thermocycleur", "date": null, "heure_debut": null, "heure_fin": null}}

Intentions autorisées : {", ".join(i.value for i in Intention)}."""


def extraire_json(texte: str) -> dict:
    """
    Le modèle entoure parfois son JSON de ```json ... ``` ou d'une phrase :
    on isole le premier objet {...}. Lève ValueError si rien d'exploitable.
    """
    texte = re.sub(r"```(?:json)?", "", texte).strip()
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut == -1 or fin <= debut:
        raise ValueError("Aucun objet JSON dans la réponse du modèle.")
    donnees = json.loads(texte[debut:fin + 1])
    if not isinstance(donnees, dict):
        raise ValueError("La réponse du modèle n'est pas un objet JSON.")
    return donnees


def _heure_presente_dans_message(heure: str | None, message_normalise: str) -> bool:
    """
    Garde-fou anti-hallucination : une heure proposée par le modèle n'est
    acceptée que si son nombre d'heures figure réellement dans le message.
    """
    if not heure:
        return False
    return re.search(rf"(?<!\d)0?{int(heure[:2])}(?!\d)", message_normalise) is not None


def fusionner(donnees_modele: dict, regles: Extraction, message: str) -> Extraction:
    """Assemble le résultat final à partir du modèle et des règles (voir docstring du module)."""
    message_normalise = normaliser_texte(message)
    intention = affiner_intention(valider_intention(donnees_modele.get("intention")), message_normalise)

    # Date : les règles d'abord. Sinon, celle du modèle, à condition que
    # l'expression recopiée figure bien dans le message (pas d'invention).
    date = regles.date
    date_modele = valeur_ou_none(donnees_modele.get("date"))
    if not date and date_modele and normaliser_texte(date_modele) in message_normalise:
        date = normaliser_date(date_modele)

    heure_debut, heure_fin = regles.heure_debut, regles.heure_fin
    if not heure_debut:
        candidate = normaliser_heure(donnees_modele.get("heure_debut"))
        heure_debut = candidate if _heure_presente_dans_message(candidate, message_normalise) else None
    if not heure_fin:
        candidate = normaliser_heure(donnees_modele.get("heure_fin"))
        heure_fin = candidate if _heure_presente_dans_message(candidate, message_normalise) else None

    return Extraction(
        intention=intention,
        equipement=valeur_ou_none(donnees_modele.get("equipement")),
        date=date,
        heure_debut=heure_debut,
        heure_fin=heure_fin,
        periode=regles.periode,
    )


def _regles_suffisent(regles: Extraction, reservation_en_cours: bool) -> bool:
    """
    Le modèle coûte cher (de quelques secondes à plusieurs minutes sur CPU) :
    on ne le sollicite que si les règles n'ont PAS compris le message.
    - un mot-clé explicite a été trouvé (« réserve », « annule », « mes
      réservations »...) : les règles sont fiables, et l'équipement sera
      retrouvé par recherche floue sur le message complet ;
    - l'utilisateur complète une réservation (« demain de 10h à 12h ») :
      dates et heures sont justement le point fort des règles.
    """
    if regles.intention != Intention.AUTRE:
        return True
    return reservation_en_cours and regles.a_des_informations_de_reservation


async def extraire(message: str, reservation_en_cours: bool = False) -> Extraction:
    regles = extraire_par_regles(message)
    if _regles_suffisent(regles, reservation_en_cours):
        return regles
    try:
        reponse = await generer(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Message : {message}"}],
            max_new_tokens=120,
        )
        return fusionner(extraire_json(reponse), regles, message)
    except ModeleIndisponible as exc:
        logger.info("Extraction par règles seules : %s", exc)
    except Exception:
        logger.exception("Réponse du modèle inexploitable, repli sur les règles")
    return regles
