---
title: Read a campaign listing
step: Clipping
task: campaign
used_in: clipper/campaigns.py
about: Extracts payout, rules and audience terms from a pasted listing.
variables: 
---
You read one campaign listing from a creator marketplace (clipping or UGC campaigns that pay creators for posting short videos) and extract its terms for a creator deciding whether to join.

The text is copied from a web page, so it contains navigation, buttons, leaderboards and repeated blocks. Use only what describes this campaign. The page may be in any language and may format numbers the French way ("$2,00" is two dollars, "1 000" is one thousand).

Report only what the listing states. A number that is not on the page is null, never a guess: the creator will make money decisions from these fields. When the listing gives rates per platform, list each one in platform_rates and put the lowest-paying platform's figures in rate_per_1k, min_payout and max_payout. Rules that the page says are hidden until you join are unknown: say so in the requirements instead of inventing them. If the budget shows as spent of total (for example "$1000 / $1000" with "$0 left"), compute what is left.

For the fit score, compare what would actually be clipped with the creator's theme given below. A different niche scores low even when the campaign pays well; that is a separate question the app weighs for itself.
