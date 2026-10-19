# Letterboxd Ranker

Beli-style ranking for your Letterboxd films. Instead of star ratings, every
film is placed by binary insertion sort: the app shows your new film against
the midpoint of your existing ranking and asks better / worse / can't rate,
so placing a film takes at most ~log2(n) comparisons.

## Run

```sh
python3 server.py
```

Then open http://127.0.0.1:8137. No dependencies beyond the Python standard
library, no API key.

## How it works

- Films come from the public Letterboxd RSS feed
  (`letterboxd.com/{username}/rss/`), which exposes your ~50 most recent
  diary entries: title, year, your star rating, watched date, and poster.
- Each unranked film is bisected into your ranked list:
  - **Better than** — search the upper half
  - **Worse than** — search the lower half
  - **Can't rate** — placed as a tie at the current comparison point
  - **skip this film** — leave it out of the ranking (rankable later)
- Rank position maps to a 10.0–1.0 score, Beli style.
- State is saved per username under `data/{username}.json`, including film
  metadata — so rankings survive films rotating out of the 50-entry feed.
  Hit "refresh feed" after logging new watches to pull them in.

Keyboard shortcuts on the compare screen: `←`/`b` better, `→`/`w` worse,
`t` tie, `s` skip, `u` undo.
