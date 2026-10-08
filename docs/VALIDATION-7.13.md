# Release verification: app 7.13

The downloadable Windows app has file version `0.2.7.0` and SHA256:

```text
f05c1d27c7b9c1a2a1a94f2aa39215623297f98858db166ab13b308e7ae92c2c
```

Before installation and publication, the development regression suite passed **493 distinct tests**. The frozen public executable then passed these seven export cases against read-only installed pack files:

| Case | Quests | Chapters |
| --- | ---: | ---: |
| Small selected-mod campaign | 233 | 21 |
| Normal selected-mod campaign | 391 | 45 |
| Large selected-mod campaign | 536 | 51 |
| Focused Gaia Guardian route | 29 | 1 |
| Authored restoration scenario | 10 | 2 |
| Normal campaign with balanced shop | 419 | 46 |
| Classic artwork, motion off, builder shop, expensive prices | 405 | 46 |

The selected-mod examples scanned 444 installed ATM9 mods. Counts depend on content, density, scope and settings; they are observations, not guaranteed targets.

## Checks performed

- Unique object IDs, complete acyclic dependency graphs, item/model references, real Gaia encounter prerequisites, and campaign navigation.
- Exact hashes and dimensions for all 110 artwork PNGs, plus 12 correct native animation metadata files in companion exports.
- Art collection, motion, size, organization, inventory and price controls exercised across packaged cases.
- No live installed pack configuration, selected resource packs, or worlds changed by the generation checks.

## Direct Minecraft review

An isolated Minecraft **1.20.1 / Forge 47.4.23 / FTB Quests 2001.4.21** instance loaded a generated book containing eight chapters and 138 quests.

- All 138 runtime quest positions and sizes matched the export. The book used sizes 0.85, 1.0, 1.55 and 2.2.
- All 233 checked item/icon/reward references were registered; none were missing.
- All 98 static artwork images loaded through FTB from the companion resource pack.
- All 12 animations loaded 32 frames each. Sixteen real-time rendered samples showed 6–11 distinct frames per animation, while a static control remained unchanged. Captured frames matched the authored sheets. The world was unpaused, and textures were not manually ticked.
- Two purchases each consumed exactly two emeralds and awarded 32 arrows through FTB's actual submission and claim handlers. Offers reset, duplicate and unpaid claims awarded nothing, and insufficient payment remained a partial deposit with no reward. The test inventory and team progress were restored exactly.

The screenshots in this repository were captured from Minecraft's own framebuffer in that disposable instance. The checks did not use global desktop controls.

This validates the listed generation, rendering and transaction behavior. It is not a complete survival playthrough of every generated book or a claim to understand every mod version and scripted pack change.

## Standalone public source check

The sanitized public checkout discovered 493 unique portable cases: **492 passed and one optional installed-FTB-JAR integration check was skipped**, with no errors or failures. Tests ran with their temporary files on the checkout drive and network connections blocked in the main test process.

The public build procedure also passed: 560 runtime files staged, a Windows executable rebuilt locally, and that rebuilt executable exported a synthetic mod's recipe-goal book with five chapters and 28 quests. The actual goal and its recipe prerequisite were present; the companion ZIP contained all 110 artwork PNGs and 12 animation metadata files.

That source rebuild is separate from the original release executable identified above. The published download retains the executable tested in the seven installed-pack cases and direct Minecraft review.
