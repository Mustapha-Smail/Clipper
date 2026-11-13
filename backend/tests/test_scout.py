import urllib.error
from datetime import timedelta

from clipper import campaigns, jobs, scout
from clipper.db import session_scope
from clipper.db.models import Campaign, Job, utcnow
from clipper.jobs import Reporter
from clipper.settings import Settings

BASE = "https://contentrewards.com"

DISCOVER = """<html><head><title>Discover</title><script>var x = "/c/campaigns/zzz";</script></head><body>
<nav><a href="/c/discover">Discover</a></nav>
<a href="/c/campaigns/aaa?ref=1" aria-label="Preview AI Podcast Clips"><span>AI Podcast Clips</span>
  <span>Founders Inc · 2d</span><span>$1.2K / $5K</span><span>$2.50 /1K</span></a>
<a href="/c/campaigns/aaa"><img alt=""></a>
<a href="/c/campaigns/bbb" aria-label="Preview Gaming Stream"><span>Gaming Stream</span><span>$1 /1K</span></a>
<a href="/c/campaigns/ccc" aria-label="Preview Music Video"><span>Music Video</span><span>$0.40 /1K</span></a>
<a href="/c/campaigns/ddd" aria-label="Preview Skipped One"><span>$9 /1K</span></a>
<a href="https://elsewhere.example/c/campaigns/eee">Elsewhere</a>
</body></html>"""


def listing(title: str) -> str:
    return (f"<html><body><nav>Sign in</nav><a>Back</a><span>Founders Inc</span><h1>{title}</h1>"
            "<p>Clip our podcast about trading.</p><p>Accepting clips</p><p>Launched 2 days ago</p><p>Podcast</p>"
            "<p>12</p><p>CPM</p><button>Join Campaign</button>"
            "<p>TikTok</p><p>Per 1K views</p><p>$2.50</p><p>Min payout</p><p>$5.00</p><p>Max payout</p>"
            "<p>$300.00</p><p>Instagram</p><p>Per 1K views</p><p>$1.00</p><p>Min payout</p><p>$2.00</p>"
            "<p>Max payout</p><p>$100.00</p><p>Budget</p><p>$</p><p>1,200</p><p>/</p><p>$5,000</p>"
            "<p>$3,800</p><p>still up for grabs</p><style>.x{}</style>"
            "<h2>Top clippers</h2><p>Rank 1</p></body></html>")


ROBOTS = "User-Agent: *\nAllow: /\nDisallow: /api/\n"


class Log(Reporter):
    def __init__(self):
        self.lines = []

    def info(self, message):
        self.lines.append(message)

    def warn(self, message):
        self.lines.append("WARN " + message)


def fake_site(pages: dict[str, str]):
    calls = []

    def get(url):
        calls.append(url)
        if url not in pages:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return pages[url]
    get.calls = calls
    return get


def fake_terms(text, theme):
    title = text.splitlines()[0]
    return campaigns.Terms(
        title=title, brand="", category="", summary="", kind="clipping", payout_model="cpm",
        listing_status="open", currency="USD", rate_per_1k=2.5, min_payout=None, max_payout=None,
        budget_total=5000, budget_left=3800, platforms=["tiktok"], platform_rates=[], source_material="provided",
        tier1_min_share=None, geo_requirement="", restricted_category="none", requirements="",
        theme_fit=8, theme_reason="")


def test_cards_and_listing_text_come_from_plain_html():
    found = scout.cards(DISCOVER)
    assert [c.url for c in found] == [f"{BASE}/c/campaigns/{x}" for x in ("aaa", "bbb", "ccc", "ddd")]
    assert found[0].text.startswith("AI Podcast Clips") and "$2.50 /1K" in found[0].text
    assert [c.rate for c in found] == [2.5, 1.0, 0.4, 9.0]
    text = scout.listing_text(listing("AI Podcast Clips"), f"{BASE}/c/campaigns/aaa")
    assert text.startswith("AI Podcast Clips") and "Budget" in text
    assert "Sign in" not in text and "Rank 1" not in text and ".x" not in text


def test_robots_rules_are_followed():
    robots = scout.Robots(fake_site({f"{BASE}/robots.txt": ROBOTS}))
    assert robots.allows(f"{BASE}/c/campaigns/aaa") and not robots.allows(f"{BASE}/api/x")
    assert scout.Robots(fake_site({})).allows(f"{BASE}/c/x")      # no robots.txt: allowed
    rules = scout.robot_rules("User-agent: Googlebot\nDisallow: /\n\nUser-agent: *\nDisallow: /c/\n"
                              "Allow: /c/discover\nDisallow: /*.pdf$\n")
    assert scout.robots_allow(rules, "/c/discover/clipping") and not scout.robots_allow(rules, "/c/campaigns/a")
    assert not scout.robots_allow(rules, "/files/a.pdf") and scout.robots_allow(rules, "/files/a.pdf.html")
    own = scout.robot_rules("User-agent: *\nAllow: /\n\nUser-agent: ClipperScout\nDisallow: /\n")
    assert not scout.robots_allow(own, "/c/discover")


def test_only_the_marketplace_is_ever_fetched():
    try:
        scout.fetch("https://elsewhere.example/page")
    except scout.ScoutError as err:
        assert "only reads" in str(err)
    else:
        raise AssertionError("fetched a host outside the allow-list")


def test_plan_refreshes_your_campaigns_then_picks_fits_then_best_paid():
    found = scout.cards(DISCOVER)
    known = {f"{BASE}/c/campaigns/ddd": ("skipped", "ai"), f"{BASE}/c/campaigns/old": ("joined", "ai")}
    picked = []

    def picker(new, theme, limit):
        picked.append(([c.url for c in new], limit))
        return [new[0]]
    chosen = scout.plan(found, known, "AI podcasts", 5, Log(), picker)
    assert [url.rsplit("/", 1)[1] for url, _ in chosen] == ["old", "aaa", "bbb", "ccc"]
    assert "ddd" not in str(picked)                       # skipped campaigns are never read again
    assert picked[0][1] == 1                               # room 4, minus 3 kept for comparison
    assert scout.plan(found, known, "AI", 1, Log(), picker) == [(f"{BASE}/c/campaigns/old",
                                                                 "refresh, you marked it joined")]


def test_a_run_adds_new_listings_and_keeps_your_decision(app_env, monkeypatch):
    monkeypatch.setattr(campaigns, "extract", fake_terms)
    with session_scope() as db:
        db.add(Campaign(url=f"{BASE}/c/campaigns/bbb", source="contentrewards", title="Gaming Stream",
                        decision="interested", notes="mine"))
    site = fake_site({f"{BASE}/robots.txt": ROBOTS, scout.DISCOVER_URL: DISCOVER,
                      f"{BASE}/c/campaigns/aaa": listing("AI Podcast Clips"),
                      f"{BASE}/c/campaigns/bbb": listing("Gaming Stream"),
                      f"{BASE}/c/campaigns/ccc": "<html><body>gone</body></html>"})
    log = Log()
    result = scout.run(Settings(scout_max_pages=4), log, get=site, pause=0,
                       picker=lambda new, theme, limit: [new[0]])
    assert result == {"read": 2, "new": 1, "failed": 2, "without_ai": 0, "ai_skipped": None}
    assert scout.outcome(result) == "Done"
    with session_scope() as db:
        rows = {c.url.rsplit("/", 1)[1]: c for c in db.query(Campaign)}
        assert set(rows) == {"aaa", "bbb"}
        assert (rows["bbb"].decision, rows["bbb"].notes, rows["bbb"].budget_left) == ("interested", "mine", 3800)
    assert any(line.startswith("New: AI Podcast Clips, score") for line in log.lines)
    assert any("no listing text" in line for line in log.lines)
    assert f"{BASE}/robots.txt" in site.calls and len([u for u in site.calls if "robots" in u]) == 1


def test_a_run_fails_when_nothing_can_be_read(app_env):
    site = fake_site({f"{BASE}/robots.txt": ROBOTS, scout.DISCOVER_URL: "<html><body>No cards</body></html>"})
    try:
        scout.run(Settings(), Log(), get=site, pause=0)
    except scout.ScoutError as err:
        assert "no campaigns" in str(err)
    else:
        raise AssertionError("an empty discover page should fail the job")
    blocked = fake_site({f"{BASE}/robots.txt": "User-Agent: *\nDisallow: /\n"})
    try:
        scout.run(Settings(), Log(), get=blocked, pause=0)
    except scout.ScoutError as err:
        assert "robots.txt" in str(err)
    else:
        raise AssertionError("robots.txt said no")


def test_schedule_runs_only_with_clipping_on_and_after_the_interval(app_env):
    with session_scope() as db:
        assert not scout.due(db, Settings(scout_every_hours=24, feature_clipping=False))
        assert not scout.due(db, Settings(scout_every_hours=0, feature_clipping=True))
        on = Settings(scout_every_hours=24, feature_clipping=True)
        assert scout.due(db, on)
        job = scout.queue(db)
        assert not scout.due(db, on)                      # one is already queued
        job.state = "succeeded"
        assert not scout.due(db, on)
        assert scout.due(db, on, now=utcnow() + timedelta(hours=25))
    assert scout.queue_if_due(Settings(scout_every_hours=6, feature_clipping=True)) is False


def test_the_scout_button_queues_one_job(client):
    first = client.post("/api/v1/campaigns/scout")
    assert first.status_code == 202 and first.json()["kind"] == "scout"
    assert first.json()["stages"] == ["scout"]
    assert client.post("/api/v1/campaigns/scout").status_code == 409
    with session_scope() as db:
        assert db.query(Job).filter(Job.kind == "scout", Job.state.in_(jobs.ACTIVE)).count() == 1


def no_credit(*_args, **_kwargs):
    raise RuntimeError("Your Anthropic credit balance is too low.")


def full_site():
    return fake_site({f"{BASE}/robots.txt": ROBOTS, scout.DISCOVER_URL: DISCOVER,
                      **{f"{BASE}/c/campaigns/{x}": listing(x.upper()) for x in ("aaa", "bbb", "ccc", "ddd")}})


def test_page_terms_read_the_layout_without_ai():
    terms = scout.page_terms(listing("AI Podcast Clips"), f"{BASE}/c/campaigns/aaa")
    assert (terms["title"], terms["brand"], terms["category"]) == ("AI Podcast Clips", "Founders Inc", "Podcast")
    assert (terms["listing_status"], terms["payout_model"]) == ("open", "cpm")
    assert terms["platforms"] == ["tiktok", "instagram"]
    # The lowest-paying platform's figures are the headline ones, as Claude is asked to do.
    assert (terms["rate_per_1k"], terms["min_payout"], terms["max_payout"]) == (1.0, 2.0, 100.0)
    assert (terms["budget_total"], terms["budget_left"]) == (5000, 3800)
    assert terms["restricted_category"] == "trading"
    assert terms["theme_fit"] is None and "Not checked" in terms["requirements"]
    assert scout.page_terms("<html><body><h1>Something else</h1><p>Hello</p></body></html>", BASE) is None
    assert scout.money("⁦$1,825⁩") == 1825 and scout.money("$12.5K") == 12500


def test_a_page_read_campaign_is_ranked_and_flagged():
    page = Campaign(**scout.page_terms(listing("AI"), BASE), read_by="page", fee_pct=None)
    judged = Campaign(**{**scout.page_terms(listing("AI"), BASE), "theme_fit": 4, "restricted_category": "trading"},
                      read_by="ai", fee_pct=None)
    a, b = campaigns.assess(page, Settings()), campaigns.assess(judged, Settings())
    assert {"label": "Theme fit not judged: read without AI", "points": 10.0} in a["breakdown"]
    assert any(flag.startswith("Read without AI") for flag in a["flags"])
    assert a["score"] == b["score"]           # unknown fit counts as 4/10, the same share as an unknown budget
    assert round(sum(line["points"] for line in a["breakdown"])) == a["score"]


def test_without_ai_listings_still_show_up(app_env, monkeypatch):
    monkeypatch.setattr(campaigns, "extract", no_credit)
    site, log = full_site(), Log()
    result = scout.run(Settings(scout_max_pages=4), log, get=site, pause=0, picker=no_credit)
    assert (result["read"], result["new"], result["without_ai"]) == (4, 4, 4)
    assert "credit balance is too low" in result["ai_skipped"]
    assert scout.outcome(result).startswith("Done, AI skipped: Your Anthropic credit")
    assert any("chosen by rate only" in line for line in log.lines)
    assert any("AI unavailable" in line for line in log.lines)
    assert any("read without AI, from the page layout" in line for line in log.lines)
    with session_scope() as db:
        rows = db.query(Campaign).all()
        assert len(rows) == 4 and {c.read_by for c in rows} == {"page"}


def test_page_read_campaigns_are_read_again_once_the_ai_works(app_env, monkeypatch):
    with session_scope() as db:
        db.add(Campaign(url=f"{BASE}/c/campaigns/aaa", source="contentrewards", title="AAA", read_by="page"))
        db.add(Campaign(url=f"{BASE}/c/campaigns/bbb", source="contentrewards", title="BBB", read_by="page",
                        decision="skipped"))
    found = scout.cards(DISCOVER)
    with session_scope() as db:
        known = {c.url: (c.decision, c.read_by) for c in db.query(Campaign)}
    chosen = dict(scout.plan(found, known, "AI", 5, Log(), lambda new, theme, limit: []))
    assert chosen[f"{BASE}/c/campaigns/aaa"] == "read without AI last time"
    assert f"{BASE}/c/campaigns/bbb" not in chosen                     # skipped stays skipped
    down = dict(scout.plan(found, known, "AI", 5, Log(), no_credit))
    assert f"{BASE}/c/campaigns/aaa" not in down                       # no AI: no point reading it again

    monkeypatch.setattr(campaigns, "extract", fake_terms)
    scout.run(Settings(scout_max_pages=5), Log(), get=full_site(), pause=0, picker=lambda new, theme, limit: [])
    with session_scope() as db:
        again = db.query(Campaign).filter(Campaign.url == f"{BASE}/c/campaigns/aaa").one()
        assert again.read_by == "ai" and again.theme_fit == 8


def test_one_ai_failure_falls_back_for_that_listing_only(app_env, monkeypatch):
    calls = []

    def flaky(text, theme):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("The model's answer did not match the expected shape")
        return fake_terms(text, theme)
    monkeypatch.setattr(campaigns, "extract", flaky)
    log = Log()
    result = scout.run(Settings(scout_max_pages=4), log, get=full_site(), pause=0,
                       picker=lambda new, theme, limit: [new[0]])
    assert (result["read"], result["failed"], result["without_ai"]) == (4, 0, 1)
    assert len(calls) == 4                                  # the AI was still asked for the others
    assert any("reading the page layout instead" in line for line in log.lines)
    assert "Done, AI skipped" in scout.outcome(result)


def test_without_ai_a_campaign_claude_read_keeps_its_judgement(app_env, monkeypatch):
    with session_scope() as db:
        db.add(Campaign(url=f"{BASE}/c/campaigns/aaa", source="contentrewards", title="AAA", decision="joined",
                        read_by="ai", theme_fit=9, theme_reason="Right on theme", requirements="Tag @brand",
                        geo_requirement="US only", budget_left=10.0, listing_status="paused"))
    monkeypatch.setattr(campaigns, "extract", no_credit)
    scout.run(Settings(scout_max_pages=1), Log(), get=full_site(), pause=0, picker=no_credit)
    with session_scope() as db:
        row = db.query(Campaign).filter(Campaign.url == f"{BASE}/c/campaigns/aaa").one()
        assert (row.read_by, row.theme_fit, row.requirements, row.geo_requirement) == ("ai", 9, "Tag @brand", "US only")
        assert (row.budget_left, row.listing_status, row.rate_per_1k) == (3800, "open", 1.0)   # figures refreshed
