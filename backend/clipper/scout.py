"""The campaign scout: reads public campaign listings and adds them to Campaigns.

A worker job (`scout`), queued from the Campaigns page or on a schedule
(Settings, `scout_every_hours`). It reads one marketplace, Content Rewards, the
way a visitor would: public pages over plain HTTP, with no browser, login or
account. It follows the site's robots.txt and reads at most `scout_max_pages`
listings per run, one at a time with a pause between them. It never joins
or submits anything.

What it reads, in this order:
  1. campaigns you marked interested or joined, to refresh budget and status;
  2. new listings Claude picks as fitting your theme, judged from the cards on
     the discover page (title, brand, budget, rate);
  3. the best-paying other new listings, for comparison.
Each page's text then goes through the same steps as the "Add a campaign" form:
Claude extracts the terms (`campaigns.extract`) and `assess` ranks them.
Campaigns you skipped are never read again.

The AI never fails the job. Without it (no credit, no key, an outage) the run
chooses by rate alone, stops before reading pages it could not use, and says
so in its log and its final message; those listings are read by the next run.
"""

import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import campaigns, jobs, llm, prompts
from .db import session_scope
from .db.models import Campaign, Job, as_utc, utcnow
from .jobs import Reporter
from .settings import Settings

DISCOVER_URL = "https://contentrewards.com/c/discover/clipping"
HOSTS = {"contentrewards.com"}
USER_AGENT = "ClipperScout/1.0 (reads public campaign listings for one creator)"
PAUSE_SECONDS = 3.0           # between two pages of the site
MAX_BYTES = 4_000_000
LISTING_CHARS = 6000          # text kept from one listing (the terms are near the top)
COMPARE = 3                   # best-paying new listings added for comparison
MAX_CARDS = 120               # cards shown to Claude when it picks
REFRESH = ("interested", "joined")


class ScoutError(RuntimeError):
    """The scout cannot run; the message says why."""


@dataclass
class Card:
    """One campaign as the discover page shows it."""
    url: str
    text: str

    @property
    def rate(self) -> float | None:
        """The per-1,000-views rate printed on the card, if any ("$0.75 /1K")."""
        found = re.search(r"\$\s?([\d.,]+)\s*/\s*1K", self.text)
        return float(found.group(1).replace(",", "")) if found else None


# --- reading pages ------------------------------------------------------------------

class _Page(HTMLParser):
    """Visible text (from the first heading on) and the campaign links of one page."""

    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.lines: list[str] = []
        self.heading_at: int | None = None
        self.links: dict[str, str] = {}
        self._skip = 0
        self._link: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "h1" and self.heading_at is None:
            self.heading_at = len(self.lines)
        elif tag == "a":
            attrs = dict(attrs)
            href = (attrs.get("href") or "").split("?")[0].split("#")[0]
            if "/c/campaigns/" in href:
                label = (attrs.get("aria-label") or "").removeprefix("Preview ").strip()
                self._link = (urljoin(self.base, href), [label] if label else [])

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._link:
            url, parts = self._link
            text = re.sub(r"\s+", " ", " ".join(parts)).strip()
            if len(text) > len(self.links.get(url, "")):
                self.links[url] = text
            self._link = None

    def handle_data(self, data):
        if self._skip:
            return
        text = data.strip()
        if text:
            self.lines.append(text)
            if self._link and text not in self._link[1]:
                self._link[1].append(text)


def parse(html: str, base: str) -> _Page:
    page = _Page(base)
    page.feed(html)
    page.close()
    return page


def cards(html: str, base: str = DISCOVER_URL) -> list[Card]:
    """The campaign cards of a discover page, in the page's order."""
    return [Card(url, text) for url, text in parse(html, base).links.items() if allowed_host(url)]


def listing_text(html: str, base: str) -> str:
    """The text of one listing: from its title down to the leaderboard."""
    page = parse(html, base)
    text = "\n".join(page.lines[page.heading_at or 0:])
    for end in ("Top clippers", "Meilleurs clippeurs"):
        text = text.split(end)[0]
    return text.strip()[:LISTING_CHARS]


# --- reading terms without AI ---------------------------------------------------------

PLATFORMS = {"tiktok": "tiktok", "instagram": "instagram", "youtube": "youtube", "x": "x", "twitter": "x",
             "facebook": "facebook", "snapchat": "snapchat"}
STATUSES = {"accepting clips": "open", "paused": "paused", "not accepting clips": "paused",
            "ended": "ended", "campaign ended": "ended", "completed": "ended", "closed": "ended"}
RULE_HEADINGS = ("Brief", "Content requirements", "Creator requirements")
# What a page reading may update on a campaign Claude already read: the figures and the status.
# Its judgement (theme fit, rules, location, restricted product, summary) is kept.
LAYOUT_FIELDS = ("listing_status", "payout_model", "rate_per_1k", "min_payout", "max_payout",
                 "budget_total", "budget_left", "platforms", "platform_rates")
NOT_RULES = re.compile(r"^(Visible once you join|Join .*|Reference materials|\d+ (rules?|requirements?|files?))$")
# Words that suggest a regulated product. Matching one only raises the same flag Claude would,
# with its penalty, so the listing is checked before joining.
RESTRICTED = [
    ("gambling", re.compile(r"\b(casino|sportsbook|betting|gambl\w*|poker|slots)\b", re.I)),
    ("crypto", re.compile(r"\b(crypto\w*|bitcoin|ethereum|memecoin|nft)\b", re.I)),
    ("trading", re.compile(r"\b(trader?s?|trading|forex|prop firm|funded accounts?|stocks)\b", re.I)),
]


def money(text: str) -> float | None:
    """"$1,825", "$9,000.00", "$12.5K" -> a number; None when the text is not an amount."""
    found = re.search(r"([\d][\d,]*(?:\.\d+)?)\s*([KkMm]?)", text.replace("⁦", "").replace("⁩", ""))
    if not found:
        return None
    value = float(found.group(1).replace(",", ""))
    return value * {"k": 1e3, "m": 1e6}.get(found.group(2).lower(), 1)


def page_terms(html: str, url: str) -> dict | None:
    """The terms of a Content Rewards listing, read from its layout instead of by Claude.

    Used when the AI is unavailable. Only what the page states in its fixed
    blocks is taken: title, brand, status, category, the rate block of each
    platform, the budget and any rules shown before joining. Theme fit,
    location rules and anything in free text are left unknown (theme_fit None),
    and the ranking says so. Returns None when the layout is not recognised.
    """
    page = parse(html, url)
    if page.heading_at is None:
        return None
    lines = page.lines[page.heading_at:]
    for end in ("Top clippers", "Meilleurs clippeurs"):
        if end in lines:
            lines = lines[:lines.index(end)]
    title = lines[0]
    before = page.lines[page.heading_at - 1] if page.heading_at else ""
    brand = "" if before in ("Join Campaign", "Back") or before.endswith("| Content Rewards") else before

    status_at = next((i for i, line in enumerate(lines) if line.lower() in STATUSES), None)
    launched_at = next((i for i, line in enumerate(lines) if line.startswith("Launched ")), None)
    summary = " ".join(lines[1:status_at or launched_at or 1])[:400]
    join_at = next((i for i, line in enumerate(lines) if line == "Join Campaign"), len(lines))
    tags = lines[(launched_at + 1 if launched_at is not None else join_at):join_at]
    words = [t for t in tags if re.fullmatch(r"[A-Za-z][A-Za-z &/-]{1,40}", t)
             and t.lower() not in PLATFORMS and t not in ("CPM", "Pool")]

    def after(label: str, start: int) -> float | None:
        """The amount on the line after `label`, within the rate block that begins at `start`."""
        block = lines[start:start + 6]
        j = start + block.index(label) if label in block else None
        return money(lines[j + 1]) if j is not None and j + 1 < len(lines) else None

    rates = []
    for i, line in enumerate(lines):
        if line == "Per 1K views" and i and lines[i - 1].lower() in PLATFORMS:
            rate = money(lines[i + 1]) if i + 1 < len(lines) else None
            if rate is not None:
                rates.append({"platform": PLATFORMS[lines[i - 1].lower()], "rate_per_1k": rate,
                              "min_payout": after("Min payout", i), "max_payout": after("Max payout", i)})

    budget_total = budget_left = None
    if "Budget" in lines:
        block = " ".join(lines[lines.index("Budget") + 1:lines.index("Budget") + 9])
        found = re.search(r"\$\s*([\d,.]+[KkMm]?)\s*/\s*\$\s*([\d,.]+[KkMm]?)", block)
        if found:
            spent, budget_total = money(found.group(1)), money(found.group(2))
            budget_left = max(0.0, budget_total - spent)
    pool = "Pool" in tags or "Pool" in lines[join_at:join_at + 3]
    if pool and budget_total is None and "Pool" in lines:
        after_pool = [money(line) for line in lines[lines.index("Pool"):lines.index("Pool") + 4] if "$" in line]
        budget_total = next((value for value in after_pool if value is not None), None)
    if not rates and budget_total is None:
        return None

    rules: list[str] = []
    for heading in RULE_HEADINGS:
        if heading in lines:
            rules += [line for line in lines[lines.index(heading) + 1:]
                      if not NOT_RULES.match(line) and line not in RULE_HEADINGS][:20]
            break
    restricted = next((name for name, pattern in RESTRICTED
                       if pattern.search(" ".join([title, brand, summary, *rules]))), "none")
    lowest = min(rates, key=lambda r: r["rate_per_1k"]) if rates else None
    return {
        "title": title[:300], "brand": brand[:200], "category": words[-1] if words else "",
        "summary": summary, "kind": "clipping",
        "payout_model": "cpm" if rates else "pool" if pool else "other",
        "listing_status": STATUSES[lines[status_at].lower()] if status_at is not None else "unknown",
        "currency": "USD",
        "rate_per_1k": lowest["rate_per_1k"] if lowest else None,
        "min_payout": lowest["min_payout"] if lowest else None,
        "max_payout": lowest["max_payout"] if lowest else None,
        "budget_total": budget_total, "budget_left": budget_left,
        "platforms": [r["platform"] for r in rates], "platform_rates": rates,
        "source_material": "unknown", "tier1_min_share": None, "geo_requirement": "",
        "restricted_category": restricted,
        "requirements": "\n".join(rules) if rules else
        "Not checked: read without AI, and the rules may be visible only after joining.",
        "theme_fit": None, "theme_reason": "Not judged: read without AI",
    }


def allowed_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return urlsplit(url).scheme == "https" and host in HOSTS


def fetch(url: str) -> str:
    """One public page of an allowed site."""
    if not allowed_host(url):
        raise ScoutError(f"The scout only reads {', '.join(sorted(HOSTS))}, not {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https on an allowed host
        data = response.read(MAX_BYTES + 1)
        charset = response.headers.get_content_charset() or "utf-8"
    if len(data) > MAX_BYTES:
        raise ScoutError(f"{url} is larger than expected; skipped")
    return data.decode(charset, errors="replace")


def robot_rules(text: str) -> list[tuple[bool, str]]:
    """(allow, path pattern) lines of the group that applies to the scout.

    The group naming the scout wins over `*`. Python's RobotFileParser applies
    the first matching line, which reads "Allow: /" followed by "Disallow: /api/"
    as allowing everything, so the standard (RFC 9309) is applied by hand.
    """
    groups: dict[str, list[tuple[bool, str]]] = {}
    agents: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        key, _, value = raw.split("#")[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
            for agent in agents:
                groups.setdefault(agent, [])
        elif key in ("allow", "disallow") and agents:
            in_rules = True
            if value:
                for agent in agents:
                    groups[agent].append((key == "allow", value))
    return groups.get("clipperscout", groups.get("*", []))


def robots_allow(rules: list[tuple[bool, str]], path: str) -> bool:
    """The longest matching pattern decides; on a tie, allow."""
    best: tuple[int, bool] = (-1, True)
    for allow, pattern in rules:
        regex = re.escape(pattern).replace(r"\*", ".*")
        if regex.endswith(r"\$"):
            regex = regex[:-2] + "$"
        if re.match(regex, path) and (len(pattern), allow) > best:
            best = (len(pattern), allow)
    return best[1]


class Robots:
    """The site's robots.txt, read once per run."""

    def __init__(self, get=fetch):
        self.get = get
        self.rules: dict[str, list[tuple[bool, str]] | None] = {}

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.rules:
            try:
                self.rules[origin] = robot_rules(self.get(f"{origin}/robots.txt"))
            except urllib.error.HTTPError as err:
                # Refused: stay out. Missing: nothing is off limits.
                self.rules[origin] = None if err.code in (401, 403) else []
        rules = self.rules[origin]
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return rules is not None and robots_allow(rules, path)


# --- choosing what to read ------------------------------------------------------------

class Picks(BaseModel):
    numbers: list[int] = Field(description="Numbers of the cards worth reading, best fit first")


PICK_SYSTEM = prompts.Prompt("campaign_pick")


def pick(new: list[Card], theme: str, limit: int) -> list[Card]:
    """The new cards that fit the theme, as judged by Claude (quick tier)."""
    if limit <= 0 or not new:
        return []
    shown = new[:MAX_CARDS]
    listing = "\n".join(f"{i}. {card.text[:200]}" for i, card in enumerate(shown, 1))
    prompt = (f"<creator_theme>{theme}</creator_theme>\n\n<cards>\n{listing}\n</cards>\n\n"
              f"Pick at most {limit}.")
    answer = llm.ask("campaign_pick", PICK_SYSTEM, prompt, Picks, effort="low")
    chosen: list[Card] = []
    for number in answer.numbers:
        if 1 <= number <= len(shown) and shown[number - 1] not in chosen:
            chosen.append(shown[number - 1])
    return chosen[:limit]


class AiState:
    """How the AI is doing during one run.

    One failure skips one step (a listing, or the theme picks). Two in a row
    mean the AI is unavailable (no credit, no key, offline): the run then makes
    no more AI calls and says so, instead of failing.
    """

    def __init__(self):
        self.failures = 0
        self.reason: str | None = None     # the latest failure, announced in the job's result

    @property
    def down(self) -> bool:
        return self.failures >= 2

    def worked(self) -> None:
        self.failures = 0

    def failed(self, err: Exception) -> bool:
        self.failures += 1
        self.reason = str(err).strip() or err.__class__.__name__
        return self.down


def plan(found: list[Card], known: dict[str, tuple[str, str]], theme: str, max_pages: int,
         rep: Reporter, picker=pick, ai: AiState | None = None) -> list[tuple[str, str]]:
    """(url, why) for each listing to read this run, at most `max_pages`.

    `known` maps each stored campaign's link to (decision, read_by).
    """
    ai = ai or AiState()
    chosen: list[tuple[str, str]] = [(url, f"refresh, you marked it {decision}")
                                     for url, (decision, _) in known.items()
                                     if decision in REFRESH and allowed_host(url)][:max_pages]
    new = [card for card in found if card.url not in known]
    rep.info(f"{len(found)} listings on the discover page, {len(new)} not seen before; "
             f"{len(chosen)} to refresh")
    room = max_pages - len(chosen)
    fits: list[Card] = []
    if room > 0 and new:
        try:
            fits = picker(new, theme, max(1, room - COMPARE))
            ai.worked()
        except Exception as err:  # noqa: BLE001 - any AI failure is announced, never fatal
            ai.failed(err)
            rep.warn(f"AI skipped while choosing listings: {ai.reason} New listings are chosen by rate only.")
    chosen += [(card.url, "fits your theme") for card in fits]
    if not ai.failures:
        # Listings read without AI earlier get a proper reading now.
        taken = {url for url, _ in chosen}
        again = [url for url, (decision, read_by) in known.items()
                 if read_by == "page" and decision != "skipped" and url not in taken and allowed_host(url)]
        chosen += [(url, "read without AI last time") for url in again[:max(0, max_pages - COMPARE - len(chosen))]]
    rest = sorted((card for card in new if card not in fits and card.rate is not None),
                  key=lambda card: -card.rate)
    chosen += [(card.url, f"pays ${card.rate:g} per 1,000 views" + ("" if ai.failures else ", for comparison"))
               for card in rest[:max(0, max_pages - len(chosen))]]
    return chosen


# --- the job --------------------------------------------------------------------------

def run(settings: Settings, rep: Reporter, get=fetch, pause: float = PAUSE_SECONDS, picker=pick) -> dict:
    """Read the discover page, choose listings and add each one to Campaigns."""
    robots = Robots(get)
    rep.progress(None, "Reading the discover page")
    try:
        if not robots.allows(DISCOVER_URL):
            raise ScoutError("The marketplace's robots.txt no longer allows reading its listings")
        found = cards(get(DISCOVER_URL))
    except OSError as err:
        raise ScoutError(f"Could not read {DISCOVER_URL}: {err}") from err
    if not found:
        raise ScoutError("The discover page shows no campaigns: the site may have changed its layout")

    with session_scope() as db:
        known = {c.url: (c.decision, c.read_by)
                 for c in db.scalars(select(Campaign).where(Campaign.url.is_not(None)))}
    ai = AiState()
    chosen = plan(found, known, settings.theme, settings.scout_max_pages, rep, picker, ai)
    if not chosen:
        rep.info("Nothing new to read")
        return {"read": 0, "new": 0, "failed": 0, "without_ai": 0, "ai_skipped": ai.reason}

    added: list[int] = []
    refreshed = failed = without_ai = 0
    for i, (url, why) in enumerate(chosen):
        if i:
            time.sleep(pause)
        rep.progress(i / len(chosen), f"Reading listing {i + 1} of {len(chosen)}")
        if not robots.allows(url):
            rep.warn(f"Skipped {url}: robots.txt does not allow it")
            failed += 1
            continue
        try:
            html = get(url)
        except (OSError, ScoutError) as err:
            rep.warn(f"Could not open {url}: {err}")
            failed += 1
            continue
        text = listing_text(html, url)
        if len(text) < 40:
            rep.warn(f"Skipped {url}: the page has no listing text (removed, or the layout changed)")
            failed += 1
            continue
        terms, read_by = None, "ai"
        if not ai.down:
            try:
                terms = campaigns.extract(text, settings.theme)
                ai.worked()
            except Exception as err:  # noqa: BLE001 - any AI failure is announced, never fatal
                if ai.failed(err):
                    rep.warn(f"AI unavailable: {ai.reason} This listing and the rest are read from the page "
                             "layout instead.")
                else:
                    rep.warn(f"The AI could not read {url} ({ai.reason}); reading the page layout instead")
        if terms is None:
            terms, read_by = page_terms(html, url), "page"
            if terms is None:
                rep.warn(f"Skipped {url}: its layout was not recognised, and the AI could not read it")
                failed += 1
                continue
            without_ai += 1
            if known.get(url, ("", ""))[1] == "ai":
                # Claude read it before: refresh only the numbers and status, keep its judgement.
                terms, read_by = {field: terms[field] for field in LAYOUT_FIELDS}, "ai"
        with session_scope() as db:
            campaign, created = campaigns.store(db, url, terms, text, read_by)
            result = campaigns.assess(campaign, settings)
            title = campaign.title
            if created:
                added.append(campaign.id)
        refreshed += not created
        rate = f", net {result['net_rate']:g} per 1,000 views" if result["net_rate"] is not None else ""
        how = ", read without AI" if read_by == "page" else ""
        rep.info(f"{'New' if created else 'Refreshed'}: {title}, score {result['score']}{rate} ({why}{how})")

    read = len(chosen) - failed
    if not read:
        raise ScoutError(f"None of the {failed} listings could be read; see the lines above")
    if without_ai:
        rep.warn(f"{without_ai} listing{'s were' if without_ai != 1 else ' was'} read without AI, from the page "
                 "layout: rules, location and theme fit are not checked. The next run with AI reads them again.")
    rep.info(f"Read {read} listings: {len(added)} new, {refreshed} refreshed"
             + (f", {without_ai} without AI" if without_ai else "") + (f", {failed} skipped" if failed else ""))
    return {"read": read, "new": len(added), "failed": failed, "without_ai": without_ai, "ai_skipped": ai.reason}


def outcome(result: dict) -> str:
    """The job's final message: plain "Done", or what the AI skipped."""
    if not result.get("ai_skipped"):
        return "Done"
    return f"Done, AI skipped: {result['ai_skipped']}"


# --- the schedule ---------------------------------------------------------------------

def is_active(db: Session) -> bool:
    return db.scalar(select(Job.id).where(Job.kind == "scout", Job.state.in_(jobs.ACTIVE)).limit(1)) is not None


def queue(db: Session) -> Job:
    return jobs.enqueue(db, "scout", "Looking for campaigns", {})


def due(db: Session, settings: Settings, now: datetime | None = None) -> bool:
    """A scheduled run is due: Clipping is on, a schedule is set, and the last run is old enough."""
    if not (settings.feature_clipping and settings.scout_every_hours):
        return False
    last = db.scalar(select(Job).where(Job.kind == "scout").order_by(Job.id.desc()).limit(1))
    if last is None:
        return True
    if last.state in jobs.ACTIVE:
        return False
    return as_utc(last.created_at) + timedelta(hours=settings.scout_every_hours) <= (now or utcnow())


def queue_if_due(settings: Settings) -> bool:
    with session_scope() as db:
        if due(db, settings):
            queue(db)
            return True
    return False
