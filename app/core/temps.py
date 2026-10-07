"""
Heure de référence du service : celle de l'établissement (Africa/Dakar,
identique au TIME_ZONE de Django), et non celle du serveur qui héberge le
service (souvent UTC). « Demain » doit désigner le même jour pour
l'utilisateur, pour FastAPI et pour Django.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings


def _fuseau():
    try:
        return ZoneInfo(settings.time_zone)
    except ZoneInfoNotFoundError:
        # Image Docker « slim » sans base des fuseaux : Dakar est à UTC+0
        # toute l'année (pas d'heure d'été), le repli est donc exact.
        return timezone.utc


FUSEAU = _fuseau()


def maintenant() -> datetime:
    """Date et heure locales de l'établissement, sans fuseau attaché (comme le reste du code)."""
    return datetime.now(FUSEAU).replace(tzinfo=None)


def aujourd_hui() -> date:
    return maintenant().date()
