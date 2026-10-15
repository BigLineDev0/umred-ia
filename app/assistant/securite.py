"""
Demandes que l'assistant refuse explicitement, sans jamais appeler une
action Django : statuer sur une réservation, consulter les données d'autres
utilisateurs, contourner les règles, révéler un secret, annoncer un succès
ou un statut que la plateforme n'a pas confirmé.

Ces refus ne sont pas la protection : Django applique de toute façon les
permissions avec le JWT de l'utilisateur, et l'assistant n'a aucun outil de
validation. Ils évitent une réponse hors sujet (avant : « Quel équipement
souhaitez-vous réserver ? ») et rendent le refus compréhensible.
"""
from app.assistant.annulation import MESSAGE_AUTRUI
from app.assistant.contexte import Contexte
from app.assistant.navigation import actions_pages
from app.core.constantes import ROLES_SUPERVISEURS
from app.nlu.texte import contient_un
from app.schemas.chat import ChatResponse

MOTS_VALIDATION = ("valide ma", "valide la", "valide sa", "valider ma", "valider la reservation", "approuve ma",
                   "approuve la", "approuver ma", "approuver la")
MOTS_AUTRUI = ("d'un autre utilisateur", "d'autres utilisateurs", "des autres utilisateurs", "reservations privees",
               "de tout le monde")
MOTS_SECRET = ("token", "jwt", "cle secrete", "cle api", "secret_key")
MOTS_FAUX_STATUT = ("dis-moi que ", "dis moi que ", "dis-moi qu'", "dis moi qu'", "pretends que", "affirme que")
MOTS_FAUX_SUCCES = ("considere quand meme", "considere la reservation comme", "fais comme si")


async def refuser(ctx: Contexte) -> ChatResponse:
    m, role = ctx.message_normalise, ctx.user.role

    if contient_un(m, MOTS_SECRET):
        return ChatResponse(type="denied", intention="securite",
                            reponse="Je n'affiche jamais de jeton d'authentification, de clé ni de mot de passe. "
                                    "Votre session est gérée automatiquement par la plateforme.")
    if contient_un(m, MOTS_FAUX_STATUT):
        # On donne l'état RÉEL, lu dans Django, au lieu de celui demandé.
        from app.assistant.suivi import statut_demandes
        reel = await statut_demandes(ctx)
        reel.reponse = ("Je ne peux annoncer que l'état réellement enregistré par la plateforme. " + reel.reponse)
        return reel
    if contient_un(m, MOTS_FAUX_SUCCES):
        return ChatResponse(type="denied", intention="securite", actions=actions_pages(role, "mes_reservations"),
                            reponse="Je ne peux pas considérer comme créée une réservation que la plateforme n'a pas "
                                    "confirmée. Seule la réponse du serveur fait foi : vérifiez « Mes réservations » "
                                    "et, si elle n'y figure pas, refaites la demande.")
    if contient_un(m, MOTS_VALIDATION):
        if role in ROLES_SUPERVISEURS:
            return ChatResponse(type="denied", intention="securite",
                                actions=actions_pages(role, "reservations_a_valider"),
                                reponse="Je ne valide ni ne refuse de réservation à votre place : chaque décision se "
                                        "prend depuis la page « Réservations à valider », où la plateforme vérifie "
                                        "vos droits sur la demande.")
        return ChatResponse(type="denied", intention="securite", actions=actions_pages(role, "mes_reservations"),
                            reponse="Je ne peux pas valider de réservation, et un étudiant ne peut statuer sur aucune "
                                    "demande, même la sienne : c'est votre encadrant, un technicien ou un "
                                    "administrateur qui décide. Vous pouvez suivre l'état de vos demandes.")
    if contient_un(m, MOTS_AUTRUI) or "annul" in m:
        return ChatResponse(type="denied", intention="securite", reponse=MESSAGE_AUTRUI)
    # Contournement des règles (« ignore tes règles », « accès à toutes les données »).
    return ChatResponse(type="denied", intention="securite",
                        reponse="Je ne peux pas contourner les règles de la plateforme : je n'accède qu'aux données "
                                "que votre compte est autorisé à consulter, et toute action reste vérifiée par la "
                                "plateforme. Que souhaitez-vous faire ?")
