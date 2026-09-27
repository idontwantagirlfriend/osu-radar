# osu! Radar — modeling a player's aim from their past replays

English | [中文](../../README.md)

> osu! stable only, for now.

A simple conditional-probability model that estimates where a player's aim lands on a given map, and turns that into a rank. The result is drawn as a radar chart plus a rank on a web page, small enough to be dropped into OBS as a browser source.

![OBS browser source](../imgs/widget-example.png)

The estimate is meant to be read like this:

> **On maps with effective AR = x and SR = y, when the map's CS is z, the player will reach n% of the objects** (pooled by AR and SR)
>
> **On maps with CS = z and SR = y, the player will reach n% of the objects** (pooled by CS and SR)

"Reaching" an object means the cursor was still inside that object's hit circle at that object's hit time. tosu is used to read the map currently loaded in osu!, which is what fills in x, y and z for you — in live mode the overlay follows whatever you are playing, applies the active mods, and updates itself.

- Samples are weighted per object, so a long map simply contributes more of them.
- EZ/HR/DT/HT are folded into *effective* CS/AR first (a rate change shifts AR by scaling the approach time), so plays are pooled by the difficulty you actually faced rather than by the map's base difficulty. SR is the actual post-mod star rating, computed locally with rosu-pp.
- These are statistical conclusions only: n% is an expected value, and no average promises anything about any single play.

## Installation

The software depends on [osu!stable](https://osu.ppy.sh/home/download) and [tosu](https://github.com/tosuapp/tosu). The backend is Python plus [rosu-pp-py](https://pypi.org/project/rosu-pp-py/) for post-mod star ratings; the Vue frontend ships prebuilt in `frontend/dist`. If `frontend/dist` is ever missing: `cd frontend && npm install && npm run build`.

osu!lazer does not necessarily expose its internal replays, so porting to it is not worth attempting.

```
./start.sh            # Linux / WSL / macOS
.\start.ps1           # Windows PowerShell
```

Either script bootstraps `.env` from `.env.example`, runs an incremental ingest of your replays (already-analyzed ones are skipped, so repeat runs take seconds), and serves the UI. Environment variables: `PORT` (default 8000), `SKIP_INGEST=1` to skip the ingest, `NO_BROWSER=1` to not open a browser.

Paths are configured in the web UI at `/` — the osu! install dir, the tosu binary, the replay dir and the Songs dir — and are written to `.env`. Saving them takes effect immediately and kicks off a background ingest. Leave `OSU_DIR` empty and a running tosu instance is used to auto-detect the osu! installation instead.

Once configured:

- `/` — configuration (paths, grade colors, grade artwork)
- `/debug` — the radar dashboard: pick the AR or CS dimension, slide along the buckets, filter by player / mods / time window
- `/ar` and `/cs` — the OBS overlays; every option is a query parameter (`?since=6m&player=&mods=HD,DT&no_mods=EZ&min_objects=10&sr_range=0.5&theme=dark`)

## Privacy

Nothing is sent anywhere; results stay on your machine. All communication with tosu is over localhost. Replays are pulled in by *copying* them out of the scores directory into the project, and no osu! file is ever modified.

## How the estimate is computed

Given your past replays, estimate the probability that your aim lands within a given offset.

If you ignore pattern, difficulty, map length and everything else, treat the objects of all replays as one big set, take the distance between the cursor and the object on the corresponding frame as the aim offset, and compute the distribution of that offset — you get the base model:

> When the offset region is restricted to within CS x, the player will reach n% of the objects.

From the same distribution you get p100/p90/p80/p70/p60, which are the containment ratios required for the S/A/B/C/D ranks respectively. Below that ratio the expected rank is below that rank, no matter what your accuracy says.

**The key extrapolation: ignore the effect the map's own CS has on your aim.**

> On a map with CS = x, the player will reach n% of the objects.

This extrapolation is obviously rough. On a CS2 map your movement is definitely sloppier than on a CS5 map; but once a CS threshold is crossed, the player starts to feel out of their depth, and the offset coming from raw skill becomes visible. Above that threshold the number is meaningful, below it, it is not.

And by restricting the past replays to a band of AR or CS, you get the aim offsets on maps of that same AR or CS (you cannot restrict both at once — the range gets too narrow), which gives a stronger claim:

> **On maps with effective AR = x, when the map's CS is y, the player will reach n% of the objects** (pooled by AR)
>
> **On maps with CS = y, the player will reach n% of the objects** (pooled by CS)

**Adding SR as a variable**

**On top of the AR/CS pooling, the past replays are further restricted to maps whose post-mod SR is within ±0.5★ of the current map**: modern maps cluster around AR 9–10 and CS 4–5, so AR/CS alone cannot capture how much the map's placement demands — the AR 9.8–10 ceiling spans everything from 7★ to 10★, and estimating one and the same rank across all of it is plainly unreasonable.

## Reading the radar

Each dashed ring is one rank, drawn at the offset percentile that rank demands (S = p100, A = p90, B = p80, C = p70, D = p60). The rings are mapped onto circle radii, so the scale on the left is literally CS: the solid ring is the effective CS of the map being played (or the CS you are inspecting in the CS model), and the ghosted rank in the middle is the strictest rank whose ring still fits inside it — i.e. the rank you would be expected to hold at that CS.

The same conversion is reported numerically as **minCS**: the CS whose circle exactly covers that percentile of your offsets. A negative minCS means the offset is larger than even a CS0 circle, so no circle size contains it.
