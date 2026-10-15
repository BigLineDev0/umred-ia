from dataclasses import dataclass

from app.nlu.intentions import Intention


@dataclass
class Extraction:
    """
    Ce que l'assistant a compris d'un message, sous une forme normalisée :
    date au format ISO (YYYY-MM-DD), heures au format HH:MM, None quand
    l'information n'est pas dans le message. Le reste du code ne manipule
    jamais le texte brut produit par le modèle.
    """

    intention: Intention = Intention.AUTRE
    # Texte désignant l'équipement (« le microscope »). None -> les
    # traitements chercheront directement dans le message complet.
    equipement: str | None = None
    date: str | None = None
    heure_debut: str | None = None
    heure_fin: str | None = None
    # "jour", "semaine" (7 prochains jours), "semaine_prochaine",
    # "semaine_derniere" ou "debut_semaine" (lundi -> mercredi).
    periode: str = "jour"
    # Moment de la journée sans heure précise : "matin", "apres_midi",
    # "soir" ou "journee". Converti en heures par le service, à partir des
    # horaires réels de l'établissement (lus dans Django).
    moment: str | None = None
    # Autre lecture possible de la date (« vendredi prochain » dit un
    # jeudi) : l'assistant la signale au lieu de trancher en silence.
    date_alternative: str | None = None
    # Date citée mais impossible (« le 31 février ») : signalée, jamais devinée.
    date_invalide: str | None = None
    # Durée demandée (« pendant deux heures », « pour 2 heures »), en minutes.
    duree_minutes: int | None = None

    @property
    def a_des_informations_de_reservation(self) -> bool:
        return any([self.equipement, self.date, self.heure_debut, self.heure_fin, self.moment])
