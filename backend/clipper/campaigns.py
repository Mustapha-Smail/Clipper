"""Campaign scouting: read a marketplace listing, rank it, turn it into a brief.

A listing reaches the app as plain page text (pasted by the user, or sent by a
scout that browses the marketplace). Claude turns the text into structured
terms. Ranking is plain arithmetic on those terms, so every score can be
explained line by line.
"""

from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .db.models import Campaign, utcnow
from .settings import Settings

MAX_TEXT = 40_000


Platform = Literal["tiktok", "instagram", "youtube", "x", "facebook", "snapchat", "other"]


class PlatformRate(BaseModel):
    platform: Platform
    rate_per_1k: float
    min_payout: float | None
    max_payout: float | None


class Terms(BaseModel):
    title: str
    brand: str = Field(description="Who runs the campaign; empty if not stated")
    category: str = Field(description="The listing's own category label, e.g. Entertainment; empty if none")
    summary: str = Field(description="One or two sentences: what gets clipped and what the creator must do")
    kind: Literal["clipping", "ugc", "other"]
    payout_model: Literal["cpm", "per_post", "retainer", "pool", "other"]
    listing_status: Literal["open", "paused", "ended", "unknown"]
    currency: str = Field(description="ISO code such as USD or EUR")
    rate_per_1k: float | None = Field(description="Payout per 1,000 views; null unless the listing states it")
    min_payout: float | None = Field(description="Smallest payout per clip, if stated")
    max_payout: float | None = Field(description="Largest payout per clip, if stated")
    budget_total: float | None
    budget_left: float | None = Field(description="Budget still unspent, if stated")
    platforms: list[Platform]
    platform_rates: list[PlatformRate] = Field(
        description="One entry per platform when the listing gives per-platform rates; empty otherwise")
    source_material: Literal["provided", "own", "unknown"] = Field(
        description="provided: the campaign supplies videos or clips to cut from; own: creators film themselves")
    tier1_min_share: int | None = Field(
        description="Minimum percent of viewers required in the US, UK, Canada, Australia or similar, if stated")
    geo_requirement: str = Field(
        description="Any country, language or audience-location rule, quoted briefly; else empty")
    restricted_category: Literal["none", "gambling", "crypto", "trading", "other"] = Field(
        description="Whether the promoted product is gambling or betting, crypto, trading or investing, "
                    "or another regulated product (alcohol, tobacco, medical)")
    requirements: str = Field(
        description="Every rule a submission must follow, one per line, in the listing's own words where "
                    "possible: length, watermarks, hashtags, banned content, account rules")
    theme_fit: int = Field(description="0 to 10: how well the campaign's content suits the creator's theme")
    theme_reason: str = Field(description="One sentence explaining the fit score")


SYSTEM = """\
You read one campaign listing from a creator marketplace (clipping or UGC campaigns that \
pay creators for posting short videos) and extract its terms for a creator deciding whether \
to join.

The text is copied from a web page, so it contains navigation, buttons, leaderboards and \
repeated blocks. Use only what describes this campaign. The page may be in any language \
and may format numbers the French way ("$2,00" is two dollars, "1 000" is one thousand).

Report only what the listing states. A number that is not on the page is null, never a \
guess: the creator will make money decisions from these fields. When the listing gives \
rates per platform, list each one in platform_rates and put the lowest-paying platform's \
figures in rate_per_1k, min_payout and max_payout. Rules that the page says are hidden \
until you join are unknown: say so in the requirements instead of inventing them. If the \
budget shows as spent of total (for example "$1000 / $1000" with "$0 left"), compute what \
is left.

For the fit score, compare what would actually be clipped with the creator's theme given \
below. A different niche scores low even when the campaign pays well; that is a separate \
question the app weighs for itself."""


def extract(text: str, theme: str) -> Terms:
    prompt = (f"<creator_theme>{theme}</creator_theme>\n\n"
              f"<listing>\n{text[:MAX_TEXT]}\n</listing>")
    return llm.ask("campaign", SYSTEM, prompt, Terms, effort="medium")


def apply_terms(campaign: Campaign, terms: Terms, raw_text: str) -> None:
    """Copy extracted terms onto a row. The user's decision and notes are left alone."""
    for field, value in terms.model_dump().items():
        setattr(campaign, field, value)
    campaign.raw_text = raw_text[:MAX_TEXT]
    campaign.last_seen = utcnow()


def source_of(url: str | None) -> str:
    if not url:
        return "pasted"
    host = url.split("//")[-1].split("/")[0].lower().removeprefix("www.")
    return host.split(".")[0] if host else "pasted"


# --- ranking -------------------------------------------------------------------

def best_rate(c: Campaign, settings: Settings) -> dict:
    """The rate that applies to this creator: the best-paying platform they post on.

    Falls back to the listing's headline figures when it gives no per-platform
    rates, or pays on none of the creator's platforms.
    """
    mine = [r for r in (c.platform_rates or []) if r["platform"] in settings.platforms]
    if mine:
        return max(mine, key=lambda r: r["rate_per_1k"])
    return {"platform": None, "rate_per_1k": c.rate_per_1k, "min_payout": c.min_payout, "max_payout": c.max_payout}


def assess(c: Campaign, settings: Settings) -> dict:
    """Net numbers, a 0-100 score, the lines that explain it, and warning flags."""
    fee = (c.fee_pct or 0) / 100
    rate = best_rate(c, settings)
    gross_rate, max_payout = rate["rate_per_1k"], rate["max_payout"]
    net_rate = round(gross_rate * (1 - fee), 2) if gross_rate is not None else None
    # What one clip with 100,000 views pays after the fee, respecting the per-clip cap.
    payout_100k = None
    if gross_rate is not None:
        gross = gross_rate * 100
        if max_payout is not None:
            gross = min(gross, max_payout)
        payout_100k = round(gross * (1 - fee), 2)

    parts: list[tuple[str, float]] = []
    flags: list[str] = []

    if net_rate is not None:
        where = f" on {rate['platform']}" if rate["platform"] else ""
        parts.append((f"Net rate {c.currency} {net_rate:.2f} per 1,000 views{where}", min(net_rate, 6) / 6 * 50))
    else:
        parts.append(("No per-view rate stated", 15.0))
        flags.append("Not paid per view, or the rate is not stated: read the payout terms")

    if c.theme_fit is not None:
        parts.append((f"Theme fit {c.theme_fit}/10", c.theme_fit / 10 * 25))

    if c.budget_left is None:
        parts.append(("Budget left not stated", 6.0))
    else:
        parts.append((f"{c.currency} {c.budget_left:,.0f} of budget left", min(c.budget_left, 3000) / 3000 * 15))
        if c.budget_left <= 0:
            flags.append("The budget is spent")
        elif max_payout and c.budget_left < max_payout:
            flags.append("Less budget left than one clip's maximum payout")

    if max_payout is not None:
        parts.append((f"Up to {c.currency} {max_payout:,.0f} per clip", min(max_payout, 500) / 500 * 5))
    if c.platforms and not set(c.platforms) & set(settings.platforms):
        flags.append("Pays on " + ", ".join(c.platforms) + ", none of which is in your platforms (Settings)")
    if c.source_material == "provided":
        parts.append(("Source videos are provided", 5.0))

    score = sum(points for _, points in parts)

    def cut(label: str, factor: float) -> None:
        """A penalty that scales the score; recorded so the breakdown still adds up."""
        nonlocal score
        parts.append((label, score * factor - score))
        score *= factor

    def minus(label: str, points: float) -> None:
        nonlocal score
        parts.append((label, -points))
        score -= points

    joinable = c.listing_status not in ("paused", "ended") and (c.budget_left is None or c.budget_left > 0)
    if c.listing_status in ("paused", "ended"):
        flags.append(f"The listing is {c.listing_status}")
    if not joinable:
        cut("Cannot be joined right now", 0.2)

    if c.tier1_min_share:
        share = settings.audience_tier1_share
        if share is None:
            minus("Location rule you may not meet", 12)
            flags.append(f"Needs {c.tier1_min_share}% of viewers in the US, UK, Canada or Australia; "
                         "your audience split is not set in Settings")
        elif share < c.tier1_min_share:
            cut("Location rule you do not meet", 0.2)
            flags.append(f"Needs {c.tier1_min_share}% of viewers in the US, UK, Canada or Australia; yours is {share}%")
    elif c.geo_requirement:
        flags.append(f"Location rule: {c.geo_requirement}")

    if c.restricted_category != "none":
        label = {"gambling": "Gambling or betting", "crypto": "Crypto", "trading": "Trading or investing",
                 "other": "Regulated"}[c.restricted_category]
        minus(f"{label} product", 15)
        flags.append(f"{label} product: French influencer rules may restrict promoting it. Check before joining")
    if c.kind == "ugc":
        flags.append("UGC campaign: you film original content instead of clipping")

    return {
        "net_rate": net_rate,
        "rate_platform": rate["platform"],
        "best_max_payout": max_payout,
        "payout_100k": payout_100k,
        "score": max(0, round(score)),
        "joinable": joinable,
        "breakdown": [{"label": label, "points": round(points, 1)} for label, points in parts],
        "flags": flags,
    }


def brief_for(c: Campaign) -> str:
    """The text a video gets as its campaign brief: used to choose moments and to check clips."""
    lines = [f"Campaign: {c.title}" + (f" (by {c.brand})" if c.brand else "")]
    if c.platforms:
        lines.append("Allowed platforms: " + ", ".join(c.platforms))
    if c.geo_requirement:
        lines.append(f"Audience location: {c.geo_requirement}")
    if c.requirements.strip():
        lines.append("Requirements:")
        lines.extend(f"- {line.strip().lstrip('-• ').strip()}" for line in c.requirements.splitlines() if line.strip())
    return "\n".join(lines)
