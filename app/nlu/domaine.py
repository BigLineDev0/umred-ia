MOTS_CLES_DOMAINE = [
    "équipement", "equipement", "laboratoire", "réservation", "reservation",
    "maintenance", "panne", "disponib", "planifi", "créneau", "creneau", "UMRED", 
    "Chercheur", "Etudiant"
]


def concerne_le_labo(message: str) -> bool:
    message_lower = message.lower()
    return any(mot in message_lower for mot in MOTS_CLES_DOMAINE)