"""
Navigation : l'utilisateur veut faire quelque chose qui se fait dans
l'interface (ajouter un équipement, gérer les utilisateurs...). L'assistant
ne le fait pas à sa place : il propose un bouton vers la bonne page, et
seulement si le rôle de l'utilisateur permet de l'ouvrir.
"""
from app.assistant.contexte import Contexte
from app.assistant.formatage import formater_liste
from app.core.constantes import LABELS_ROLE_PLURIEL
from app.core.navigation import PAGES_PAR_CLE, Page, destination, pages_accessibles
from app.schemas.chat import ChatAction, ChatResponse

# Pages proposées quand l'assistant ne sait pas laquelle ouvrir.
PAGES_PRINCIPALES = ("mes_reservations", "reservation_formulaire", "equipements", "laboratoires",
                     "reservations_a_valider", "maintenances", "utilisateurs", "rapports")


def action_page(cle: str, role: str | None, label: str | None = None) -> ChatAction | None:
    """Bouton vers une page du catalogue, ou None si le rôle n'y a pas accès."""
    page = PAGES_PAR_CLE.get(cle)
    if page is None or not page.autorisee(role):
        return None
    return ChatAction(label=label or page.label, route=page.route_pour(role))


def actions_pages(role: str | None, *cles: str) -> list[ChatAction] | None:
    actions = [a for a in (action_page(c, role) for c in cles) if a]
    return actions or None


def message_reserve_a(page: Page) -> str:
    roles = formater_liste([LABELS_ROLE_PLURIEL.get(r, r) for r in sorted(page.roles or ())])
    return f"Je ne peux pas vous ouvrir « {page.label} » : cette page est réservée aux {roles}."


async def naviguer(ctx: Contexte) -> ChatResponse:
    role = ctx.user.role
    trouvee = destination(ctx.message_normalise, role)

    if trouvee is None:
        # Le modèle a compris « naviguer » sans page reconnaissable : on
        # propose les pages principales accessibles plutôt que d'inventer.
        accessibles = [p for p in pages_accessibles(role) if p.cle in PAGES_PRINCIPALES]
        return ChatResponse(
            type="navigation",
            reponse="Quelle page souhaitez-vous ouvrir ? Voici les principales rubriques auxquelles vous avez accès.",
            actions=[ChatAction(label=p.label, route=p.route_pour(role)) for p in accessibles] or None,
            intention=ctx.extraction.intention if ctx.extraction else None,
        )

    page = trouvee.page
    if trouvee.autorisee:
        return ChatResponse(
            type="navigation", reponse=page.message,
            actions=[ChatAction(label=page.label, route=page.route_pour(role))],
            intention="naviguer",
        )

    # Refus : le rôle (signé par Django dans le JWT) n'ouvre pas cette page.
    reponse = message_reserve_a(page)
    actions = None
    if trouvee.alternative:
        reponse += f" {trouvee.alternative.message}"
        actions = [ChatAction(label=trouvee.alternative.label, route=trouvee.alternative.route_pour(role))]
    return ChatResponse(type="denied", reponse=reponse, actions=actions, intention="naviguer")


async def creer_equipement(ctx: Contexte) -> ChatResponse:
    """Intention historique « creer_equipement » (comprise par le modèle) : même logique que la navigation."""
    page = PAGES_PAR_CLE["equipement_ajouter"]
    if page.autorisee(ctx.user.role):
        return ChatResponse(type="navigation", reponse=page.message, intention="creer_equipement",
                            actions=[ChatAction(label=page.label, route=page.route)])
    return ChatResponse(
        type="denied", intention="creer_equipement",
        reponse="Seuls les administrateurs et les techniciens peuvent ajouter un équipement. "
                "Vous pouvez en revanche consulter la liste des équipements.",
        actions=actions_pages(ctx.user.role, "equipements"),
    )
