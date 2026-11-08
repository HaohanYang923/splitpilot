# Demo video script (target 2:30, hard limit 3:00)

Setup: run `python3 app.py` with PayPal Sandbox credentials set, browser
open at localhost:8000. Have the sample receipt ready (it is pre-filled).

0:00-0:20 Problem. Say: "Three friends, one $83 dinner bill. Shared pizza,
personal dishes, tax, tip, and one awkward text message. SplitPilot
turns the receipt into PayPal payment requests, with AI doing the
checking and the chasing."

0:20-0:50 Split. Click "Split it with AI". Point at the table: pizza
shared three ways, salad only on Alice, tax and tip split
proportionally, and the three shares add up to exactly $83.16. Alice
paid up front, so her row reads Settled, and Bob and Carol owe her.

0:50-1:10 AI check. Change the Total line to 93.16 and re-split: the
total-mismatch warning appears, with the $10.00 difference, before any
payment is requested. Change it back. (A duplicate-line warning works
the same way.)

1:10-1:50 PayPal. Click "Request payment" for Bob. Show the Sandbox
order ID and open the PayPal approval link: "This is a real order
created through the PayPal Orders v2 API in the Sandbox environment."
Click "Capture payment" and watch the collection bar move.

1:50-2:15 Personal payment link. Click "Copy personal payment link"
for Carol and open it in a new tab: she sees only her own items, her
breakdown, and one PayPal button. Pay from that page and switch back:
the organiser's collection bar and grid status update.

2:15-2:30 Reminder agent. Click "Reminder text" for Carol, switch the
tone to Funny, regenerate: the message names her actual items and
amount. Sort the results grid by "Owes" to show the AG Grid table in
action.

2:30-2:45 Close. "Receipt in, money requested, nobody does maths in
the group chat. SplitPilot: built with PayPal and AI, standard-library
Python, code is public on GitHub."

Rules reminder: no copyrighted music, show the project running on
screen, upload to YouTube as public, keep it under 3 minutes.
