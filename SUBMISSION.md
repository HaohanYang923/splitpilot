# Devpost submission draft - SplitPilot
Hackathon: PayPal AI Hackathon (paypalaihackathon.devpost.com)
Deadline: Nov 12, 2026, 3:00 PM EST / 12:00 PM PST
Prize pool: $69,750 total ($67,500+ cash categories). Target categories:
Best Use of PayPal + AI, Best Use of Agentic Commerce, Most Impactful,
and Best Use of AG Grid (the results table is an AG Grid data grid).

## Project name
SplitPilot

## Tagline
Paste a messy group bill. AI splits it fairly to the cent, checks it for
mistakes, and turns every share into a PayPal payment request.

## Description (paste into Devpost)
Splitting a group bill goes wrong in predictable ways: shared dishes,
personal items, tax and tip, rounding drift, the occasional duplicate
charge, and the awkward follow-up message nobody wants to write.
SplitPilot is an AI agent that closes the whole loop, from receipt to
money actually moving.

Paste the receipt and say who was there. SplitPilot's parser reads each
line, assigns personal items to the people named on them, and shares the
rest. Its fairness engine allocates tax and tip proportionally and
reconciles rounding so everyone's shares sum exactly to the bill total.
Before any money is requested, an anomaly check flags duplicate-looking
charges and explains them in plain language.

Tell SplitPilot who paid the whole bill up front, and it works out the
settlement: everyone else owes that person, whose own share is settled
automatically, and a per-person explanation shows exactly how every
number was produced. Each amount owed then becomes a real PayPal order
through the Orders v2 API in the Sandbox environment: approval link,
capture, and a live collection bar tracking what has come back. A
reminder agent drafts the follow-up message in a polite, firm, or
funny tone, built from what that person actually ordered, and the final
split exports as a group-chat summary or CSV. Three built-in scenarios
(dinner, road trip, flat utilities) show it is not a one-bill trick.
Every debtor also gets a personal payment-link page that shows only
their own items and a PayPal pay button, results are presented in an
AG Grid data grid, bills can be split in USD, CAD, EUR, or GBP, and the
collection state persists across server restarts.

The interface is designed around the object it processes: a printed
receipt. It is flat with no shadows or gradients, typeset in IBM Plex
Sans with IBM Plex Mono for every number, and colour is used only as
money state (PayPal blue to act, green when settled, red when owed),
with payment status shown as rubber-stamp badges.

SplitPilot is built to be judged: it runs on the Python standard library
alone, with no build step and no API key required for the core demo.
Add free PayPal Sandbox credentials and the same buttons create live
Sandbox orders instead of simulated ones (the mode is always labelled
in the UI).

## Tools (paste into Devpost "tools" field)
PayPal Developer Platform (Orders v2 API, Sandbox, OAuth client
credentials); AG Grid (results data grid); a local AI layer written for
this project (natural-language receipt parser, proportional fair-split
engine with cent-exact reconciliation, anomaly detector, tone-aware
reminder generator); Python 3 standard library.

## Checklist before submitting
- [ ] Register on Devpost and join the hackathon (user, 5 min)
- [ ] Create PayPal Developer Sandbox app, put Client ID/Secret in env,
      re-run demo so the video shows a real Sandbox approval link
- [ ] Push this folder to a public GitHub repo (license is at the top level)
- [ ] Optional: deploy to Render for a hosted demo URL
- [ ] Record the demo video using DEMO_SCRIPT.md (under 3 min), upload
      to YouTube as public
- [ ] Paste the texts above into Devpost, add repo + video + demo links
- [ ] Submit before Nov 12, 12:00 PM PST (do it by Nov 11 to be safe)
