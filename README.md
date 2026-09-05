<div align="center">

<img src="logo.png" alt="AutoQuestGen" width="180">

# AutoQuestGen

**Generate a complete, ready-to-play FTB Quests book for any Minecraft 1.20.1 Forge modpack.**

Point it at a `mods/` folder, press one button, play.

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Minecraft 1.20.1](https://img.shields.io/badge/Minecraft-1.20.1%20Forge-brightgreen.svg)](#known-limits)
[![FTB Quests](https://img.shields.io/badge/FTB%20Quests-2001.x-blue.svg)](#known-limits)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab.svg)](#run-from-source)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](#what-beta-means)
[![Latest release](https://img.shields.io/github/v/release/Antidqrk/AutoQuestGen?include_prereleases&label=download)](../../releases/latest)
[![Works offline](https://img.shields.io/badge/works-offline-success.svg)](#what-it-does)

Created by **antidqrk** · Built with **Claude (Anthropic)** — engineering, QA, and the research pipeline.

</div>

---

## What it does

Writing a quest book by hand is days of work per pack. AutoQuestGen reads the pack you
already have and writes the book for you.

- **Reads your actual jars.** Every mod in `mods/` is opened and parsed for its real
  items, blocks, entities, advancements, recipes and lang names. Nothing is guessed —
  every quest task uses an id that verifiably exists in *your* pack, so no purple
  "Missing Item" icons.
- **Works entirely offline.** No account, no API key, no internet. The offline builder
  is the product.
- **Ships a research database for 300+ mods.** Hand-researched progression chains,
  gating facts, and ordering signal distilled from how real packs and guides teach each
  mod. A mod with no research still gets a sensible chapter from the generic path.
- **Writes real FTB Quests SNBT.** Chapters, chapter groups, reward tables and
  `data.snbt` land straight in `config/ftbquests/quests/`, and anything it replaces is
  backed up first.
- **Designs, not just dumps.** Chapters grouped by theme, progression-ordered quests,
  dimension/boss/structure goals, written descriptions, icons, rewards, and a laid-out
  quest graph with short dependency lines.
- **Stable across rebuilds.** Quest ids are derived deterministically, so regenerating a
  book does not wipe a player's progress. Pin a seed to reproduce a book exactly.

An optional AI path (bring your own API key) can draft or improve books. It is
experimental in this beta — the offline builder is better tested and gets all of the
research data.

## Install

**Option A — the app.** Download `AutoQuestGen.exe` from the
[latest release](../../releases/latest) and double-click it. Python is not required.
There is also a portable zip that starts faster and upsets antivirus less. Settings,
logs and scan caches are written next to the app, or to `%LOCALAPPDATA%\AutoQuestGen`
if that folder is read-only. The build is unsigned for this beta, so Windows SmartScreen
will warn on first run; the release notes carry a SHA-256 you can check.

**Option B — from source.** See [Run from source](#run-from-source).

## Quick start

The first launch opens a guided setup window that walks you through all of this. It is
also available any time from the **Guided setup** button next to the build buttons.

By hand instead:

1. **Setup tab** — pick your pack under *Detected instances*, or point *Mods folder* at
   your pack's `mods/` directory yourself.
2. Press **Scan mods** and let it finish. Large packs take a few minutes the first time;
   results are cached after that.
3. **Generate tab** — choose a quest count (start with *normal*) and press
   **Build Quest Book**.
4. Launch the game and open your quest book.

### Where the book goes

The output folder **is** the quests folder. The app writes `chapters/`,
`chapter_groups.snbt` and `data.snbt` directly into it, defaulting to your selected
pack's `config/ftbquests/quests` — exactly where the game reads them, so the default
needs no copying.

If you build somewhere else first (a good idea for a look before you commit), copy the
*contents* of that folder into `<your pack>/config/ftbquests/quests`, not the folder
itself.

> **Close Minecraft before building into a live pack.** FTB Quests keeps the book in
> memory and writes it back over your files on exit, so a build under a running game is
> silently discarded. The app checks for this and refuses rather than letting you lose
> the work.

## Run from source

```bash
git clone https://github.com/antidqrk/AutoQuestGen.git
cd AutoQuestGen
pip install -r requirements.txt
python autoquestgen.py
```

Python 3.11 or newer, on Windows. The GUI is tkinter, which ships with the standard
python.org installer. `requests` is only needed for the optional AI path and for
instance-metadata lookups; everything in the offline builder runs on the standard
library.

## Repository layout

| Path | What it is |
| --- | --- |
| `autoquestgen.py` | The whole application: jar scanner, quest designer, SNBT writer, tkinter GUI |
| `mod_progression.py` | Curated progression chains imported by the app |
| `moddb/chains_research/` | Per-mod researched progression chains |
| `moddb/guides/` | Ordering signal and notes distilled from community guides |
| `moddb/gating.json` | Cross-pack progression gates ("this must come before that") |
| `moddb/moddb.json` | Consensus mod-ordering rows measured from published packs |
| `moddb/design_rules.json`, `moddb/style_guide.json` | Book design and prose rules |
| `moddb/immersion_spec.json` | The immersion checks a generated book is measured against |
| `harvested_order.json` | Published-pack ordering facts read at runtime |

## What "beta" means

- The offline builder is the supported path. The AI path is experimental.
- Tested end to end against local CurseForge instances ranging from 6 to 338 jars, with
  every generated book validated against that pack's own jars.
- The exe is unsigned, and the project has had a release audit but not a formal legal
  review.

## Known limits

- **1.20.1 Forge focus.** Other Minecraft versions and loaders (Fabric, Quilt,
  NeoForge-only builds) are not supported in this beta. The scanner will read the jars,
  but the research data and all testing target 1.20.1 Forge.
- **FTB Quests must be installed** in your pack for the book to appear. This project is
  independent and is not affiliated with or endorsed by FTB or Mojang.
- Mods that compose display names at runtime, or that ship broken item textures, may get
  fewer quests than they deserve.
- Very large packs (300+ mods) can take a few minutes to scan.

## Credits

- Created by **antidqrk**
- Built with **Claude (Anthropic)** — engineering, QA, and the research pipeline

## License

**MIT.** Use it, fork it, ship books made with it commercially — no obligations back.
See [LICENSE](LICENSE).

The grant covers this app's own code and the research data it ships. It does not extend
to Minecraft, to FTB Quests, or to any mod you point it at — those belong to their
authors, and this app neither contains nor redistributes any of them. It reads the jars
already installed on your own machine and writes config files you own.
