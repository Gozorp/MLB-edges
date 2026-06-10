# PitcherList probable-SP extraction — 2026-06-10 (~02:40 PT)

Source: pitcherlist.com/teams/<team>/ "Starting Pitchers / Projected Starts" widget,
extracted per user request **without the MLB API**. Collected via rendered browser
pages (hydrated) + in-page fetches. Untracked working note; not published.

## ⚠ Source reliability verdict (read first)

PitcherList /teams/ proved **internally inconsistent request-to-request** during
this extraction — three observed flips within ~30 minutes [E]:

| Team | Fetch A | Fetch B |
|------|---------|---------|
| CHW Davis Martin | raw fetch: TBD | rendered page (2x): **Wed 6/10** |
| CLE Parker Messick | raw fetch: **Wed 6/10** | rendered page (later): TBD |
| MIL Brandon Sproat | fetch 1: **Wed 6/10** | fetch 2 (20 min later): TBD |

Their raw HTML literally contains `<td> TBD </td>` where the rendered page shows a
date, with **no data XHR** — content varies by request path/backend snapshot.

Additionally, the widget **omits 4 of tonight's statsapi-announced starters
entirely** (not even listed in the team's SP table): Jake Bennett (BOS),
Max Scherzer (TOR), Austin Warren (NYM — opener type), Jack Perkins (ATH).
It is a fantasy **rotation tracker**, not a probables/scratch feed.

**Conclusion:** unsuitable as primary truth for today-probables or late scratches;
usable only as a secondary eyeball for rotation ORDER and next-few-days intent.
The premise "PL catches scratches the MLB API misses" fails on this slate — the
reverse held: last night's diag (statsapi) names starters PL doesn't list.

## Tonight's slate — diag (statsapi, last night) vs PitcherList

| Game | Diag away / home | PitcherList signal | Agreement |
|------|------------------|--------------------|-----------|
| BOS @ TB | Bennett / Rasmussen | Bennett NOT LISTED; Rasmussen TBD | PL blind on BOS |
| NYY @ CLE | Rodón / Messick | Rodón TBD; Messick 6/10↔TBD (volatile) | weak agree |
| WSH @ SF | Griffin / Ray | both listed, TBD | agree (undated) |
| CIN @ SD | Singer / King | Singer TBD; **King 6/10** | agree |
| SEA @ BAL | Kirby / Young | Kirby TBD; **Young 6/10** | agree |
| LAD @ PIT | Ohtani / Jones | both listed, TBD | agree (undated) |
| ARI @ MIA | announced; thin-data pending (Gusto <100 pitches) | Gusto TBD listed | agree (undated) |
| PHI @ TOR | Luzardo / Scherzer | Luzardo TBD; Scherzer NOT LISTED | PL blind on TOR |
| STL @ NYM | Pallante / Warren | Pallante TBD; Warren NOT LISTED | PL blind on NYM |
| TEX @ KC | Gore / Lugo | both listed, TBD | agree (undated) |
| CHC @ COL | Imanaga / Lorenzen | both listed, TBD | agree (undated) |
| MIL @ OAK | Sproat / Perkins | Sproat 6/10↔TBD; Perkins NOT LISTED | weak agree / blind |
| HOU @ LAA | Lambert / Detmers | both listed, TBD | agree (undated) |
| MIN @ DET | **PENDING** (MIN side) | MIN: no 6/10 row; Matthews → **Thu 6/11** | both unknown |
| ATL @ CHW | **PENDING** (CHW side) | CHW: **Davis Martin Wed 6/10** (rendered 2x); Kay → Thu 6/11 | PL has a name |

## Actionable cross-checks vs our [PROJ] badges (display-only)

- **CHW (vs ATL): PL projects DAVIS MARTIN tonight, Kay Thursday.** Our
  rotation+rest heuristic badge shows Kay (5d, high). PL's beat-informed slot
  disagrees → treat the badge as suspect tonight. No action under the freeze:
  the game stays SKIP either way and the badge auto-clears when the real
  probable posts. (PL volatility caveat applies even here.)
- **MIN (@ DET): PL slots Matthews TOMORROW (Thu 6/11)**, no name for tonight
  (Bradley TBD). Our badge says Matthews tonight (5d, medium) — possibly a day
  early. Same non-action: display-only, auto-clears.
- MIL→Sproat: yesterday's [PROJ] projection is tonight's CONFIRMED starter in
  the diag — a heuristic hit.

## Full PL rotation snapshots (volatile; date = PL "projected start", /2026 dropped)

AL East — BOS: Gray TBD, Suarez 6/12, Early 6/13, Tolle TBD · TB: Rasmussen TBD,
McClanahan 6/12, Jax 6/13, Martinez TBD · NYY: Cole TBD, Rodón TBD, Weathers 6/12,
Warren 8/29(!), Schlittler 6/13 · BAL: Bradish 6/11, Baz 6/12, Rogers TBD,
Young 6/10, Gibson TBD · TOR: Gausman 6/13, Corbin TBD, Yesavage 6/12, Miles TBD

AL Central — CLE: Williams TBD, Cecconi TBD, Messick TBD*, Bibee 6/12,
Cantillo 6/13 · CHW: Martin 6/10*, Fedde TBD, Kay 6/11, Burke 6/12, Sandlin 6/13 ·
DET: Valdez TBD, Montero 6/11, Madden TBD, Flaherty 6/12, Melton TBD ·
KC: Lugo TBD, Wacha 6/11, Avila TBD, Cameron 6/13 · MIN: Ryan 6/12,
Prielipp 6/13, Bradley TBD, Matthews 6/11

AL West — HOU: Arrighetti TBD, Teng TBD, Lambert TBD, Imai 6/12, Burrows 6/13 ·
LAA: Soriano 6/13, Rodriguez TBD, Ureña TBD, Detmers TBD, Aldegheri TBD ·
ATH: Springs TBD, Ginn TBD, Jump 6/13 · SEA: Gilbert TBD, Kirby TBD, Woo 6/11,
Miller 6/12, Hancock TBD · TEX: deGrom 6/13, Eovaldi TBD, Gore TBD, Rocker 6/11,
Leiter 6/12

NL East — ATL: Sale TBD, Pérez 6/11, Strider 6/12, Elder 6/13, Holmes TBD ·
MIA: Alcantara 6/13, Gusto TBD, Meyer TBD, Phillips 6/11 · NYM: Peralta TBD,
Scott TBD, McLean 6/12, Manaea 6/13 · PHI: Sánchez TBD, Wheeler TBD, Luzardo TBD,
Painter 6/12, Nola 6/13 · WSH: Cavalli 6/13, Griffin TBD, Littell 6/12,
Cornelio TBD

NL Central — CHC: Cabrera 6/11, Imanaga TBD, Brown 6/12, Taillon 6/13, Rea TBD ·
CIN: Burns TBD, Paddack TBD, Singer TBD, Lodolo 6/12, Abbott TBD, Lowder 6/13 ·
MIL: Misiorowski 6/12, Harrison TBD, Crow TBD, Sproat TBD*, Gasser TBD ·
PIT: Skenes TBD, Jones TBD, Keller 6/11, Ashcraft 6/12, Chandler 6/13 ·
STL: Liberatore 6/12, McGreevy 6/13, May TBD, Leahy TBD, Pallante TBD

NL West — ARI: Soroka 6/13, Gallen TBD, Nelson TBD, Kelly 6/11, Rodriguez 6/12 ·
COL: Lorenzen TBD, Feltner 6/11, Freeland 6/13, Sugano TBD · LAD: Yamamoto 6/13,
Sheehan TBD, Lauer TBD, Ohtani TBD, Wrobleski 6/11, Sasaki 6/12 · SD: King 6/10,
Canning 6/12, Vásquez 6/13, Buehler TBD, Giolito TBD · SF: Webb TBD, Houser TBD,
Ray TBD, Roupp 6/12, McDonald 6/13

(* = value flipped between fetches during this session — see verdict.)
