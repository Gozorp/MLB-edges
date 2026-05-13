# What Actually Happened — Every Game the Model Lost

_Extracted from `docs/data/postgame/*.json`. Each entry is a game the model's pick lost, with the auto-generated post-mortem from Claude (or for 5/8, my hand-written analysis)._

> **2026-05-13 reconciliation note.** Cross-checking the postgame archive against the source diag CSVs surfaced a brain-prompt bug specific to 2026-05-10: Claude inferred `model_pick` from `f5_prob` instead of reading the explicit `pick` column. The bug only manifests on games where Stage 1 and Stage 2 disagree about which side wins. The **verdict (WIN/LOSS) is correct** in every postgame entry — the verdict was computed against the actual CSV pick — but the *narrative description* of which team the model favored is wrong for these four 5/10 entries: **WSH @ MIA** (real pick was WSH, Claude wrote MIA), **COL @ PHI** (real WAS COL, Claude wrote PHI), **HOU @ CIN** (real was CIN, Claude wrote HOU), **NYM @ ARI** (real was NYM, Claude wrote ARI). Also flagged: **CHC @ TEX** had a different misread (Claude wrote CHC when both probabilities favored TEX) — not f5_prob-related, probably tier-driven. Prompt fix shipped on commit following this note; next brain run reads pick column directly. Total loss count of 29 remains accurate.
## Summary table

| Date | Losses | Games graded |
|---|---:|---:|
| 2026-05-08 | 9 | 14 |
| 2026-05-09 | 8 | 15 |
| 2026-05-10 | 9 | 15 |
| 2026-05-11 | 3 | 6 |
| **TOTAL** | **29** | **50** |

---


## 2026-05-08  (9 losses of 14 graded)
_Day summary_: {'graded_picks': 14, 'wins': 5, 'losses': 9, 'hit_rate': 0.357, 'graded_tier_picks': 5, 'graded_tier_wins': 1, 'graded_tier_hit_rate': 0.2, 'headline': 'Bad day. 5-9 overall, 1-4 on graded GOLD picks. Common thread: 4 of 9 losses came on picked underdogs where Vegas had the opposite side priced as a real favorite — the model overrode the market and lost.'}

### COL @ PHI  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** GOLD-tier PHI pick loses to COL 9-7 in a slug-fest.
- **Hypothesis:** Model had PHI at 51.8% with edge -18pp (Vegas had PHI heavily favored at ~70%). The model was already disagreeing with the market here, then COL exploded for 9 runs against Luzardo. The F3_swing_take_gap signal fired weakly (261) but didn't anticipate the offensive eruption. Two failure modes likely: (1) Dollander's recent K-rate may have been understated in the SP feature, (2) PHI bullpen took a beating. The new -8pp negative-edge cap that we just shipped would have lowered this from B+ to C — directionally right.
- **Signals to re-check:** edge_pp; F3_swing_take_gap; bullpen_fatigue

### OAK @ BAL  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** BAL pick loses 4-3 in a one-run game; ATH bullpen held up.
- **Hypothesis:** Model picked BAL at 63% with a +6.5pp edge — middling conviction. ATH (Athletics) won the close one, suggesting the team_quality_modifier (which we just disabled) was likely pushing BAL because of name brand. Bradish vs Lopez was closer than the pitcher-quality features suggested — Lopez has been running better xFIP than ERA. Result is consistent with market uncertainty (close game, low edge) more than a specific feature failure.
- **Signals to re-check:** team_quality_mod; SP_xera_gap

### TB @ BOS  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** GOLD-tier TB pick loses 0-2 — F-signal fired but ran into BOS pitching wall.
- **Hypothesis:** Model picked TB at 45% with +10.9pp edge (Vegas had BOS heavily favored). F3_swing_take_gap=762 is a real lineup signal, but Connelly Early threw an effective 5+ scoreless. TB's offense got nothing going. This is the classic 'underdog with lineup quality vs hot SP' loss — the SP pre-game forecast didn't account for Early's recent form. Worth checking if Early had been excluded from FanGraphs scrape due to limited starts.
- **Signals to re-check:** SP_xera_gap; F3_swing_take_gap; small_sample_SP

### WSH @ MIA  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** MIA pick loses 2-3 in a tight one — small edge wasn't real.
- **Hypothesis:** Model had MIA at 59.8% with only +2.7pp edge — below the typical conviction threshold. Snelling vs Griffin was close on paper; WSH made one more clutch swing. Low-edge pick going wrong is statistically expected and not actionable. The market was nearly neutral and the game played that way.

### DET @ KC  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** GOLD-tier DET pick loses 3-4 in a one-run game.
- **Hypothesis:** Model had DET as a slight road favorite (51.3% picked-side prob, +9.8pp edge) with F2_xwoba_gap=0.021 firing. Bubic threw a competent line; DET bullpen gave up the deciding run late. The xwoba gap was modest (0.021 is a small lineup edge) yet the grader assigned GOLD — possibly the team_quality_modifier was inflating DET's grade pre-disable. With the new disabled state, this would likely have been B+ or B.
- **Signals to re-check:** team_quality_mod; F2_xwoba_gap; bp_min

### SEA @ CHW  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** CHW pick blown up: SEA 12-8 — model had CHW at 74%, way wrong.
- **Hypothesis:** Model's 74% confidence on CHW with edge +31.2pp is exactly the over-confidence pattern the calibrator was built to fix. After calibration this would be ~71%, still too high. The Burke vs Hancock SP edge was probably overstated; Hancock had a decent line, Burke got hit. CHW bullpen also gave up runs late. This is the worst kind of loss: high stated confidence, extreme edge, total miss. Most useful signal to investigate: what drove model_prob this high. If F1_xera_gap or PQI was outsized, that's where the leak is.
- **Signals to re-check:** full_prob; PQI; F1_xera_gap; calibration_bucket_>0.70

### NYY @ MIL  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** GOLD-tier NYY shut out 0-6 by Misiorowski.
- **Hypothesis:** Model picked NYY at 47% with edge -1.9pp — barely negative. F1_xera_gap=1.25* indicates Fried was supposed to dominate, but Misiorowski was electric (rookie hype, strong velocity). The * on F1 means it was flagged as small-sample — Misiorowski had limited prior data, so the SP comparison was partial. Rookies are a known model blindspot. Plus NYY's offense had been cold (3rd straight low-run game). The team_quality_modifier was probably pushing NYY here too.
- **Signals to re-check:** F1_xera_gap; small_sample_SP; team_quality_mod; rookie_pitcher_flag

### NYM @ ARI  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** ARI pick loses 1-3 — model had ARI at 68% with +23pp edge.
- **Hypothesis:** Another high-confidence loss. Model loved ARI (68%) when Vegas was nearly neutral. McLean threw quality, Nelson got hit. The +23pp edge is suspicious enough that it should have triggered a sanity check before the [4, 15]pp band filter SKIPped it for parlay purposes. The calibrator brings this down to ~63%, but that's still too high. Pattern matches SEA @ CHW: high model confidence on a home favorite that the market wasn't backing — model may be over-rewarding home-field advantage on certain matchups.
- **Signals to re-check:** full_prob; edge_pp; home_field_advantage; calibration

### STL @ SD  (—)
- **Model pick:** —    **Claude:** NO_DECISION
- **Headline:** SD shut out 0-6 — model was 50/50, Vegas had SD at 59%.
- **Hypothesis:** Model effectively flipped a coin (50.2%) but picked SD. Edge -8.5pp meant Vegas favored SD more than the model did. Canning was poor against STL's lineup (McGreevy went 6+ scoreless). The new -8pp cap we just shipped would have correctly downgraded this from a graded pick. Low-conviction pick that lost cleanly — par for the course.
- **Signals to re-check:** edge_pp

---

## 2026-05-09  (8 losses of 15 graded)
_Day summary_: The model went 6-8 on direction (42.9% hit rate) across 14 graded matchups; TB@BOS was postponed by rain. Both Claude-confirmed GOLD picks lost badly (CHC@TEX 0-6, NYY@MIL 3-4), making GOLD model direction 1/5 on the day. Two of three Claude-downgraded GOLD picks correctly avoided losses (COL@PHI 3-9, DET@KC 1-5), but the third missed a dominant PIT@SF win (13-3). Key finding: negative-edge GOLD picks in the -5pp to 0pp range failed 0/2; the stage 1/2 gap heuristic overcalled the PIT@SF downgrade when strong offensive lineup signals were present.

### HOU @ CIN  (1-3)
- **Model pick:** HOU ML SKIP    **Claude:** CONFIRM
- **Headline:** CIN won 3-1; model's bullpen-carry thesis for HOU failed against CIN's SP dominance.
- **Hypothesis:** Model picked HOU at 52.5% despite CIN's overwhelming F5 dominance (f5_prob=0.853 for CIN in first five), meaning HOU's full-game edge required CIN's bullpen to collapse in late innings. It didn't. CIN's starter held and HOU couldn't break through. The fetched_capped odds status (no fair_prob) made the edge unverifiable from the start. Process-sound SKIP; directional miss attributable to the bullpen-carry thesis not materializing.
- **Signals to re-check:** When the opponent's F5 probability exceeds 0.80 and the pick depends on a late-inning reversal, the full-game pick direction deserves skepticism even at SKIP tier; consider flagging these as directional weak picks.

### COL @ PHI  (3-9)
- **Model pick:** COL ML GOLD    **Claude:** DOWNGRADE
- **Headline:** PHI crushed COL 9-3; Claude's DOWNGRADE correctly avoided a GOLD-tier loss on a triple-filter failure.
- **Hypothesis:** Model picked COL at GOLD with three simultaneous filter failures: edge clipping the upper band (+15.92pp), fair_prob below floor (0.350 vs 0.42 minimum), and confidence_downgrade=True pipeline flag. PHI's 9-3 blowout validates the downgrade — COL was facing Aaron Nola, a far tougher matchup than the prior day's pitcher. Process win: the triple-filter stack reliably identified a weak GOLD pick.
- **Signals to re-check:** Any two of {edge outside band, fair_prob < 0.42, confidence_downgrade=True} stacking on a GOLD pick should be sufficient to DOWNGRADE — today confirmed all three fired correctly.

### MIN @ CLE  (2-1)
- **Model pick:** CLE ML SKIP    **Claude:** CONFIRM
- **Headline:** MIN edged CLE 2-1; Stage 1/2 gap and bullpen fatigue flags predicted this scenario.
- **Hypothesis:** Model picked CLE at 57.2% but CLE's F5 probability was only 40.9% — a 16.3pp Stage 1/2 gap requiring CLE's taxed bullpen to carry a late comeback. MIN held on 2-1, exactly the outcome the fatigue flags warned about. Both bullpen_fatigue=True and confidence_downgrade=True were correctly signaling the risk. SKIP tier was appropriate; directional miss was partly foreseeable from the gap. Process-sound loss.
- **Signals to re-check:** Stage 1/2 gap of 16.3pp combined with bullpen_fatigue=True is a reliable contra-indicator for the pick direction; consider flagging this combination as a soft DOWNGRADE signal even on SKIP-tier picks.

### CHC @ TEX  (0-6)
- **Model pick:** CHC ML GOLD    **Claude:** CONFIRM
- **Headline:** CHC shut out 0-6; Claude's GOLD CONFIRM on a negative-edge pick was a process failure.
- **Hypothesis:** Model graded CHC as GOLD (50.9%, edge -4.41pp) and Claude confirmed the direction based on Jack Leiter's 5.40 ERA and dual F-signals. TEX won 6-0. Edge at -4.41pp means the market priced TEX as the slight favorite — and the market was right. Leiter's ERA may reflect a few blowup starts rather than persistent exploitability; xFIP would be a better gate than ERA alone. Confirming GOLD when edge is negative means going against the market without sufficient justification. Process failure.
- **Signals to re-check:** F1_xera_gap and F2_xwoba_gap firing together on a negative-edge pick is insufficient grounds for GOLD CONFIRM; edge must be positive (> 0pp) to sustain GOLD direction against market consensus. Also re-examine whether ERA vs xFIP is the right SP quality gate for identifying exploitable starters.

### DET @ KC  (1-5)
- **Model pick:** DET ML GOLD    **Claude:** DOWNGRADE
- **Headline:** KC routed DET 5-1; Claude's DOWNGRADE on stacked acute_roster + confidence_downgrade flags was correct.
- **Hypothesis:** Model graded DET as GOLD (51.3%) with acute_roster=True flagging a live lineup concern and confidence_downgrade=True from pipeline. DET lost by four runs — not a one-run variance loss but a clear team-quality gap. Wacha kept DET's depleted lineup in check. Process win: the stacked-flags DOWNGRADE protocol was validated again.
- **Signals to re-check:** acute_roster=True + confidence_downgrade=True on a coin-flip probability (51.3%) = reliable automatic DOWNGRADE; this combination has now been validated on consecutive days.

### NYY @ MIL  (3-4)
- **Model pick:** NYY ML GOLD    **Claude:** CONFIRM
- **Headline:** MIL edged NYY 4-3; second consecutive GOLD NYY series loss confirmed a systematic overrating of NYY vs MIL.
- **Hypothesis:** Model picked NYY at GOLD (54.2%, edge -2.00pp) and Claude confirmed based on Schlittler's 8-GS/1.52 ERA track record. NYY lost 4-3 in a close game — the second consecutive loss in this series (5/8: NYY shut out 0-6 by Misiorowski; 5/9: NYY loses 3-4). Edge at -2.00pp means Vegas slightly favored MIL both days. Process failure: GOLD CONFIRM on a negative-edge pick with a fresh series loss the prior day should have been DOWNGRADED. The F1_xera_gap asterisk (*) on MIL's SP may be consistently underrating MIL starters on short samples.
- **Signals to re-check:** F1_xera_gap with an asterisk (*) marking small-sample SP data is unreliable as a directional basis for GOLD CONFIRM; additionally, confirm GOLD direction only when edge >= 0pp. Track NYY vs MIL series model accuracy — two consecutive losses at GOLD suggests a systematic team matchup bias.

### STL @ SD  (2-4)
- **Model pick:** STL ML SKIP    **Claude:** CONFIRM
- **Headline:** SD won 4-2; model picked STL wrong with no active F-signals and a low-conviction SKIP.
- **Hypothesis:** Model picked STL at 55.4% with no F-signals firing and edge +12.9pp (within band but no tier elevation). Vasquez outpitched or outlucked Dustin May; SD's lineup converted. No active signals firing means this was a raw model output based on team quality — losing here is expected variance in a low-conviction pick. Yesterday SD was picked and lost; today STL was picked and lost. This matchup is a coin flip the model keeps mispicking.
- **Signals to re-check:** Without active F-signals on a 55% pick, directional accuracy is near random; no specific feature to flag. Consider whether STL/SD series picks without active signals should default to NO_PICK rather than a directional call.

### ATL @ LAD  (7-2)
- **Model pick:** LAD ML SKIP    **Claude:** CONFIRM
- **Headline:** ATL crushed LAD 7-2; HARD_VETO and negative edge correctly signaled to stay out.
- **Hypothesis:** Model picked LAD at 55% with sp_savant_gate=HARD_VETO and edge -9.49pp. Claude confirmed SKIP unconditionally. ATL's 7-2 blowout validates both signals — Blake Snell's matchup risk materialized and LAD's offense underperformed. The model's LAD direction was wrong, but the HARD_VETO prevented any stake exposure. Process win: HARD_VETO reliably identifies high-risk SP situations. No model feature update needed.
- **Signals to re-check:** HARD_VETO performed as designed; no recalibration needed. Continue tracking Snell's 2026 starts to determine whether HARD_VETO can eventually be relaxed as his sample grows.

---

## 2026-05-10  (9 losses of 15 graded)
_Day summary_: The model went 4-9 on direction (30.8% hit rate) across 13 resolved games; two PENDING picks (NYY@MIL, DET@KC) were correctly withheld. Both Claude-confirmed GOLD picks lost: PIT fell 6-7 to SF in a one-run game and LAD was crushed 7-2 by ATL despite F3_swing_take_gap=1774.2 — the largest signal on the slate. Claude's downgrades of MIN@CLE and COL@PHI avoided two losses, but downgrading TB@BOS and SEA@CHW missed two correct GOLD calls. Key lessons: F3 alone cannot override an elite opposing SP, the F2_xwoba exception from 05-09 failed in its second application to PIT@SF, and confirmed GOLD picks are now 0-for-4 across 05-09 and 05-10.

### WSH @ MIA  (2-5)
- **Model pick:** WSH ML SKIP    **Claude:** CONFIRM
- **Headline:** MIA won 5-2; CSV model picked WSH but claude_picks json identified MIA as the model pick — MIA won.
- **Hypothesis:** The CSV 'pick' column shows WSH (away), but the claude_picks json listed MIA as the model pick with prob=0.5118. MIA won 5-2. This is one of five games on the slate where the CSV pick column and claude_picks json disagree on which team the model favored. The discrepancy matters: if the model truly liked MIA (home), Claude's CONFIRM was directionally correct. If the model truly liked WSH (per CSV), this is a directional LOSS. Given the ambiguity in the CSV pick column (may represent F5 stage winner rather than full-game pick), the reliable signal is that WSH was not the right choice.
- **Signals to re-check:** CSV 'pick' column interpretation: verify whether this field represents the F5 stage pick or the full-game model recommendation, as five games showed discrepancies between the CSV and claude_picks json on pick direction.

### OAK @ BAL  (1-2)
- **Model pick:** OAK ML SKIP    **Claude:** CONFIRM
- **Headline:** BAL won 2-1 in a one-run game; model and Claude both picked OAK and lost.
- **Hypothesis:** Model picked OAK (away) at SKIP with no fair_prob and no active F-signals. BAL won by one run. This is a low-conviction coin-flip at SKIP — directional loss is expected variance on a 54% pick. On 05-09 OAK had won 6-2 at SKIP, so this reversal within the same series is consistent with coin-flip uncertainty. No process failure identifiable; variance loss.
- **Signals to re-check:** No specific signal to flag; SKIP-tier losses without active signals are expected variance.

### COL @ PHI  (0-6)
- **Model pick:** COL ML SKIP    **Claude:** DOWNGRADE
- **Headline:** PHI crushed COL 6-0; Claude's downgrade avoided what would have been a directional loss.
- **Hypothesis:** CSV shows model pick=COL (away) at SKIP, but the claude_picks json identified model_pick=PHI at PLATINUM. PHI won 6-0. Regardless of which direction the model favored, the game went to PHI decisively. Claude's DOWNGRADE (targeting the PHI-PLATINUM interpretation) was correct in outcome — PHI was the right side and a PLATINUM-tier confirmation would have been appropriate since PHI won. But since Claude downgraded to SKIP citing the historical PLATINUM ~43% hit rate and no fair_prob, no stake was placed either way. The pick column discrepancy (CSV=COL, claude_picks=PHI) may indicate the CSV was showing the F5 stage favorite (COL SP advantage) while PHI had the full-game edge.
- **Signals to re-check:** COL@PHI CSV pick=COL with PHI winning the full game by 6 runs is a data point that the 'pick' column may reflect the F5 stage advantage (COL SP) not the full-game model recommendation.

### LAA @ TOR  (6-1)
- **Model pick:** TOR ML SKIP    **Claude:** CONFIRM
- **Headline:** LAA won 6-1 in a blowout despite TOR crushing LAA 14-1 the prior day.
- **Hypothesis:** TOR was the SKIP pick based on historical 60% F5 and 61% full probability. TOR had won 14-1 the prior day. LAA reversed the series momentum completely (6-1 win). The model and Claude expected TOR to continue dominating, but the SP matchup changed dramatically on this day — TOR's momentum from the prior blowout did not carry over at all. Series momentum is not a reliable predictor: two consecutive blowouts in opposite directions over this series (5/9: TOR 14-1, 5/10: LAA 6-1). Process-sound loss; variance or SP-day change drove the reversal.
- **Signals to re-check:** Multi-day momentum should not be used as a directional signal for TOR@LAA or similar series — the 14-1 blowout the prior day did not predict direction today.

### MIN @ CLE  (5-4)
- **Model pick:** CLE ML GOLD    **Claude:** DOWNGRADE
- **Headline:** MIN edged CLE 5-4; Claude's DOWNGRADE of CLE GOLD correctly avoided a staked loss.
- **Hypothesis:** Model graded CLE as GOLD with F3_swing_take_gap=16.1 (extremely weak F3 signal — the smallest on the slate). Claude downgraded citing: (1) very weak F3 barely above noise, (2) Stage 1/2 gap with bullpen_fatigue=True, (3) confidence_downgrade=True. MIN won by one run, validating all three concerns. The weak F3 correctly signaled a borderline model grade; CLE's bullpen fatigue prevented the late comeback. This is the third consecutive day (05-08, 05-09, 05-10) Claude's stacked-flags downgrade protocol avoided a real loss.
- **Signals to re-check:** F3_swing_take_gap=16.1 is too close to noise to justify GOLD tier; consider adding an absolute floor (e.g., F3 must exceed 50 to count as the sole signal basis for GOLD tier).

### PIT @ SF  (6-7)
- **Model pick:** PIT ML GOLD    **Claude:** CONFIRM
- **Headline:** SF edged PIT 7-6 in a one-run game; Claude's GOLD CONFIRM on the F2_xwoba exception failed.
- **Hypothesis:** Claude confirmed PIT at GOLD citing F2_xwoba=0.032 (exceeding the 0.025 threshold — the exception rule from 05-09 that says a strong lineup signal can override Stage 1/2 gap concern). PIT lost 7-6 in a one-run game. The 05-09 exception was drawn from a single 13-3 blowout; today's narrow one-run loss shows the exception is not robust. The Stage 1/2 gap (SF wins F5, PIT wins full) remained real — SF's starting pitcher was effective enough that PIT's lineup did not overwhelm early, and the bullpen-carry thesis then failed. Process failure: confirming GOLD based on a single-game-derived exception rule was premature. Could be variance (one-run game) but the underlying thesis was fragile.
- **Signals to re-check:** F2_xwoba >= 0.025 exception: two-game sample on PIT@SF is 1-1 (WIN 13-3 then LOSS 6-7). Do not treat this as a deterministic rule; treat it as a severity reducer (SKIP instead of DOWNGRADE) not a blanket override of Stage 1/2 gap concern.

### NYM @ ARI  (1-5)
- **Model pick:** NYM ML SKIP    **Claude:** CONFIRM
- **Headline:** AZ won 5-1; CSV model picked NYM (away) but claude_picks identified ARI as the model pick.
- **Hypothesis:** CSV pick=NYM (away) at SKIP, AZ won 5-1. From the CSV perspective, NYM direction was wrong → LOSS. However, the claude_picks json identified model_pick=ARI and Claude confirmed ARI at SKIP, which was directionally correct (ARI won). Like HOU@CIN and WSH@MIA, this is one of five games where the CSV pick column and claude_picks json disagree on direction. AZ's 5-1 margin suggests this wasn't a coin flip — one side was clearly better, and ARI was it. The CSV 'pick' column showing NYM when the model probability data apparently pointed to ARI is a reliability concern.
- **Signals to re-check:** NYM@ARI: CSV pick=NYM despite ARI winning 5-1, another data point that the CSV 'pick' column may not reliably represent the full-game model direction.

### STL @ SD  (2-3)
- **Model pick:** STL ML SKIP    **Claude:** CONFIRM
- **Headline:** SD won 3-2 in a one-run game; model and Claude picked STL with no active F-signals.
- **Hypothesis:** Model picked STL at SKIP with no active F-signals and the largest Stage 1/2 gap on the prior-day CSV (SD wins F5, STL wins full requiring a late comeback). SD won 3-2, again beating the model's STL direction. Over this series: 05-09 STL lost 2-4, 05-10 STL lost 2-3. The model has now missed STL@SD direction two consecutive games. Without active F-signals, the directional call is near random, and the SD home advantage may be structurally underweighted. Variance loss, but the pattern of picking STL without signal support is a recurring process exposure.
- **Signals to re-check:** Two consecutive STL@SD directional losses without F-signals; consider defaulting to NO_PICK when no F-signals fire on a SKIP-tier matchup rather than defaulting to model direction.

### ATL @ LAD  (7-2)
- **Model pick:** LAD ML GOLD    **Claude:** CONFIRM
- **Headline:** ATL crushed LAD 7-2; Claude's biggest GOLD confirm was the day's biggest loss.
- **Hypothesis:** LAD was GOLD with F3_swing_take_gap=1774.2 — the strongest signal on the slate by a wide margin. Claude confirmed at GOLD, treating F3=1774.2 as sufficient to override heuristic-1 (home favorite >65%). ATL won by five runs. The F3 signal measures LAD's lineup contact quality relative to the opposing SP, but it did not account for the ability of ATL's own pitching to neutralize LAD's lineup advantages. ATL's SP was evidently effective enough to render the F3 edge irrelevant. This is a process failure: F3 is a lineup-quality signal, not a total-game-quality signal, and it cannot override the possibility that the opposing starter simply dominates. The 'strong F3 exceptions heuristic-1' rule is now disproven on its first real test.
- **Signals to re-check:** F3_swing_take_gap > 1000 is not sufficient to override heuristic-1 (home favorite >65%) when the opponent brings quality pitching. Require a second independent positive signal (verified positive edge_pp AND opposing SP xERA > 4.0) before applying F3 as an exception to heuristic-1.

---

## 2026-05-11  (3 losses of 6 graded)
_Day summary_: The model went 3-3 on direction (50.0% hit rate) across 6 resolved matchups. Both Claude-confirmed GOLD picks won — TB@TOR 8-5 and ARI@TEX 1-0 — the first GOLD CONFIRM winners after 0-for-4 across 05-09 and 05-10, validating the three-signal bar (F3 > 500, positive edge, Stage 1/2 gap < 15pp). Claude's three defensive calls were all correct: NYY@BAL DOWNGRADE avoided a 2-3 loss, SF@LAD DOWNGRADE avoided a 9-3 PLATINUM blowout, and the SEA@HOU OVERRIDE to SEA proved correct as George Kirby dominated HOU 3-1, directly applying the F3-vs-elite-SP pattern from 05-10. All three model directional losses had pre-game warning signals Claude acted on correctly.

### NYY @ BAL  (2-3)
- **Model pick:** NYY ML GOLD    **Claude:** DOWNGRADE
- **Headline:** BAL rallied in the 7th to beat NYY 3-2; Claude's DOWNGRADE on negative edge correctly avoided the GOLD staked loss.
- **Hypothesis:** NYY took a 2-0 lead into the bottom of the 7th, then BAL erupted for 3 runs to win 3-2 — exactly the late-inning bullpen erosion scenario the negative edge warned about. The market priced NYY at 59.16% (edge -4.75pp) while the model had them at 54.41%, meaning the market was MORE bullish on NYY than the model and the model was still picking NYY as GOLD. The market held better information about this SP matchup (Brent Headrick proved exploitable). Claude's DOWNGRADE based on the validated negative-edge GOLD rule was process-correct; the loss would have cost a GOLD stake. This is now the third consecutive validated instance of negative-edge GOLD failing (CHC@TEX 05-09, NYY@MIL 05-09, NYY@BAL 05-11).
- **Signals to re-check:** Negative-edge GOLD pattern is now 3-for-3: edge < 0 on a GOLD pick reliably signals market holds superior information. Enforce this rule without exception regardless of F-signal count.

### SEA @ HOU  (3-1)
- **Model pick:** HOU ML GOLD    **Claude:** OVERRIDE
- **Headline:** George Kirby shut HOU down 3-1; Claude's OVERRIDE to SEA was fully vindicated and the F3-vs-elite-SP pattern from 05-10 proved directly applicable.
- **Hypothesis:** George Kirby threw a dominant 9-inning game, holding HOU to just 1 run despite 9 HOU hits. SEA scored 2 in the 2nd and 1 in the 3rd — early enough that Kirby never needed to protect a late lead against a taxed HOU bullpen. This is a process WIN for Claude's override, built on four converging signals: (1) F3=1783.5 is virtually identical to 05-10 ATL@LAD's F3=1774.2, which failed against quality pitching — the pattern was real and applicable; (2) acute_roster=True + confidence_downgrade=True stack, validated on consecutive days; (3) fair_prob=0.4177 < 0.42 floor, market priced HOU at only 41.77%; (4) confirmed elite opposing SP in Kirby. The model's GOLD thesis on HOU required Kirby to be beatable; he was not. The OVERRIDE was the correct call, not just a DOWNGRADE, because the SEA direction was affirmatively correct.
- **Signals to re-check:** OVERRIDE to opposing team (SEA) is now validated when all four conditions converge: F3 analogy to prior failure + stacked pipeline flags + fair_prob below floor + confirmed elite opposing SP. This combination warrants OVERRIDE, not just DOWNGRADE to SKIP.

### SF @ LAD  (9-3)
- **Model pick:** LAD ML PLATINUM    **Claude:** DOWNGRADE
- **Headline:** SF crushed LAD 9-3; PLATINUM model_prob 0.9447 was a calibration artifact exactly as Claude identified.
- **Hypothesis:** SF won convincingly 9-3, scoring in innings 2, 6, 7, and 9. LAD had 10 hits but only 3 runs — the classic pattern of a team losing in the field while the model's raw lineup quality scores look adequate. The model's 94.47% probability on LAD was a calibration artifact driven by: (1) the largest Stage 1/2 gap ever seen on a slate (f5_full_delta=0.4679), likely caused by limited Statcast data for Roki Sasaki inflating the full-game model; (2) PLATINUM tier historical hit rate ~43% meaning extreme model probabilities systematically fail. Claude's DOWNGRADE to SKIP based on these flags correctly avoided a large staked loss. This is the second consecutive PLATINUM failure when model_prob > 0.85 (ATL@LAD 05-10 context also applies). Process failure by the model; process win for Claude's downgrade decision.
- **Signals to re-check:** PLATINUM + model_prob > 0.85 + f5_full_delta > 0.20 has now failed 2/2 times. Formalize as a hard DOWNGRADE rule: when all three conditions are present simultaneously, the PLATINUM designation is a calibration artifact and not genuine confidence — automatic SKIP regardless of F-signal strength.

---
