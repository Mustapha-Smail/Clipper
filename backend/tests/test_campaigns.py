import pytest

from clipper import campaigns
from clipper.db.models import Campaign
from clipper.settings import Settings


def campaign(**over):
    base = dict(title="Test", currency="USD", rate_per_1k=2.0, fee_pct=10.0, max_payout=300.0, min_payout=4.0,
                budget_left=5000.0, budget_total=10000.0, listing_status="open", theme_fit=8,
                source_material="provided",
                tier1_min_share=None, geo_requirement="", restricted_category="none", kind="clipping",
                payout_model="cpm", requirements="", platforms=["tiktok"], brand="", platform_rates=[])
    return Campaign(**{**base, **over})


def test_net_numbers_take_the_fee_and_the_per_clip_cap_into_account():
    result = campaigns.assess(campaign(rate_per_1k=2.0, max_payout=150.0), Settings())
    assert result["net_rate"] == 1.8
    assert result["payout_100k"] == 135.0          # 100k views would earn 200, capped at 150, minus 10%
    assert campaigns.assess(campaign(max_payout=None), Settings())["payout_100k"] == 180.0
    assert result["joinable"] and result["flags"] == []


def test_the_rate_used_is_the_best_one_on_a_platform_you_post_on():
    rates = [{"platform": "tiktok", "rate_per_1k": 1.0, "min_payout": 10, "max_payout": 100},
             {"platform": "instagram", "rate_per_1k": 2.0, "min_payout": 19, "max_payout": 200},
             {"platform": "facebook", "rate_per_1k": 3.0, "min_payout": 19, "max_payout": 500}]
    listing = campaign(rate_per_1k=1.0, max_payout=100.0, platforms=["tiktok", "instagram", "facebook"],
                       platform_rates=rates)
    both = campaigns.assess(listing, Settings(platforms=["tiktok", "instagram"]))
    assert (both["net_rate"], both["rate_platform"], both["payout_100k"]) == (1.8, "instagram", 180.0)
    tiktok_only = campaigns.assess(listing, Settings(platforms=["tiktok"]))
    assert (tiktok_only["net_rate"], tiktok_only["payout_100k"]) == (0.9, 90.0)
    elsewhere = campaigns.assess(campaign(platforms=["x"]), Settings(platforms=["youtube"]))
    assert any("none of which is in your platforms" in f for f in elsewhere["flags"])


def test_higher_rate_and_better_fit_rank_higher():
    settings = Settings()
    low = campaigns.assess(campaign(rate_per_1k=0.5, theme_fit=3), settings)["score"]
    high = campaigns.assess(campaign(rate_per_1k=4.0, theme_fit=9), settings)["score"]
    assert high > low + 25


def test_closed_or_spent_campaigns_sink_and_say_why():
    settings = Settings()
    open_score = campaigns.assess(campaign(), settings)["score"]
    paused = campaigns.assess(campaign(listing_status="paused"), settings)
    spent = campaigns.assess(campaign(budget_left=0.0), settings)
    assert not paused["joinable"] and paused["score"] < open_score / 3
    assert not spent["joinable"] and "The budget is spent" in spent["flags"]


def test_audience_location_rule_depends_on_your_own_split():
    needs = campaign(tier1_min_share=60)
    unknown = campaigns.assess(needs, Settings())
    low = campaigns.assess(needs, Settings(audience_tier1_share=20))
    enough = campaigns.assess(needs, Settings(audience_tier1_share=75))
    assert any("not set in Settings" in f for f in unknown["flags"])
    assert any("yours is 20%" in f for f in low["flags"]) and low["score"] < unknown["score"] < enough["score"]
    assert enough["flags"] == []


def test_restricted_products_and_missing_rates_are_flagged():
    flags = campaigns.assess(campaign(restricted_category="gambling", rate_per_1k=None, payout_model="pool"),
                             Settings())["flags"]
    assert any("Gambling" in f for f in flags) and any("Not paid per view" in f for f in flags)


def test_penalties_lower_the_score_and_the_breakdown_still_adds_up():
    plain = campaigns.assess(campaign(), Settings())
    risky = campaigns.assess(campaign(restricted_category="trading", listing_status="paused"), Settings())
    assert risky["score"] < plain["score"]
    # The score never goes below zero, even when the penalties add up to more.
    assert max(0, round(sum(line["points"] for line in risky["breakdown"]))) == risky["score"]
    assert round(sum(line["points"] for line in plain["breakdown"])) == plain["score"]
    assert [line["label"] for line in risky["breakdown"]][-2:] == ["Cannot be joined right now",
                                                                    "Trading or investing product"]


def test_brief_lists_the_rules_one_per_line():
    brief = campaigns.brief_for(campaign(title="Seal", brand="SealStats", platforms=["tiktok", "x"],
                                         requirements="Videos must be at least 10 seconds long.\n• No ads or bots"))
    assert brief.splitlines() == ["Campaign: Seal (by SealStats)", "Allowed platforms: tiktok, x", "Requirements:",
                                  "- Videos must be at least 10 seconds long.", "- No ads or bots"]


TERMS = campaigns.Terms(
    title="Seal stream", brand="SealStats", category="Personal Brand", summary="Clip the stream.", kind="clipping",
    payout_model="cpm", listing_status="open", currency="USD", rate_per_1k=2.0, min_payout=4.0, max_payout=300.0,
    budget_total=1000.0, budget_left=400.0, platforms=["tiktok", "instagram"], platform_rates=[],
    source_material="provided",
    tier1_min_share=None, geo_requirement="", restricted_category="none",
    requirements="Videos must be at least 10 seconds long.", theme_fit=4, theme_reason="Streamer content.")
URL = "https://contentrewards.com/c/campaigns/abc"
LISTING = "Get paid $2/1000 views clipping for Seal. " * 3


@pytest.fixture()
def fake_claude(monkeypatch):
    monkeypatch.setattr(campaigns, "extract", lambda text, theme: TERMS)


def test_reading_a_listing_stores_it_and_a_second_read_keeps_your_decision(client, fake_claude):
    created = client.post("/api/v1/campaigns", json={"url": URL, "text": LISTING})
    assert created.status_code == 200
    body = created.json()
    assert body["source"] == "contentrewards" and body["net_rate"] == 1.8 and body["decision"] == "new"

    client.patch(f"/api/v1/campaigns/{body['id']}", json={"decision": "joined", "notes": "Applied Monday"})
    again = client.post("/api/v1/campaigns", json={"url": URL, "text": LISTING}).json()
    assert again["id"] == body["id"] and again["decision"] == "joined" and again["notes"] == "Applied Monday"
    assert len(client.get("/api/v1/campaigns").json()) == 1
    assert client.post("/api/v1/campaigns", json={"text": "too short"}).status_code == 422


def test_terms_can_be_corrected_by_hand(client, fake_claude):
    body = client.post("/api/v1/campaigns", json={"text": LISTING}).json()
    fixed = client.patch(f"/api/v1/campaigns/{body['id']}", json={"rate_per_1k": 3.0, "budget_left": 0}).json()
    assert fixed["net_rate"] == 2.7 and fixed["joinable"] is False


def test_a_video_made_for_a_campaign_gets_its_rules_as_brief(client, fake_claude, video):
    camp = client.post("/api/v1/campaigns", json={"text": LISTING}).json()
    linked = client.patch(f"/api/v1/videos/{video}",
                          json={"campaign_id": camp["id"], "use_campaign_brief": True}).json()
    assert linked["campaign"] == {"id": camp["id"], "title": "Seal stream"}
    assert "at least 10 seconds" in linked["brief"]
    assert client.get("/api/v1/campaigns").json()[0]["video_count"] == 1

    job = client.post("/api/v1/videos", json={"src": "https://example.com/v", "campaign_id": camp["id"]})
    assert job.status_code == 202
    assert client.post("/api/v1/videos", json={"src": "https://example.com/v", "campaign_id": 999}).status_code == 400

    assert client.delete(f"/api/v1/campaigns/{camp['id']}").status_code == 204
    kept = client.get(f"/api/v1/videos/{video}").json()
    assert kept["campaign"] is None and "at least 10 seconds" in kept["brief"]
