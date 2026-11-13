"""Campaigns: add a listing, see them ranked, record what you decided."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import campaigns as campaigns_mod
from .. import scout
from .. import settings as settings_mod
from ..db import get_db
from ..db.models import Campaign, Video
from . import schemas as s

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
log = logging.getLogger("clipper.campaigns")


def get_campaign(campaign_id: int, db: Session = Depends(get_db)) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    return campaign


def campaign_out(campaign: Campaign, settings, video_count: int = 0) -> s.CampaignOut:
    return s.CampaignOut(
        **{field: getattr(campaign, field) for field in s.CampaignOut.model_fields
           if field not in s.COMPUTED_CAMPAIGN_FIELDS},
        **campaigns_mod.assess(campaign, settings),
        brief=campaigns_mod.brief_for(campaign),
        video_count=video_count,
    )


@router.get("", response_model=list[s.CampaignOut])
def list_campaigns(db: Session = Depends(get_db)):
    """All campaigns, best first."""
    settings = settings_mod.load()
    counts = dict(db.execute(select(Video.campaign_id, func.count()).where(Video.campaign_id.is_not(None))
                             .group_by(Video.campaign_id)).all())
    items = [campaign_out(c, settings, counts.get(c.id, 0)) for c in db.scalars(select(Campaign))]
    return sorted(items, key=lambda c: (-c.score, c.title.lower()))


@router.post("", response_model=s.CampaignOut)
def ingest_campaign(body: s.CampaignIngest, db: Session = Depends(get_db)):
    """Read a listing's page text with Claude and store its terms.

    Sending a URL that is already known refreshes that campaign and keeps your
    decision and notes.
    """
    settings = settings_mod.load()
    try:
        campaign, created = campaigns_mod.ingest(db, body.url, body.text, settings)
    except RuntimeError as err:
        raise HTTPException(502, str(err)) from err
    log.info("campaign read", extra={"campaign_id": campaign.id, "is_new": created, "campaign": campaign.title})
    return campaign_out(campaign, settings)


@router.post("/scout", response_model=s.JobOut, status_code=202)
def start_scout(db: Session = Depends(get_db)):
    """Queue the scout job: it reads public listings and adds them here (see scout.py)."""
    if scout.is_active(db):
        raise HTTPException(409, "The scout is already looking for campaigns")
    return scout.queue(db)


@router.patch("/{campaign_id}", response_model=s.CampaignOut)
def update_campaign(body: s.CampaignPatch, campaign: Campaign = Depends(get_campaign),
                    db: Session = Depends(get_db)):
    for field in body.model_fields_set:
        setattr(campaign, field, getattr(body, field))
    if {"rate_per_1k", "max_payout", "min_payout"} & body.model_fields_set:
        campaign.platform_rates = []   # a hand correction replaces whatever was read per platform
    db.flush()
    count = db.scalar(select(func.count()).where(Video.campaign_id == campaign.id)) or 0
    return campaign_out(campaign, settings_mod.load(), count)


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign: Campaign = Depends(get_campaign), db: Session = Depends(get_db)):
    """Videos made for it are kept; they just lose the link."""
    db.delete(campaign)
