# MLB EDGE — Pre-Game Audit, 2026-04-30

*Generated ~1h30m before first pitch (DET @ ATL, 12:15 PM ET).*
*Bash sandbox unavailable for this run (no-space-on-device); pipeline outputs were read directly from disk artifacts (`picks_2026-04-30_diag.csv`, `parlay_2026-04-30.txt`, `picks_2026-04-30_news_overrides.csv`) and cross-referenced against Savant CSVs, the anchor file, and recent news overrides. The 72h pitch_logs parquet could not be opened without bash, so bullpen commentary leans on the "bullpen_short" flags already baked into the news overrides plus rotation-context priors.*

---

## DET @ ATL — 12:15 PM ET — Pick: ATL (p=66.8%, GOLD, Grade A-)

This is the cleanest matchup on the board and the model knows it. Bryce Elder (xERA 2.85, xwOBA-against .271, K-BB% running ~16% behind a 31% slider whiff rate) draws Framber Valdez (xERA 3.92, xwOBA-against .315, sinker still inducing 56.9% hard-contact but generating only 10.4% whiffs). The 1.07 xERA gap toward Atlanta translates to roughly +0.5 expected runs/9 of suppression — meaningful on a 12:15 day game in Truist where the ball stays in the yard. Elder's slider is the engine: 32.9% usage, .249 xwOBA-against, and a movement profile (-9 inches of vertical drop above league, sharp gyro shape) that has him punching out right-handers at a 31% clip. Valdez's signature sinker plays, but the 60.9-grade hard-hit rate against it is a tell that quality contact has come easy on the ground all month.

The Atlanta lineup is the structural advantage. Drake Baldwin (.395 xwOBA, top-3 among catchers leaguewide), Matt Olson (.390 xwOBA, .615 SLG), Ronald Acuña Jr. (.390 xwOBA in his return ramp), and Ozzie Albies (.301 xwOBA but a .380 actual wOBA) give Elder a four-deep heart of the order that lives on damage. Detroit's offense has been gutted today: Javier Báez and Casey Mize are both freshly on the IL per the news overrides, and the 4-IL-placement burden away (Horn, Seabold, Báez, Mize) is exactly the structural drag that justified the news-rules +3.0pp shove toward ATL. ATL's own IL hits (Iglesias, Dodd) cost a leverage reliever and a swing arm, but Elder's expected length plus the depth of the Atlanta pen makes that a manageable -1.8pp.

Bullpen state: neither flagged as short. Stage 1 and Stage 2 model probs are within 0.03 — total agreement, +1 grade boost. The F3 swing-take signal fires (599 gap), the SP edge agrees, the news rules agree, and the lineup imbalance is severe. There is no counter-signal here worth respecting.

**Verdict: Lock.** Highest-conviction read on the slate.

---

## COL @ CIN — ~1:10 PM ET — Pick: CIN (p=50.7%, GOLD, Grade C)

Andrew Abbott (xERA 5.52, xwOBA-against .366, ERA 6.59) versus Michael Lorenzen (xERA 5.55, xwOBA-against .367, ERA 5.96). On paper, this is one of the worst pitching matchups of the day in either direction — Abbott's four-seam grades out essentially league-average for movement (-0.4 inches vertical, -0.1 inches arm-side), and his secondary mix has not produced a usable swing-and-miss pitch through the first month. Lorenzen carries decent ride (16.8 inches induced break, +2.1 inches arm-side tail) but allows hard contact in 51%+ of plate appearances. The 0.03 xERA edge is statistically a wash; the model takes CIN almost entirely on park, lineup, and home-field framing.

Cincinnati's bats are the reason this lands GOLD. Elly De La Cruz (.393 xwOBA, .411 actual wOBA, .590 SLG) is the marquee threat, with Matt McLain (.337 xwOBA), Spencer Steer, and Sal Stewart (.416 xwOBA in limited PA) flanking. Eugenio Suárez landing on the IL is a real loss — that's a middle-of-the-order .500-SLG bat off the card — and the news rules already debited 1.2pp for it. Colorado answers with a flat lineup whose top wOBA contributors are mostly mid-.330s; the road environment hurts them more than the GABP humid-air boost helps Reds pitching.

The F3 swing-take gap is enormous (2,277.5) — the largest on the entire slate — and that is what carries this through to GOLD despite the Stage-2 prob barely scraping 50.7%. Counter-signal: this is the slimmest probability on the board for any non-SKIP pick, the SP edge is essentially zero (so no +2 boost in the parlay grader), and the parlay file already filters this to "DO NOT PARLAY" with a Grade C. Translation: the model loves the ticket as a single-game lean but won't risk it on a multi-leg.

**Verdict: Pass on the parlay leg, Lean on a stand-alone if you must play it.**

---

## WSH @ NYM — ~1:10 PM ET — Pick: WSH (p=62.0% Stage-1, but blended only 38.0% before override → 62.0% post p_model, SKIP, Grade D)

This is the sharpest disagreement on the slate, and the audit grade is appropriately punitive. Miles Mikolas (xERA 5.45, xwOBA-against .364, ERA 8.49 — yes, eight) toes the rubber for Washington against Freddy Peralta (xERA 3.55, xwOBA-against .301, K rate north of 23%). On stuff alone NYM should be a heavy favorite: Peralta's four-seamer plays at 93.8 with +1.6 inches of ride above league average and an .280 xwOBA against, and his 23.2%-usage changeup has a .203 xwOBA-against — a bona fide put-away weapon. Mikolas, by contrast, is throwing a 92.7 sinker with -2.0 inches of drop (worse than league) and a movement profile in the bottom 18th percentile of vertical break — he's pitching to contact with a defense behind him that is being asked to do too much.

So why is the pick WSH? Two reasons embedded in the diagnostics. First, Stage 1 (the F5 model) prints WSH 53.6%, but the full-game blend then collapses to 37.98% — and the post-news p_model is 62.0%, which is the artifact of the news override snapping back. Francisco Lindor and Kodai Senga both hitting the IL at NYM is structural: Lindor was the offensive engine, and even though Peralta starts (not Senga), the Senga IL placement signals rotation strain that bleeds into the bullpen profile this week. Second, the Stage-1/Stage-2 disagreement is large — well outside the 10pp comfort band — which is precisely why the parlay grader docks this all the way to D. The SP edge is 1.90 xERA *against* the pick (-2 grade), the F-signal does not fire, and the model itself flags this as SKIP.

**Verdict: Fade the parlay treatment entirely. If you have a personal read on the news, fine — but the audit machinery is screaming "do not touch."**

---

## HOU @ BAL — ~1:35 PM ET — Pick: HOU (p=54.1%, SKIP, Grade B+)

Lance McCullers Jr. (xERA 4.94, xwOBA-against .349, ERA 6.75) versus Chris Bassitt (xERA 6.23, xwOBA-against .385, ERA 6.75 with deeply ugly underlying numbers). Bassitt's four-seam is being thrown 91.1 mph with -4.6 inches of vertical drop relative to league — that is bottom-1% movement, and it shows up in the .385 xwOBA-against. He's a contact-manager whose contact has stopped being managed. McCullers is no ace this year, but his curveball still misses bats and the 1.29 xERA edge to Houston is one of the cleanest stuff disparities on the slate.

The lineup math swings further toward Houston. Yordan Alvarez (.535 xwOBA, .497 actual wOBA, .823 xSLG — the best hitter not named Judge in baseball right now) and Alex Bregman (.334 xwOBA) anchor a lineup that hits left-handed pitching and right-handed sinker-ball pitching about equally well. Baltimore counters with Gunnar Henderson (.326 xwOBA — a down month from his career baseline) and a top-heavy lineup that has been spotty at sustaining innings. Dean Kremer hitting Baltimore's IL chips at rotation depth (-1.2pp news), while HOU loses Nick Allen and Taylor Trammell — both bench/glove pieces, no structural impact.

So why SKIP at Stage 2? The Stage-2 full-game blend prints only 45.94% on HOU, and the news/p_model pulls it back to 54.06% — that's a Stage 1/2 disagreement of about 5pp (within tolerance, +1 in the grader), but the underlying model isn't fully bought in. The parlay grader gets it to B+ on the strength of the SP edge alone. There's no F-signal here, which is what keeps it out of A territory.

**Verdict: Lean. Strong stretch leg for a 3- or 4-leg parlay.**

---

## TOR @ MIN — ~2:10 PM ET — Pick: TOR (p=54.6%, GOLD, Grade B+)

Kevin Gausman (xERA 2.96, xwOBA-against .276, splitter generating 39.3% whiff and a .219 xwOBA-against — one of the genuinely elite secondary pitches in the AL) draws Bailey Ober (xERA 3.87, xwOBA-against .313). The 0.91 xERA edge to Toronto agrees with the pick (+2). Gausman's four-seam plays 93.8 with a 51.5% usage rate; the .263 xwOBA-against and 22.8% whiff aren't sexy on their own, but layered with the splitter they create the K-BB% gap that drives his suppression profile. Ober is a vertical-break specialist — 17.6 inches of induced ride is genuinely elite — and he keeps the ball in the air, which is a problem against the Toronto lineup he's about to face.

Toronto's offense skews hard-contact, contact-rate. Vladimir Guerrero Jr. and Bo Bichette (.321 xwOBA in a slow start — projected to bounce) anchor; Daulton Varsho's defense doesn't show up in this read but his bat is healthy. The TOR lineup gets a small assist from Minnesota's IL hits — Mick Abel and Garrett Acton are bullpen pieces, but the news rules also flag a *short MIN bullpen*, which is the second piece worth dwelling on. With Ober historically running a 5–5.5 IP/start average, this becomes a game decided in innings 6 through 8 by a thinned MIN pen against a TOR lineup that grinds. Max Scherzer hitting TOR's IL is a future-game concern, not today's.

Stage 1 prints 32.2% (TOR away from a Stage-1 perspective) but Stage 2 climbs to 45.4% and p_model lands at 54.6% — the Stage 1/2 gap is well outside 10pp. That is exactly the dynamic the grader penalizes (no Stage-1 boost), which is why this earns B+ rather than A-. The F3 swing-take signal does fire (1,535.6) and contributes the GOLD tier.

**Verdict: Lean. Best B+ leg on the board because the bullpen-short flag and the Gausman edge stack.**

---

## STL @ PIT — ~6:40 PM ET — Pick: PIT (p=54.0%, GOLD, Grade B)

Paul Skenes (xERA 1.93, xwOBA-against .223 — top-3 in baseball) gets the home start against Hunter Dobbins (the Cardinals' #5/swingman with limited Statcast sample). Skenes is the league's most dominant arsenal: a 97.5-mph four-seam with -2.8 inches of vertical drop relative to league (i.e., flatter, deceptive ride from a low slot) and a 64-percent-tail sinker that has produced .215 xwOBA-against, plus the Sweeper grading 99th percentile in horizontal break. There is essentially no SP at-bat in baseball right now that you'd take a hitter into less willingly. The xERA edge to PIT against an unknown is hard to quantify cleanly because Dobbins doesn't have a 130-PA reliable xERA on file, but the implied gap is comfortably north of 1.5 in PIT's favor.

The reason this lands at only Grade B (and not A) is the *bullpen* flag — the news overrides mark BOTH bullpens as short. PIT's pen has been worked the prior two days; STL's even more so. In a Skenes start that should mean nothing because Skenes goes 6-7 innings, but if anything goes sideways in the 7th or 8th, both managers are dipping into mop-up arms in a one-run game. The Stage 1/2 gap is also widest of the GOLD-tier picks on this slate — Stage 1 prints PIT 50.1%, Stage 2 prints 54.0%, p_model 54.0%. No major IL hits either way.

The lineup story is muted: STL's top-of-order is wOBA-average (Wetherholt .362, Herrera .401 in limited PA, Burleson .380); PIT counters with a thin lineup whose value sits with Bryan Reynolds (.344) and Oneil Cruz (.374) and falls off quickly. The F3 swing-take signal is huge (772.3) and is doing real work to push this over the GOLD threshold.

**Verdict: Lean. Skenes is the talent edge but the parlay file deliberately leaves this off the recommended ticket — respect that. Single-game lean only.**

---

## SF @ PHI — ~6:40 PM ET — Pick: PHI (p=55.9%, GOLD, Grade A-)

The other anchor leg. Cristopher Sánchez (xERA 3.28, xwOBA-against .290, changeup at 50% whiff and .184 xwOBA-against) versus Logan Webb (xERA 4.41, xwOBA-against .332). Sánchez's changeup is the engineering marvel of this matchup: 35.5% usage, .256 xwOBA-against, and a 50% whiff rate that ranks top-3 leaguewide on a high-volume secondary. His sinker plays 91 with the elite 49-percentile horizontal tail working the back-foot zone vs. RHB. Webb is still a strike-thrower with the kitchen sink, but his four-seam at 92.6 plays with the worst movement profile of his career (-4.4 inches vertical relative to league, 1st percentile for arm-side movement on the four-seam — note he barely uses it for that reason). Webb survives on his sinker and changeup, and his .332 xwOBA-against tells you the survival has been thin.

Lineup edge favors PHI. Bryce Harper, Trea Turner (.293 xwOBA — slow start but the underlying contact remains), Kyle Schwarber (.369 xwOBA, .505 SLG), and Alec Bohm form a four-bat core that mauls right-handed pitching of Webb's profile. SF's lineup has been carried by Heliot Ramos and Matt Chapman; the rest is back-end-of-the-order quality. JT Realmuto on the IL is a notable framing/leadership loss for PHI — handled with a -1.2pp news debit — and Daniel Susac on SF's IL is a wash. Neither bullpen is flagged short.

The grader prints A- here for the same reasons as DET @ ATL: F-signal fires (1,449.1 swing-take gap), the SP edge agrees (+2), Stage 1 (65.4%) and Stage 2 (55.9%) are within tolerance though noisier (+1 for agreement, but the 9.5pp gap is right at the line).

**Verdict: Lock. Pair this with ATL as the 2-leg core.**

---

## ARI @ MIL — ~7:40 PM ET — Pick: MIL (p=64.4%, SKIP, Grade B+)

Brandon Woodruff (xERA 3.05, xwOBA-against .280, four-seam playing 92.5 with +1.3 inches of ride above league) hosts Michael Soroka (xERA 4.59, xwOBA-against .338, four-seam 93.9 mph but with -1.4 inches of *worse* vertical break than league average and a hard-contact profile that has gotten worse over the past 30 days). The 1.54 xERA edge to MIL agrees with the pick. Woodruff in 2026 is a different shape than peak Woodruff — fewer four-seams, more sinker — but the .280 xwOBA-against is real and the four-seam still wins up in the zone.

The lineup question is whether Milwaukee can punish Soroka enough to make a 64% home win the right number. Christian Yelich, William Contreras (.390 xwOBA), Jackson Chourio, and Sal Frelick produce a balanced left/right attack, and Soroka's four-seam-deficient profile is exploitable. Arizona counters with Corbin Carroll, Ketel Marte, and Eugenio Suárez — wait, Suárez is on CIN now in 2026. ARI's offense centers on Carroll and Marte, with Lourdes Gurriel as the third pillar. The Angel Zerpa IL placement on MIL is a left-handed bullpen piece — small (-1.2pp) and already debited.

Why SKIP at Stage 2? Stage 1 prints MIL 57.5%, Stage 2 64.4% — agreement well within tolerance (+1). But the post-news p_model lands exactly at 64.4% with a slight news drag, and the Stage-2 model still flags this SKIP because the EV math gets pinched — the implied price after news adjustment is already short of breakeven. The grader still likes the SP edge enough to call it B+. No F-signal fires.

**Verdict: Lean. Reasonable 4-leg stretch piece; do not single-bet.**

---

## KC @ OAK — ~9:40 PM ET — Pick: OAK (p=66.5%, SKIP, Grade B+)

Closing the slate, the largest SP edge of the day: Jeffrey Springs (xERA 2.92, xwOBA-against .274, four-seam 91.3 with +51-percentile arm-side run, changeup with 48.1% whiff and .198 xwOBA-against — easily his best secondary) versus Noah Cameron (xERA 6.98, xwOBA-against .403, ERA 5.13 with the underlying numbers screaming regression *upward*). The 4.06 xERA gap is the largest on the board by a country mile and is the single biggest reason the parlay grader has this at B+ even with a SKIP tier flag. Cameron's four-seam plays at 91.8 with league-average movement; the issue is command and a secondary mix that hasn't found a put-away pitch — his .576 xSLG-against means hitters are squaring him up consistently.

Lineup: OAK's offense leans on Tyler Soderstrom, Lawrence Butler, JJ Bleday, Jacob Wilson, and Shea Langeliers (.408 xwOBA — a real weapon in the 5-hole). KC has Bobby Witt Jr. (.349 xwOBA — slow start, pedigree reads higher) and Vinnie Pasquantino (.371 xwOBA), but the 3-9 falls off into a contact-only profile that won't punish Springs' arsenal. The KC bullpen is also flagged short, which the news rules already debited at +1.5pp toward OAK. OAK loses Denzel Clarke and Max Muncy (the *Athletics* Max Muncy, 1B/3B) — Muncy is a real bat off the lineup, -1.8pp; KC loses Jonathan India (-1.2pp away).

Why SKIP at Stage 2? Stage 1 prints 66.3% and Stage 2 prints 66.5% — *literally* identical (Δ=0.00, +1). The F-signal does not fire. The SKIP flag here looks like a price/EV constraint rather than a model disagreement: at -200+ implied odds, even a 66.5% true probability nets minimal EV after vig, so the Stage-2 stake gate snaps it shut. The grader cares about the structural truth, not the price, which is why this lands B+.

**Verdict: Lean. The best SP edge of the day, but understand it's an EV-gate skip, not a probability skip — fine as a parlay piece, do not single-bet at chalk.**

---

## Aggregate summary

Slate size: 9 games. Picks distribution: 4 GOLD (DET@ATL, STL@PIT, SF@PHI, COL@CIN, TOR@MIN — actually five), 4 SKIP (HOU@BAL, WSH@NYM, ARI@MIL, KC@OAK). Of the GOLD set, two earn A- (DET@ATL, SF@PHI), two earn B (STL@PIT) and B+ (TOR@MIN), and one earns C (COL@CIN). Of the SKIP set, three earn B+ (HOU@BAL, ARI@MIL, KC@OAK) and one earns D (WSH@NYM).

Positive-edge picks (where SP xERA edge agrees with the pick, ≥0.30): DET@ATL (+1.07), HOU@BAL (+1.29), SF@PHI (+1.13), ARI@MIL (+1.54), KC@OAK (+4.06), TOR@MIN (+0.91). That's six legs where the pitching matchup independently confirms the model — exactly the conditions where parlay variance is best contained.

Negative-edge / counter-signal picks: WSH@NYM (-1.90 against the pick — the model is fading the better SP, structurally driven by Lindor IL and the news override). COL@CIN is essentially zero edge (+0.03) — a wash where the model is leaning on park, lineup, and framing rather than stuff.

Edge-band exclusions: the parlay grader filters anything outside [-5, +15] pp on `edge_pp`. The diagnostic CSV shows blank `edge_pp` columns for every pick today (the edge column wasn't populated by this run — likely because the line/anchor decimal hasn't moved meaningfully since the anchor capture, or because line data is upstream-stale). All nine games show "filtered" in the parlay file, meaning none made it through the edge-band gate based on price. That makes the grade-based logic the dominant filter today.

Biggest disagreements between Stage 1 and Stage 2: WSH@NYM (Stage 1 53.6% → Stage 2 37.9% → p_model 62.0% — wild news swing); TOR@MIN (Stage 1 32.2% → Stage 2 45.4%, ~13pp gap); HOU@BAL (Stage 1 51.2% → Stage 2 45.9%, 5pp gap, both same direction).

## Things that could change before first pitch

Lineup scratches are the biggest unresolved variable on a noon-ET first-pitch day. Watch ATL's lineup card for Acuña Jr. usage (he's been DH-ing on getaway days) — a scratch costs ~1pp from the pick. Watch BAL for whether Henderson is in the lineup; he's been day-to-day with a lower-body issue. Watch TOR for Vlad's slot; if he drops to the bench it materially erodes the TOR lean. Weather: a wind-out forecast in CIN would push the COL@CIN total higher and indirectly help CIN's offense disproportionately. Line moves: the news_overrides file has zero bps of line movement recorded — an early steam toward DET, BAL, NYM, or MIN before noon ET would warrant downgrading the corresponding pick by one tier. The MIN bullpen-short flag and the KC bullpen-short flag are particularly sensitive to the closer being declared "available" or "unavailable" pre-game; check the official notes 60 minutes before first pitch.

## Audit-graded parlay recommendation

The parlay_builder.py output recommends three constructions:

**2-leg (joint p ≈ 37.4%)** — DET @ ATL (ATL, A-) + SF @ PHI (PHI, A-). This is the safest ticket on the slate and the one with the cleanest grade pedigree. Both legs F-signal-fire, both have SP edges agreeing, both Stage 1/2 within tolerance.

**3-leg (joint p ≈ 24.9%)** — Add KC @ OAK (OAK, B+). The B+ comes from the largest SP edge on the slate (+4.06), and the KC bullpen flag stacks on the front-end pitching gap. This is the second-highest-conviction stretch leg available.

**4-leg (joint p ≈ 16.0%)** — Add ARI @ MIL (MIL, B+). The 1.54 SP edge is real and Woodruff's profile travels well at home. This is where variance starts to dominate payout — the parlay rules explicitly cap at 4 legs for that reason.

Per the guidance section of the parlay file: a 4-leg requires either all A/A- legs *or* 3 anchors + 1 B+. Today only 2 anchors exist, so technically the 4-leg violates the strict rule (2 anchors + 2 B+). The 3-leg construction (2 anchors + 1 B+) is the build that matches the published policy.

## Final read

The single highest-conviction read on the slate is **DET @ ATL → ATL**. Elder over Valdez, Atlanta's lineup over a Detroit lineup that just lost Báez and Mize, and a clean grade A- with every audit gate cleared. The second-strongest read is **SF @ PHI → PHI** — Sánchez's changeup is the best individual pitch in either matchup today, the lineup edge is real, and the grade is identical. What to actively avoid: **WSH @ NYM**. The pick is WSH but the SP edge is 1.90 *against* the pick, the Stage 1/2 disagreement is the largest on the board, and the grader has already flagged this as a D — do not buy news-override probability when the underlying stuff says NYM. If you play one ticket, take the 2-leg parlay (ATL + PHI). If you stretch to three, add OAK (the SP edge is undeniable). Skip the 4-leg unless the line moves create a price you cannot resist on MIL.
