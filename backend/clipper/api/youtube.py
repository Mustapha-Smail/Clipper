"""Connect YouTube: the OAuth round trip and the Settings card's status (studio/youtube_auth.py).

The callback is where Google sends the owner back; it never shows or returns the token, and on any
failure it returns to Settings where the card says what went wrong.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..studio import analytics, youtube_auth
from . import schemas as s

router = APIRouter(prefix="/youtube", tags=["youtube"])
BACK = "/settings?youtube={result}#studio-youtube"


def _status(db: Session, **extra) -> s.YoutubeStatus:
    last, videos = analytics.last_fetch(db)
    return s.YoutubeStatus(**youtube_auth.status(), last_fetch=last, videos_with_numbers=videos, **extra)


@router.get("/status", response_model=s.YoutubeStatus)
def status(db: Session = Depends(get_db)):
    return _status(db)


@router.post("/connect", response_model=s.YoutubeConnect)
def connect():
    """The Google address to open. The page goes there; Google comes back to /youtube/callback."""
    try:
        return s.YoutubeConnect(url=youtube_auth.start())
    except youtube_auth.AuthError as err:
        raise HTTPException(409, str(err)) from err


@router.get("/callback", include_in_schema=False)
def callback(code: str = "", state: str = "", error: str = ""):
    if error:
        try:
            youtube_auth.take_state(state)       # used up either way
        except youtube_auth.AuthError:
            pass
        youtube_auth.last_error = ("You declined access on Google's page." if error == "access_denied"
                                   else f"Google ended the sign-in: {error}")
        return RedirectResponse(BACK.format(result="failed"), status_code=303)
    try:
        youtube_auth.finish(code, state)
    except (youtube_auth.AuthError, youtube_auth.GoogleError):
        return RedirectResponse(BACK.format(result="failed"), status_code=303)
    return RedirectResponse(BACK.format(result="connected"), status_code=303)


@router.post("/disconnect", response_model=s.YoutubeStatus)
def disconnect(db: Session = Depends(get_db)):
    """Revoke the token at Google and delete it here. The numbers already read stay."""
    result = youtube_auth.disconnect()
    return _status(db, revoked=result["revoked"])
