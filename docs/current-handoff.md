# Current Handoff

Last updated: 2026-09-07 (eighteenth pass)

## The Owner's Priorities

Stated 2026-09-02, and they are the primary goal of the project. Everything
else is subordinate to these five:

- **A. A reasonable visualization of the world, without crashes.**
- **B. Log in to the Second Life main grid, at the home location by default.**
- **C. Edit an object and extract all its internals.**
- **D. Upload a whole folder into an in-world selected object.**
- **E. Sync an object's internals to an external folder, so scripts can be
  worked on outside, then synced and tested easily.**

C, D and E are one track seen from three angles: get an object's contents out,
get a folder's contents in, and keep the two in step. D is the piece with real
missing code.

### Where each stands

**A -- and the soak found one on its first run (2026-09-07).** Two hours
against the quiet local region -- three prims and one avatar -- and of fifty
container gauges exactly one came back `growing`: `udp.seen_sequences`, at
1,415 entries over the two hours -- 671 an hour, at a rate that had not
fallen by the end. Every other container settled or went flat.

`LiveCircuitSession.seen_reliable_sequences` was a `set[int]` that nothing
ever took anything out of. Every reliable packet's sequence number went in and
stayed for the length of the session. It is the only container in this client
that grows with **every packet** rather than with the size of the world, which
is what makes it the one whose cost is measured in hours instead of in prims,
and why no amount of staring at a frame would ever have found it.

What it is *for* is recognising a resend. `udp/recent.py` keeps two sets and
swaps them when the newer fills -- eviction as one assignment rather than one
bookkeeping step per packet -- so what is remembered swings between one window
and two and never exceeds two. 8192 sequence numbers is tens of seconds of a
busy region's traffic, and both halves full measures 505 kB, which is the
whole point: it is a *bound*.

Sizing it needed a fact about the simulator, and the first version got that
fact half right. OpenSim retries an unacked reliable packet every RTO -- 1000
ms by default, capped at 3000, and never backed off, which is now pinned in
`test/test_opensim_source_pins.py` along with the clamp and the fact that
nothing else writes the timeout. So *consecutive copies* are seconds apart.
The chain as a whole is another matter: OpenSim gives up on an unacked packet
never. The sixty-second timeout that exists is measured from the last packet
the simulator *received*, so it fires when the viewer falls silent, not when a
packet goes unacked -- which means a viewer that is talking while its acks are
being lost can see copies of one sequence arriving for minutes. A window sized
to one RTO would forget it mid-chain and hand the same message up twice.

That is what `seen` is for: it answers and remembers in one call, so an
arrival always buys another window and the bound applies to how long an *idle*
sequence is kept. The receive path used to ask `in` and then `add`, which
looks equivalent and is not -- on a hit it returned early, so the `add` never
ran and nothing was ever refreshed.

**Which the tests only caught on the second try, and only because of a
mutant.** The first version put the refresh inside `add`, with a test that
added every arrival unconditionally. Planting the old behaviour left it
green: the test's own caller re-added on every arrival, so it never exercised
the path the shipped caller takes. The surviving mutant was the finding --
the refresh was dead code exactly where it mattered. The tests now drive
`seen` the way `handle_incoming` does, ask once and act on the answer, and
there is a session-level pair as well: a sequence that keeps arriving behind
a hundred others stays a duplicate, and one that stops arriving does not.
That second one is the control, because the first would pass just as happily
against the unbounded set this replaced.

The trade that is left is stated as a test rather than left in a comment: a
resend that arrives after a whole idle window is handled twice. On messages
that are near enough idempotent -- an object update re-applied says the same
thing -- that is the cheaper of the two failures, and it is the trade every
viewer makes.

**And a second one of exactly the same shape, found by looking rather than by
measuring, once the shape was known.** `wrap_lines` caches wrapped text keyed
by *the text*. The diagnostics panel is eighteen lines of which two -- the
framerate and the sim's own stats -- are a new string every refresh, once a
second, for as long as the panel is open. Those two can never be hit and were
kept anyway: three and a half thousand entries an hour, a hundred thousand
overnight, none of them reachable. It is emptied at `WRAP_CACHE_LIMIT` now,
and the bound lives in `wrap_lines` rather than in the caller, because that is
the only thing that writes to the dict and a promise about a container's size
that its one writer does not keep is not a promise.

This one is behind `--diagnostics` and so was never in a default session --
which is precisely why it needed finding by hand: the soak that would have
caught it is the soak nobody runs.

**Two things the report got wrong about itself, and both are now fixed
instruments rather than findings.** `eq.polls` counted event-queue *batches
decoded*, not polls made, so a quiet queue -- which long-polls and returns
nothing -- read as `stalled`, which is the report's word for a session that
has gone deaf. And `udp.acks_received` counted only `PacketAck` messages,
while acks reach this client two ways: as that message, or appended to the
tail of any packet. Reading one channel makes a busy circuit look silent.
There are now `eq.attempts` (every trip round the poll loop, answered or not
-- the one number that says the loop is still running) beside `eq.batches`,
and `udp.packet_acks` beside `udp.appended_acks`.

The lesson is the one this project keeps relearning in new costumes: **an
instrument that reads plausibly is not the same as an instrument that reads
correctly**, and the way to tell is to make it say something you can check.

**And a third thing the same report said, which is a gap rather than a fix.**
`udp.pending_reliable` came back `settled` at 6 -- first 0, peak 6, last 6.
Six reliable packets this client sent in two hours were never acknowledged,
and nothing ever tried again. `_build_outbound_packet` records
`pending_reliable[sequence] = label`: the label, not the bytes, so a resend
is not merely absent but currently impossible -- the packet is gone by the
time anyone could want it back. The only readers are the ack handler, which
pops, and the snapshot, which sorts the keys for display.

The simulator's half of this is now pinned: it resends *its* unacked packets
every RTO for as long as the client is talking, and gives up never. We do the
opposite. A lost `CompleteAgentMovement` or `AgentThrottle` strands the
session with no symptom other than a thing that never happens, which is the
hardest kind of bug to go looking for and the easiest kind to measure. The
shape of the fix is in the notes; the one thing it must not do is take a new
sequence number, because then the simulator sees two packets and this client
has invented the duplicate storm it spent today learning to survive.

**And the report was wrong about two of its own gauges, in a way only a
second reading of a real run could show.** `proc.rss_bytes` and
`proc.py_blocks` came back `settling` -- the word for a cache that filled and
is levelling off. Broken into thirds they are not:

    proc.rss_bytes    148 MB/h    24 MB/h    42 MB/h
    proc.py_blocks    259 k/h     46 k/h     50 k/h

The rate fell and then rose. What made the report say otherwise was the rule:
`settling` meant the second half grew less than half as much as the first,
and the first half of any run contains the startup burst. Nothing after that
burst can fail such a test. A process that spends a hundred and fifty megabytes
getting going and then leaks forty an hour for ever reads as settling for the
life of the session -- which is exactly the failure the whole instrument exists to
catch, arriving as a reassuring word.

The rule now throws the first third away and compares the middle stretch with
the last. Which was **also wrong**, in the opposite direction, and the same
run said so within the minute: with the first third gone,
`udp.seen_sequences` -- the flat leak this entry is about -- went 773, 681,
668 an hour and flipped to `settling`, because a rate that fell by two per
cent had fallen. A container filling at a steady rate is the
canonical leak and a steady rate wobbles; "lower than before" calls half of
all leaks settled.

So the final rule keeps the halving that the original was reaching for and
only moves it somewhere it can work: the rate must at least halve between the
middle stretch and the last. All three gauges then read `growing`, which is
what they are.

Both errors were caught by pointing the instrument at a two-hour run and
reading it, not by a test -- the tests agreed with both wrong rules, because
the fixtures were written by the same person who wrote the rules. What the
tests hold now are the two shapes that broke it, with the real numbers in the
comments, and a mutation battery over the rule: thirteen planted, thirteen
killed, one of them only after a fixture that had been passing for the wrong
reason was replaced. **A verdict is an instrument too, and it needs the same
treatment as a gauge: make it say something you can check.**

That leaves two findings underneath, one of which is fixed further down (this
client never resent a packet) and one of which is not. RSS climbs about 45 MB
an hour and allocated blocks about 52,000 an hour on a
region with three prims in it -- 340 MB to 489 MB over the two hours, while every one of the forty-odd container
gauges is settled or flat. Something is growing that nothing on the report
names. A type histogram sampled at the same cadence is the next instrument,
and the second soak is what says whether it is worth building.

**A -- the leak did not turn up in run 3, and that is a finding with an
experiment attached (2026-09-07).** Run 3 is the first soak with the type
census. Forty-six minutes in, `proc.rss_bytes` has been flat at 634.8 MB since
minute sixteen, `proc.py_blocks` oscillates around 430,000 with no trend, and
`obj._total` around 74,000 with none either. Run 1, at the same point, was
already climbing -- 424,876 blocks at minute 35 and 450,928 by minute 70, a
straight line the rest of the way.

Three things differ between the two runs, and only one of them is testable
here:

* **Machine load.** Run 1 ran at 6.2 fps because two orphaned `pytest`
  processes were eating two cores. Run 3 runs at 30, against a 30 fps cap.
* **`--hidden`.** Run 3 opens no window. Run 1 did.
* The census itself, which walks the heap every thirty seconds.

The window cannot be isolated: reproducing "visible window on the real GPU"
means putting a window on the owner's display, which is off limits, and Xvfb
has no GPU so it answers a different question. The *frame rate* can be, and it
is the better suspect anyway: a viewer drawing at six frames a second is not a
viewer doing less work, it is a viewer draining its queues five times less
often per unit of arriving traffic. A container that fills with the clock and
empties with the frame grows only when frames are slow -- and would read as a
leak that starts around minute forty and never stops.

So run 4 was to be run 3 with `--max-fps 6` and nothing else changed. That
experiment is still worth running, but it is no longer the next one -- see
below.

**A -- the soak could not see the machine it was running on (2026-09-07).**
Run 3 finished the thought the paragraph above started, and the answer was not
about the client. Sampled every five minutes, `proc.rss_bytes` climbed to
634.8 MB by minute fifteen, then sat within **4 kB** of that number for
thirty-five minutes, and then climbed steadily for the rest of the run:
640 MB, 656, 670, 685, 702. The pygame_gui text objects the census had named
did the same thing -- oscillating between 130 and 370 for fifty-five minutes,
which is generational GC and not a trend, and then monotonic.

`ps` during the climb: a 100%-CPU build of somebody else's project, then
another one fifteen minutes later. The owner works on this machine. The client
itself was on a quarter of a core and holding exactly 30.0 fps against its
30 fps cap, first half and second -- it was never starved. What changed at
minute fifty-five was the machine, and **nothing in the report could say so**.

That is a hole in the instrument, not in the client, and it is the same hole
that made run 1 unreadable: run 1's 6.2 fps was two orphaned `pytest`
processes, discovered by hand in `ps` hours later. A soak on a developer's own
desktop shares the machine with a compiler, a browser and the test suite of
the thing being soaked, and every figure it prints is measured against that.
Without a record of it the report cannot tell "the client started growing at
minute fifty-five" from "something else started at minute fifty-five" -- and
the two look identical, which is how a soak comes to call a build a leak.

So the probe now takes two more readings on every sample, and the report
prints them **above** the growth table, because they are what decides whether
to believe it:

* `proc.load_1m` -- the machine's one-minute load average. The machine's, not
  the client's.
* `proc.cpu_seconds` -- this process's own CPU, printed as cores. Load says
  the machine was busy; this says whether *we* were, which is the difference
  between starved and idle.

Both are kept **out** of the growth report, by name, in `CONDITION_NAMES`. A
load average that rises because a build started passes every test for
"growing" in `health.py`, and CPU seconds only ever rise by definition; either
one in the table is a row that cries wolf on a run where nothing is wrong, and
a report with one of those in it is a report nobody reads to the bottom of.
The same argument already kept `gc.get_count()` out of `PROCESS_GAUGES`.

A log written before this says nothing rather than 0.0. Filling in a zero
would have every soak before 2026-09-07 claim it was measured on an idle
machine, which is exactly the claim they cannot support.

**What run 4 is now.** Run 3 with the condition gauges and the `gc.freeze()`
below, so that there is one soak on the record whose numbers can be read at
all. Only after that is the `--max-fps 6` experiment worth running, because
until the report can say what the machine was doing, its result is as
uninterpretable as run 3's was.

**A -- the leak was real, and it was the garbage collector's arithmetic
(2026-09-07).** The section above is right that run 3's climb began when
somebody else's build started, and wrong to stop there. Run 3 ran the full two
hours and ended at **RSS 481 MB -> 1.04 GB**, with `obj.list` 5,541 -> 41,976
and `TextBoxLayout` 132 -> 3,333, while `udp.total_received` and `frame` were
straight lines from end to end. The inputs never changed. Something was
keeping one text object per HUD line, forever.

The objects are *cyclic garbage*: pygame_gui builds a small graph per laid-out
line, the parts of it refer to each other, and only the cyclic collector frees
them. So the question is not what holds a reference -- nothing does -- but why
the collection stops arriving. This interpreter collects the old generation
**incrementally**: a young collection runs every few thousand net allocations
and drags a slice of the old generation along with it, so one complete pass
over the old generation costs as many slices as that generation is large. A
viewer's static heap is most of the old generation and is never garbage; all
it does is lengthen every pass, until the pass that would free a text object
promoted an hour ago has still not come round. Freezing moves that heap into
the permanent generation, which is not scanned at all.

The harness that settled it is `tools/gc_pressure.py`: a real HUD with its
diagnostics panel open, driven for twenty thousand frames with no window, and
the heap sampled by type as it goes. What makes it work is that the sampling
loop does **no** `gc.collect()` of its own. The first version called one before
each census and reported a clean heap for as long as it was asked to -- a
census that collects first cannot see a collection failing to happen. Removing
that call reproduced the sawtooth on the first run, and the file now says so at
the top so it does not get put back.

The mechanism is measurable on its own, without a HUD, and
`tools/gc_pressure.py --mode threshold` does it: hold a heap, then count how
much cyclic garbage has to be allocated before one automatic collection
arrives.

    heap                         objects per collection
    empty                                        37,964
    624k objects held                           415,586
    the same, frozen                              3,998

A hundredfold, and the middle row is what a loaded viewer is. (The first row
is this process's own baseline -- the module imports pygame -- and moves with
what is loaded; the pair that means something is the second against the
third.) It also explains a test this pass wrote twice and had to kill once. The pin
on the index mapping first allocated a fixed 60,000 cyclic objects and waited
for the counter to move: it passed in its own file and failed in the full
suite, because in the full suite 60,000 is not one collection's worth. The
second version allocated *until* the counter moved -- which is unbounded for
exactly the reason being measured, and reached **8 GB** of resident set before
it was killed. The version that shipped checks only what an explicit call
moves, and identifies the automatic counter by elimination: it is the one
`gc.collect(0)` and `gc.collect()` both leave alone. The positive
demonstration belongs in the harness, where it is the measurement rather than
a precondition.

    mode=none  frames=20000            mode=freeze  frames=20000
      frame     list   deque  TextBox     frame     list   deque  TextBox
          0     4382     346      105         0        6       0        0
      12000    20894    3346     1605     10000     2266     409      204
      14000     5048     472      168     12000      396      69       34
    one full gc.collect(): 20.6 ms      one full gc.collect(): 2.0 ms

Live objects sawtooth between 36,000 and 64,000 when left alone, and sit
between 700 and 4,000 after `gc.freeze()`. The collection that ends each tooth
costs **20.6 ms** in the first column and **2.0 ms** in the second -- a
twenty-millisecond stall is a dropped frame at 30 fps and two at 60, so the
old behaviour was not only growing, it was stuttering.

`health.freeze_static_heap()` calls `gc.collect()` and then `gc.freeze()`,
which moves everything alive at that instant into a permanent generation the
collector never walks again. `app.py` calls it **immediately before the frame
loop, after the session is up but before the world arrives** -- and that
placement is the whole safety argument, not a convenience. Anything frozen is
never freed. A prim frozen here would be a genuine leak the moment its region
went away, so the call has to happen while the heap is the viewer and nothing
else. It prints what it froze (`gc.freeze objects=12689`) so a later run can
see whether that number has quietly started tracking the world.

What makes the placement *safe* rather than merely sensible is one line
above it: nothing between `asyncio.create_task(run_live_session(...))` and the
freeze awaits, so the session coroutine has not run a single step and no packet
has been decoded. A single `await` slipped in between would start freezing
prims, and nothing else in the file would notice, so
`test_nothing_awaits_between_starting_the_session_and_freezing` reads the
source between those two lines and fails if one appears. Checked against a
mutant: inserting `await asyncio.sleep(0)` there turns it red.

Three counters go on the sample, straight off `gc.get_stats()`, and all three
are declared **counters** in `tools/soak_report.py` with a test that says so.
Left as gauges they would be three permanent `growing` rows at the top of every
report, which is how a report stops being read. They are there for one
comparison: an automatic-collection count that slows while `obj._total` climbs
is this bug, by name, without a two-hour rerun.

**And the first version of both of those was wrong, in the way this pass keeps
finding.** They went out as `gc.gen0`, `gc.gen1`, `gc.gen2`, and the paragraph
above went out saying `long_lived_pending > long_lived_total / 4` -- which is
the *pre-3.13* collector. Python 3.13 replaced it with the incremental one and
kept `gc.get_stats()` at three entries while the meaning moved underneath.
Measured on the interpreter this actually runs on (3.14.4, thresholds
`(2000, 10, 0)`): index **1** is where the automatic collector counts, and 0
and 2 move only when something calls `gc.collect(0)` or `gc.collect()` by
hand. Soak run 4's first sample says `gc.gen0 = 0` and `gc.gen1 = 12`, which is
what gave it away -- a young generation that had never been collected while the
one above it had been collected twelve times is not a thing.

So they are `gc.young_collections`, `gc.auto_collections` and
`gc.full_collections` now: named for what each index was *measured* to count,
with a test that trips if an interpreter moves them. `soak_report.py` still
lists the old names as counters, because run 4 is written in them. This is the
same mistake as printing a wire byte where a reader expects metres, one layer
down: a number labelled by what it was assumed to be. The fix and its
measurements are unaffected -- freezing still removed the sawtooth and cut a
full collection from 20.6 ms to 2.0 -- only the explanation was wrong, and
`7e792ed`'s commit message still carries the wrong one.

**Run 3 and run 4 do not compare on `obj.*`.** `gc.get_objects()` does not
report the permanent generation, so from run 4 on the census counts the world
and no longer the viewer plus the world. Run 4's opening `obj._total` will be
far below run 3's and that is the fix working, not a different scene. Within a
run the trend still means exactly what it meant.

**What is deliberately not done.** A region of 15,000 prims makes 15,000
long-lived objects, and `long_lived_total` climbs with them; freezing the
startup heap buys headroom, it does not repeal the arithmetic. The follow-up
is a periodic explicit `gc.collect()` on a measured budget, and it is left out
on purpose so that run 4 measures **one** change. Two fixes in one soak is a
soak that cannot say which one worked.

**A -- the instrument was reading the sawtooth's phase (2026-09-07).** Run 4
answered the question it was launched for and then exposed a worse one in the
tool used to read it. The heap was flat: `proc.rss_bytes` reported
**630,185,984 on sample after sample** for the last twenty minutes of a
forty-three-minute run, the same number to the byte. The report called three
of its rows leaks anyway -- `proc.py_blocks` at 8,183 an hour, `obj._total` at
3,355, `obj.list` at 1,853.

The rate was the last sample minus the middle one, divided by the time between
them. That is a line through two points chosen by *when they happened* rather
than by what they say, and a healthy viewer heap does not sit still between
samples: it fills and is evicted, over and over, going nowhere. Run 4's
`obj._total` swung between **11,597 and 16,722** all through the flat stretch.
Subtracting two points off a swing that size reports which end of it the run
happened to stop on, and calls it an hourly rate.

The fix is a least-squares fit over the second half, reported **with its own
standard error**:

    name             per hour        +/-   verdict
    proc.py_blocks      1,461      1,780   settled     (was 8,183, growing)
    obj._total            365        770   settled     (was 3,355, growing)
    obj.list              137        447   settled     (was 1,853, growing)

Neither number means anything without the other, which is why the column was
added rather than the rate quietly corrected: 1,737 an hour beside an error of
40 is a leak and beside an error of 2,041 is nothing, and the old report
printed the first of those either way.

**What stops this from being a way to explain leaks away.** The threshold is
two standard errors, and it is not a knob -- the two runs on record sit
twenty-fold either side of it. Run 4's flat rows fit at **0.5 and 0.9 sigma**;
run 3's real leak fits at **37 to 41 sigma on every row**, and comes through
the new rule reading `growing` exactly as before, at 464 MB an hour with an
error of 12 MB. Anything from 1.5 to 10 separates the two identically.

Checked afterwards against the runs it was *not* designed on, which is the
part that would have caught a threshold fitted to two examples. Run 1 -- the
other pre-`gc.freeze()` two-hour soak -- reads `proc.rss_bytes` at 41.4 MB an
hour with an error of 0.41 MB, **100 sigma**, `growing`, and `proc.py_blocks`
at 91 sigma. Three independent soaks then, two leaking and one not, all
landing on the right side.

The short logs are the honest caveat rather than a fourth success. A
two-minute run is entirely startup and every row of it reads `growing`, which
is right. A ninety-second one has five samples in its second half, and over
forty-five seconds the fit will take a dip and a recovery for a slope --
`proc.gc_tracked` in `smoke.jsonl` reads +27,219 an hour through a gauge that
ended *lower* than it started. Nothing is wrong with the arithmetic; there is
simply no trend in forty-five seconds to find, and the error column is what
says so. `MIN_SAMPLES_FOR_VERDICT` is four, and it is about having two halves
to compare rather than about having enough run to mean anything.

The tests carry both directions: a seven-phase sweep of the same healthy sawtooth
(a verdict that depends on which phase you recorded is not measuring the
heap), and the same sweep with run 3's measured leak of 64,263 an hour laid
under a swing larger than an hour of it, which must still read `growing` from
every phase.

**And what it costs.** The sensitivity is now a measured number rather than a
hope. Through a swing of 5,125, a two-hour run finds a climb of 1,000 an hour
-- a fifth of one swing, invisible sample to sample. A twenty-minute run does
not, and says `settled` with an error of 7,174 printed beside it. That is the
honest answer to a run too short to tell, and a test pins it so that nobody
recovers the missing sensitivity by lowering the threshold.

`steady` was in this for an hour, as a third word for "climbed, but inside the
noise", and it was wrong for the reason the whole change exists: the same
healthy sawtooth landed in `steady` or in `settled` depending on its phase.
Both answer the only question the column is asked. One word.

**Two survivors, and only one of them was a gap.** Twelve mutants of the new
arithmetic. Ten died. Dividing the residuals by `n` instead of `n - 2` lived
through the whole file, which is the shape of the problem: every other test
reads a *verdict*, and a five per cent error in the error crosses no
threshold in any fixture. Killing it by tuning a fixture to sit within five
per cent of two sigma would be a test that fails for the wrong reasons, so
`FitTests` pins the arithmetic directly against a fit worked by hand -- five
points, slope 0.8, error sqrt(0.12) -- which is the level the mistake lives
at.

The other survivor is an equivalent mutant: making the *counter* branch use
the fit too changes no answer, because a counter is non-decreasing and
"higher at the end than in the middle" and "the fitted slope is positive" are
then the same statement. Checked rather than argued -- 20,000 random monotonic
counters, zero disagreements -- and the argument is now a test, so if
something ever emits a counter that falls, that becomes the finding instead of
this line quietly changing meaning.

**The rows that stand alone now, and the answer they give.** With the noise
quiet, `--only growing` on run 4 returns **two** rows rather than five, and
both of them are bounded containers still filling:

* `udp.seen_sequences`, 265 an hour on a 630-sigma fit -- the tightest in the
  run. `seen_reliable_sequences` is a `RecentSequences` and stops at twice
  `SEQUENCE_MEMORY`, so it reaches 8,192 in about thirty hours and no soak
  here has run that long.
* `hud.wrap_cache`, 13.3 an hour -- `wrap_lines` empties it at
  `WRAP_CACHE_LIMIT` (512), and it stood at 112 after fifty minutes.

Which makes run 4 the first soak on record with **nothing growing that is not
supposed to**, and it took both fixes to see: `gc.freeze()` to stop the leak,
and the fit to stop the instrument from inventing three more.

The comment beside the `udp.seen_sequences` gauge in `app.py` still described
the `set` that `RecentSequences` replaced -- "the one with no ceiling at all
by construction" -- which is the same defect as the coarse height byte below,
one layer up: a reader consults the comment to interpret the row and concludes
leak. Corrected, and the bound it now claims was already pinned by two tests
in `test_udp_recent_sequences.py`.

**A -- the chat ticker, checked and left alone (2026-09-07).** The same
sweep as the hover-text cap, one input over, and this one comes back
negative. Chat arrives from other avatars and from scripts, so its length is
not this client's to choose, and `ChatFromSimulator` carries the message in a
Variable 2 field: the wire allows 65,535 bytes.

The ticker's shape invites the same bug the labels had. `visible_rows`
gathers rows from the newest entry backwards and stops when the box is full,
which is the right rule -- but it stops *after* `wrap_entry` has wrapped the
whole entry, so the work is linear in the message while the display is not.
Measured with the viewer's own font at 600 px:

    chars      rows   wrap ms
       80         1       2.6
    1,020        12       4.4
    8,000        89      34.6
   65,535       729     207.7

207 ms for a boxful of rows would be worth fixing. It cannot arrive: OpenSim
puts the message through `Util.StringToBytes1024` before writing the two-byte
length, so a kilobyte is the whole field, and a kilobyte costs **4.4 ms** --
inside one frame at 30 fps and two orders of magnitude from mattering. There
was no allocation bomb to find either; unlike the label textures, the ticker
surface is sized to its container, so the rows that do not fit are simply
never drawn rather than rasterised into something enormous.

Pinned rather than commented, in `ChatLengthTests` -- both the call *and* the
length that gets written beside it, because a writer that clamped the bytes
and then wrote the original string's length would be a different bug and a
worse one. This is the kind of reason that stops being true without anybody
editing the file it is written in.

**A -- the suite wants ten gigabytes, and it is one upstream window
(2026-09-07).** Carried over as an open question -- pytest was seen at 10.8 GB
RSS while progressing normally, and it was not established whether that was
pre-existing. It is, it is reproducible, and it is now diagnosed all the way
down. `tools/hud_memory.py` holds the measurement and the reasoning.

Traced per test with a `pytest_runtest_logreport` hook that records RSS after
every teardown, the rises land in seven files and nowhere else:

    3.3 GB    28 tests  test/test_viewer3d_hud_render_mode.py
    1.5 GB     7 tests  test/test_viewer3d_hud_scale.py
    1.4 GB    11 tests  test/test_viewer3d_hud_dirty.py
    1.3 GB     9 tests  test/test_viewer3d_object_inspector.py
    0.8 GB     7 tests  test/test_viewer3d_hud_refresh.py
    0.8 GB     7 tests  test/test_viewer3d_health.py
    0.3 GB     2 tests  test/test_viewer3d_hud_events.py
    -------
    9.3 GB    62 tests, every one of which builds a HUD

Build a HUD, drop it, collect, census: **7,162 objects and 120.8 MB per HUD**,
a straight line over twelve rounds with no deviation in any of them. A census
taken *after* `gc.collect()` still counting them is a retention and not
allocator arenas -- the same distinction the soak work turned on, asked one
process down.

**Bisected to a single construct.** Every pygame_gui element type churns flat
at zero per round -- thirty buttons, a text entry, a text box, a selection
list, a drop down -- except `UIWindow`, at **+601 per round**. The HUD builds
nine of them, which is 5,409 of the 7,162; the rest is the widgets those
windows carry, since the one in the bisection is empty.

**Upstream, and there is nothing here to fix.** pygame_gui 0.6.14, in a
process with no vibestorm code in it at all, leaks the same 601 per window.
`window.kill()` leaks 604. `manager.clear_and_reset()` leaks 621. Walking the
referrers back finds no module global holding any of it -- not through frames
either, which the first attempt at that walk got wrong -- so the window sits
in a closed cycle `gc` will not free, which is what a C-level reference the
collector cannot traverse looks like from Python. The HUD itself *is*
collected; its `UIManager` is not.

**Deliberately not fixed, and this is the reasoning rather than a shrug.** The
only lever is building fewer windows, which means sharing HUDs between tests.
Those 62 tests toggle render modes, resize, hide and show windows and change
scale -- sharing one HUD across them trades 9.3 GB for order-dependence, and a
suite that fails depending on what ran before it is worse than a suite that
wants a lot of memory on a machine with 60 GB of it. The suite passes, and it
has never been the thing that failed.

What makes it worth writing down anyway is the second reader: **the owner runs
their own builds on this machine**, so a suite that wants ten gigabytes is one
unlucky overlap from an OOM kill landing on somebody's compile. If that ever
happens the trade above is the one to re-make, and the measurement is kept so
it can be made on numbers. Run `tools/hud_memory.py --mode elements` after any
pygame_gui upgrade: if `a window` joins the other rows at +0, the 9.3 GB goes
away without anyone touching a test.

**A -- the viewer knew where it was and said otherwise (2026-09-07).** Found
in a screenshot taken to check something else. The HUD read

    Pos: 128.0, 128.0, 6.0
    water: level=20.0 avatar_z=6.0 under

while the picture showed the avatar standing on a hilltop in the sun, well
clear of a sea whose surface is at 20 m. A probe against the same region
settled which one was lying: the simulator had the avatar at **25.94 m**, the
render drew it there, and the readout was wrong.

`CoarseLocationUpdate` spends one byte per axis. On a 256 m region x and y are
whole metres and fit exactly. Height does not fit at all, so the byte counts
*fours* -- OpenSim writes `(byte)(CoarseLocations[i].Z * 0.25f)`. The client
read it as metres, so 25.94 became 6, and 6 is under a sea at 20.

Two things came out of the same line and are worth keeping apart:

* **The height is quantised, not merely scaled.** Four metres a step is a
  radar blip, not a position. So `self_avatar_position` now prefers the
  agent's *own* `ObjectUpdate`, which is metres and which terse updates keep
  current, and falls back to the coarse entry only until that arrives. The
  coarse entry names which index is us and carries the agent id, and the
  object dictionary is keyed by it, so the better source costs one lookup.
* **A zero height byte is two different facts.** The encoder is
  `Z > 1024 ? (byte)0 : ...`, so an avatar on a beach and an avatar at 1500 m
  send the same byte and nothing in the message separates them.
  `height_is_certain` says which of those a caller is holding. Nothing in the
  viewer branches on it yet; the forensics dump prints it, and a minimap that
  draws other people's heights will need it.

One case the precise source cannot serve, caught before it shipped: a
**seated** avatar is a child of its seat and reports its position in the
seat's frame -- half a metre, not a region coordinate, measured by
`tools/verify_seated_avatar.py` when the child-prim work was done. Preferring
the object update unconditionally would have read `Pos: 0.4, 0.0, 0.6` the
moment the owner sat down. So the precise source is used only for an
unparented avatar, and the coarse entry -- which stays a region position while
seated -- takes over otherwise.

The reading lives in `vibestorm/world/models.py` now -- `position_m`,
`height_is_certain`, and one `self_avatar_position` -- because it had been
copied into both viewers, which is how the same misreading was wrong in two
places at once. The 3D viewer's orbit camera had a third copy of it: "centre
on avatar" aimed at ground level while the avatar stood on a hill.

The forensics line used to print `pos=(128,128,6)`, which is the bytes, in a
shape that reads as a position. It prints `bytes=` and `pos_m=` now, both
labelled.

Pinned to OpenSim's source rather than to the one measurement: the encoder
line and the un-scaled x and y beside it are both in
`test_opensim_source_pins.py`, because the asymmetry -- two plain bytes and
one that is not -- is exactly the part a re-reader would get wrong again.

Twelve mutants, twelve killed, at the second attempt: the first battery left
"any terse object is an avatar" alive, which would have put the viewer inside
whichever crate the region happened to send first once the last-resort branch
was reached. A test with a prim ahead of the avatar in the dictionary closes
it.

What this says about the test suite is worth more than the fix. Every test
that touched a position readout set `scene.avatar_position` directly, so the
one line that computes it from a message had no test at all, and a live
screenshot found in one glance what 2,430 tests could not. The new tests feed
the view instead of the scene.

**A -- one prim could take the whole viewer down, and nothing local would
ever send it (2026-09-07).** `test_decoder_fuzz.py` covers bytes that do not
parse. This is the other half: updates that parse *perfectly* and mean
something impossible. A position of NaN, a rotation of infinity, a scale of
1e300 -- every one of those is a well-formed `ObjectUpdate`, and every one of
them reached the renderer untouched.

The failure is not a smear. `_instance_blob` packs each model matrix with
`struct.pack("19f", ...)`, and `struct.pack` **raises `OverflowError`** on a
finite number too large for a 32-bit float:

    >>> struct.pack("<f", 1e300)
    OverflowError: float too large to pack with f format

Inside the draw loop, on a frame, from one prim among fifteen thousand. NaN
and infinity pack without complaint and go on to smear geometry across the
frame instead, which is the milder half of the same problem.

So a transform that is not a place gets no place, exactly like one whose
parent never arrived -- and the machinery for that already exists and already
knows how to carry it. `linkset.is_a_place` is seven chained comparisons
against `FLOAT32_MAX`, and the chain does three jobs at once: a NaN fails both
halves, an infinity fails the upper half, and so does a finite number too big
to narrow. A region is 256 m across and the largest varregion 8,192, so
nothing legitimate comes within thirty-five orders of magnitude of the bound.

Four things it took a while to get right:

* **The composed result is checked, not the reported offsets.** Two prims each
  well inside the bound compose to a child outside it, so a parent and a child
  that are each drawable produce a child that is not.
* **A prim that loses its place this way has to be reported as moved**, or
  last frame's entity stays on screen at last frame's position. It joins the
  `pending` walk at the end of the resolve, which already exists for exactly
  this.
* **The resolve is not enough on its own.** A region with nothing parented
  never calls it -- composing is skipped when there is nothing to compose,
  which is the whole local test region -- so a root's position reaches the
  entity build unexamined. The gate is in both places, and the end-to-end test
  is what found that: the scene still drew all four bad prims after the
  resolve was fixed.
* **There are three doors, not two.** `ImprovedTerseObjectUpdate` carries raw
  floats and no parent id, so a terse-only prim is neither composed by the
  resolve nor built by the loop that gates full updates -- it gets a
  placeholder of its own, into the same instance buffer. Found by asking the
  question a third time rather than by a test.
* **Scale needs its own gate.** It is not part of a transform, because it is
  not relative and is never composed, but it lands in the same matrix and
  packs through the same `struct.pack`. A zero or negative scale is *not*
  rejected: nothing and inside-out are pictures, not crashes, and leaving them
  out would be the client deciding what content is allowed.

The test that matters is the last one in the file: build a scene from nine
hostile prims and pack every entity it produced. It fails on an
`OverflowError` rather than on an assertion, which is the actual bug rather
than a proxy for it.

Twelve mutants, twelve killed, first time -- including the four that matter
most: dropping either gate, dropping either half of the transform check, and
not reporting a lost place as a move.

**And the same question of the text a prim can put on the screen.** A label
is rasterised one pixel per pixel into a GL texture, with no wrapping.
Measured with the viewer's own font: 200,000 characters render to a surface
**2,581,248 x 21** -- 217 MB and 321 ms -- and the `ctx.texture` call after it
asks the driver for something no GPU will make. One prim, in the draw loop.

The cap is not a rendering budget picked out of the air. OpenSim writes a
prim's floating text with `AddShortLimitedUTF8`, whose length prefix is
`AddByte((byte)(len + 1))` -- **one byte** -- so 254 bytes is the whole field
and anything longer arrived from a malformed packet. Both pins are in
`test_opensim_source_pins.py`. At that cap the worst case is 254 capital Ws,
measured at 4,572 px across: 384 kB, against the 512x512 object textures this
renderer already uploads by the hundred.

The truncation goes in `_collect_labels` and not at the texture, and that
placement is load-bearing: the label cache is keyed by the string and pruned
against the set `_collect_labels` returns, so truncating later would leave the
two disagreeing and every long label would be released and rebuilt *every
frame* -- a 4,572-pixel upload per frame per prim, which is worse than the bug
it was meant to fix. Avatar name tags share the pass and the cap.

**Terrain was checked and left alone, on purpose.** A NaN height smears the
ground mesh the same way, but it cannot crash: the terrain path builds its
vertices with `array("f", ...)`, which turns 1e300 into an infinity silently
where `struct.pack` raises. And 4,000 random blobs through `decode_layer_blob`
produced *no* decoded patches at all -- the group header rejects every one --
so there is no demonstration that a non-finite height is even reachable.
Guarding it would be a guess dressed as a fix. Recorded here so the next
person knows the question was asked rather than missed; if a real grid ever
produces one, the guard goes in `RegionHeightmap.apply_patch` and this
paragraph is the reason it was not there already.

Cost, measured back to back on the same machine: somewhere between nothing and
seven per cent on the frames that rebuild entities, which is to say **inside
this machine's noise floor** -- best-of-three rounds still swung ±10% between
identical runs at load 4, and two of the nine rows came out *faster* with the
gates in. It is ten comparisons per rebuilt entity and it is only paid on
prims that changed. Worth re-measuring on a quiet machine if that path is ever
the bottleneck again; not worth optimising against noise now.

**A -- the same defect one field over, and an instrument so it is the last
one found by squinting (2026-09-07).** `TimeDilation` is a U16 in every
object-update header and it is a 0-to-1 float packed into one -- OpenSim
writes `Utils.FloatZeroOneToushort(m_scene.TimeDilation)`, and a second sender
spells the range out as `FloatToUInt16(..., 0.0f, 1.0f)`, which is what makes
the range a fact rather than a guess about what a helper's name means. Five
session-log lines printed the raw number, so a region running at 0.98 of real
time logged as `dilation=64512`, which reads as an error code. They print
`64512 (0.98)` now. The decoders still keep the wire value: it is what
arrived, and a capture is compared against it.

That is log-only and would not be worth an entry on its own. What is worth one
is that it is the *third* instance of one shape of mistake -- a wire integer
shown in a place where a reader will take it for a value -- and the first two
were found by reading a screenshot. So `tools/verify_hud_readouts.py` makes
the reading repeatable: it logs in, waits for the region to settle, builds a
scene the way the viewer does, and then computes each claim again from the
`WorldView` by a different route and compares.

* **Where we are** -- against our own `ObjectUpdate`, skipped while seated
  because the seat's frame is not the region's.
* **The region and its sea** -- name and water height against the handshake,
  and the under/above verdict recomputed.
* **What got drawn** -- the scene's entity count against a count made without
  the scene: roots directly, and a child only where every parent above it
  arrived. Drawing fewer than that is a prim that lost its place; drawing more
  than the simulator says the region holds is worse, and is a failure on its
  own.
* **Avatars** -- against pcode 47 in the view, with the coarse blip count
  beside it.

It rezzes nothing, opens no window, and can be run against any region the
agent can reach. It is not a substitute for looking: it checks the claims the
viewer makes, not the picture. But a claim it checks cannot go wrong quietly
again.

**A -- a region that is not still is still mostly still (2026-09-07).** The
repeat frame above catches the case where *nothing* moved. On a live mainland
region something always has: "not a repeat" means a few dozen prims out of
fifteen thousand, and the refresh answered that by rebuilding every prim's own
transform to change fifty of them. Measured on the 15,000-prim bench, building
`transforms` for all of them is 6.77 ms a frame; patching the 150 that moved is
0.06 ms.

The measurement that made it worth doing is the one about the scan. Bailing on
the first changed prim and collecting every changed prim as it goes cost the
*same* 2.32 ms, because they are the same walk -- so once a frame has been
shown not to be a repeat, the list of what moved has already been paid for.
The frame now carries its `transforms`, its `sources` (the instance each entry
was read off) and its parented count, and patches them.

    3000 linksets of 5   15000 objects  1% moving   41.33 ms -> 36.19
    1000 linksets of 5    5000 objects  1% moving   10.61 ms ->  9.07
    1000 linksets of 5    5000 objects  5% moving   19.51 ms -> 17.82

At 5% of 15,000 there is no change: 750 moving prims is enough that rebuilding
their entities dominates and the patch's copy is a wash. The 1% row is the
realistic one.

One idea was refuted rather than deferred. The complement -- the ids that did
*not* change, which `resolve_world_transforms` needs -- is the big side, and
the note from the previous pass said to track the small set and never
materialise the big one. A `__contains__` wrapper standing in for the set
tests that idea directly, and loses: 2.80 ms against 1.85 for 15,000
membership tests, because every test is then a Python method call. A C-level
`transforms.keys() - changed_ids` is what the code does.

*What a patch must never do* is patch through a removal. An id that has gone
is still in last frame's transforms and nothing in the scan would visit it, so
it would go on composing its children forever. It is caught by arithmetic
rather than a second walk over the region: every prim that should have an
entry is one the scan found in last frame's sources or one it did not, so
`len(sources) + added == live` exactly when nothing was dropped -- and an add
and a remove in the same frame do not cancel, because the added one is counted
on the left as well.

Two bugs were found by the randomised differential and neither by any case
anyone thought of:

* A prim with **no position** and a terse update for the **same local id**.
  The patch deleted the terse entry as if it owned it, the terse loop put it
  straight back, and the id was counted twice -- and that spare +1 cancelled a
  removal elsewhere in the region, letting a dead prim compose its children.
  `WorldView.objects` is keyed by *full* id, so it cannot be asked whether a
  local id is in it; a carried `terse_only` set answers instead.
* `_nothing_moved` calling a frame a repeat when it is not. The entity cache
  cannot see a prim it never built an entity for -- a child whose parent has
  not arrived -- so removing that orphan makes the two lengths agree again
  while the region has in fact changed. Harmless before, because nothing was
  carried; now it strands a transform. The added check is O(1): on the repeat
  path every prim is positioned and cached, so `transforms` should hold
  exactly the objects plus the terse-only ids.

The second is why the differential now compares what the frame **carries** and
not only what it drew. A stranded transform draws nothing, so both screens
match while one scene is quietly still composing a prim that left the region;
it surfaces as a wrong position thousands of frames later, nowhere near the
frame that caused it. Ten seeds in the suite, and 1,000 seeds by 250 random
operations were run before the commit: 181,848 patched frames, 65,184
declined, no divergence.

Nineteen mutants; fifteen died. The four survivors are all "correct but
slower", and three of the fifteen needed tests that did not exist -- all three
requiring *two* simulator operations in the same frame, which is exactly what
the randomised harness cannot reach, because it does one per frame. That is
worth keeping as a shape: a differential harness that steps one operation at a
time can only find bugs that one operation causes.

**A -- the composing, which was the last of the three (2026-09-07).**
`resolve_world_transforms` is already careful: a prim whose own transform did
not change keeps the very tuple it had. It still visited every prim in the
region to hand 14,850 of them back what they already held, and after the first
two patches that walk was most of what a frame cost -- 47% of it by profile.

A prim's world position changes only if its own did or an ancestor's did, so
the work is a walk **downward** from what changed. That needs the one thing
the resolve never kept: which prims hang off which. `_BuiltEntities.children`
is that index now, maintained beside `transforms` for the price of the edits,
and copied one branch at a time -- a shallow copy of a dict of sets shares
every set in it, and editing one of those edits the record of the frame
before.

The index holds parents that are **not in view**, which is the interesting
half: updates are not ordered, so a child routinely arrives before its root
and sits unplaced. Nothing about the child changes on the frame its parent
lands, so unless the index remembered who was waiting on that id, the walk
down from the parent reaches nobody and the child stays where it is not.

It gives up on a parent cycle rather than going round it: the budget is four
passes over the region, far more than any honest linkset depth, and beyond it
the full resolve is asked instead -- which says that nothing caught in a cycle
has a place at all, and is the one answer a downward walk cannot reach. A
simulator should never send one; the randomised differential makes them on
purpose.

Fourteen mutants, eleven killed. The three survivors are the old resolve
itself (performance, which the bench measures and no test can), one documented
guard against a state that is argued unreachable, and a stale index entry that
costs an extra visit and produces the same answer -- because the walk composes
from the *transform entry*, not from the list it arrived by.

**A -- the benchmark was measuring the owner's compiler (2026-09-07).** Two
runs of `tools/bench_scene_refresh.py`, twenty minutes apart on identical
code, came back at 2.82 ms and 6.24 ms for the same row. `ps` in between: two
of the owner's builds at 95% of a core each, on a machine at load 15. The
benchmark had exactly the hole the soak report had, one level down, and it had
been quietly inflating every figure recorded here today.

Two changes, and neither closes it completely:

* **CPU time, not wall clock.** `time.process_time` is this process's own user
  plus system time, so the scheduler handing a core to somebody else stops
  counting against us. What it cannot exclude is a core *stalled* on another
  process's cache misses, which is still our time being spent.
* **Rounds, and the best of all of them.** The whole table is walked several
  times rather than each row measured in one sitting, so a row's samples land
  at several moments of the machine's day instead of all inside one busy
  minute. The reported figure is the minimum, which is the only sample close
  to the truth on a machine somebody else is compiling on.

And the load is printed at the bottom, for the same reason the soak report
prints it at the top. (The change rode in on `3bb4c40`, whose message is about
the composing patch and does not mention it -- so this is the entry that
records it.)

Re-measured under that method, at load 15, before and after the composing
patch:

    3000 linksets of 5   15000 objects  1% moving   25.05 ms -> 15.04   (40 -> 66 fps)
    3000 linksets of 5   15000 objects  5% moving   50.75 ms -> 38.18
    1000 linksets of 5    5000 objects  1% moving    6.67 ms ->  3.93
    1000 linksets of 5    5000 objects  5% moving   13.97 ms -> 10.25

Note the "before" column: 25.05 ms where the previous entry recorded 27.17 for
the same code. Every wall-clock figure in the entries above this one is
inflated by whatever else was running, and they are left as written rather
than restated, because the *differences* they report were measured back to
back and are the part that was ever load-independent.

**A -- and every one of those savings, times the regions in view
(2026-09-07).** `_refresh_neighbour_entities` handed `_build_entities` a cache
and a placement map but never a *previous build*, so a region next door paid
full price for every frame: no repeat frame, no patched transforms, no patched
entities. A mainland avatar has regions on several sides. It carries the whole
record per handle now -- which is also less bookkeeping than carrying two
pieces of it, since the build holds its own cache and placement -- and still
throws the lot away when the offset moves, which is what walking across a
border does to every one of them.

That makes the neighbour path exactly as capable of quietly keeping an answer
that has stopped being true as the root region's, and it reaches that state
through a different function: a different offset, a different cache, a
different region handle stamped on every entity. So it has its own randomised
differential now, at `RandomisedNeighbourAgreementTests`, and 300 seeds by 250
random operations were run against it before the commit.

**A -- the other fifteen-thousand-prim loop (2026-09-07).** Patching the
transforms left the entity walk: for every prim in the region, two dictionary
lookups and an identity check to conclude that the entity already in hand is
still the right one. That is nothing per prim and 27,000 lookups a frame at
15,000, and the frame that patched the transforms already knew which prims
those lookups would have said anything about.

    3000 linksets of 5   15000 objects  1% moving   36.19 ms -> 27.17
    3000 linksets of 5   15000 objects  5% moving   67.08 ms -> 56.03
    1000 linksets of 5    5000 objects  1% moving    9.07 ms ->  6.68

Against where this pass started: 41.33 ms to 27.17, 24 fps to 37, before a
triangle is drawn.

The rebuild set is *not* the set of prims whose data changed. A linkset's
children get no update at all when their root moves, and every one of them is
somewhere else regardless -- so the composing has to say which those are.
`resolve_world_transforms` already maintains that set internally (it is how a
moved root carries its whole linkset) and now fills a caller's set on request.
Working it out afterwards means comparing a tuple per prim against last
frame's, which is the walk being removed.

Three ways a prim stops being what it was without its own update saying so,
all three found by the randomised differential and none by any case anyone had
thought of:

* **The terse pass is a fallback, not a category.** It is not only for prims
  no full update has been seen for: it is also what draws a full update that
  could not be *placed* -- an orphaned child appears as a placeholder rather
  than not at all. Splitting the rebuild ids by which half of the world owns
  the *transform* skips that fallback, and the prim disappears.
* **A terse update for an id a full update owns changes no transform and still
  changes the picture**, for the same reason. It has to be collected -- but
  *after* the composing, not before: folding it in earlier tells the resolve
  that the full prim's transform changed and recomposes its whole linkset for
  nothing.
* **A prim can lose its place.** Reparent a root onto an id that does not
  exist and it can no longer be composed, and neither can the child hanging
  off it -- which nothing told anything about. The resolve is the only thing
  that knows, because knowing means having tried; what is left in its
  `pending` at the end is exactly that set, and the ids in it that had a place
  last frame are now entities that have to go.

Eleven mutants, seven killed. Of the four survivors, two are performance-only
in the same sense as the last pass ("never patch the entities at all" is the
old code, and it is the bench that says what that costs, not a test), one is
defensive (`fresh_cache.pop` before a rebuild that may fail to produce an
entity -- no reachable path was found where it matters, and it keeps the cache
an honest record of the frame), and the fourth needed a test: a prim that
changes which dictionary it belongs in. `TerseWorldObject.is_avatar` comes off
the update, so the same local id can be an avatar in one frame and a prim in
the next; dropping it only from the dictionary about to be written leaves the
other holding it, and the viewer draws the prim twice -- once where it is, and
once, forever, where it was.

The differential ran 1,200 seeds by 300 random operations before this was
committed: 261,900 patched frames, 94,399 declined, no divergence.

**A -- the frame that arrives back where it started (2026-09-07).** The
refresh already skipped *rebuilding* an entity for a prim that had not moved.
It did not skip finding out. A still 15,000-prim region spent 30 ms a frame on
69,000 dictionary lookups and 60,000 inserts, producing four dictionaries
equal to the four it produced last frame -- 31.21 ms measured, a 32 fps
ceiling before a triangle is drawn.

The handoff called the fix incremental -- "the world saying which prims
changed rather than the scene asking each one" -- and that is a real design
with new state in `WorldView` to keep correct. It is also not what this
needed. Asking every prim is cheap; it is the four dictionaries that are
expensive. `_nothing_moved` asks, in one pass that bails on the first prim
that moved, whether the frame is a *repeat*: the object count is what it was
and every prim is the same instance the cache holds. If it is, `_build_entities`
returns the previous result whole.

    3000 linksets of 5   15000 objects   0% moving   31.21 ms  ->   3.07 ms
    1000 linksets of 5    5000 objects   0% moving    9.59 ms  ->   0.76 ms
    1000 single prims     1000 objects   0% moving    1.09 ms  ->   0.16 ms

The moving rows are unchanged, which is the point of bailing early: 1% of
15,000 prims in motion means the scan finds one after about a hundred
comparisons and then does the work it was going to do anyway.

**What can go wrong with a memo is omission**, and an omission is invisible in
the frame it happens -- the screen keeps showing what it was showing. So the
test that matters is not a list of cases somebody thought of:
`RepeatFrameMatchesAFullRebuildTests` runs two scenes through fourteen steps,
one carried across every frame and one built from nothing, and they must agree
after each. The steps include the two that a count alone would miss -- one
prim leaving as another arrives, and a terse-only prim moving -- and the one
that must *not* take the fast path, a child whose parent has not arrived.

The check rests on `is`, and that is worth a test of its own that is not a
clock. `WorldObject` is a frozen dataclass, so swapping `is` for `==` passes
every behavioural test and quietly walks two dozen fields per prim per frame.
`_RefusesToBeComparedByValue` raises if anything asks -- the wrong answer is
made impossible to obtain rather than merely slow.

**And the net was widened before the next pass, not after.**
`RandomisedRefreshAgreementTests` runs five seeds of a hundred and twenty
random changes -- prims arriving, leaving, moving, being reparented onto other
prims and off them, terse-only prims doing the same -- and after every single
one a scene that carried its state must equal a scene that carried nothing.
Every carried-state bug is an omission, and an omission is invisible in the
frame it happens: the screen keeps showing what it was showing. Fourteen steps
somebody thought of will not find the fifteenth. It also asserts that the run
took *both* paths, because a differential test that never took the fast path
proves nothing about the fast path.

**What is left, measured rather than guessed.** The still rows are done; the
*moving* rows are now the whole of it, and they did not move:

    3000 linksets of 5   15000 objects   1% moving   41 ms   (24 fps ceiling)
    3000 linksets of 5   15000 objects   5% moving   73 ms   (14 fps ceiling)

One percent of 15,000 prims is 150 that moved, and they cost 38 ms -- 250
microseconds apiece, which is not what rebuilding 150 entities costs. It is
that *any* change puts the frame back on the full O(n) path: 69,000 dictionary
lookups and four dictionaries of 15,000 entries rebuilt to hold what 14,850 of
them already held. Profiled, `_region_frame_transforms` and
`resolve_world_transforms` are 61% of that, and both already carry unchanged
answers across -- they simply rebuild the dictionary they carry them into.

**And one refactor that looked obvious and measured worse, recorded so it is
not tried twice.** The frame walks every prim twice -- once for the repeat
check, once inside `_region_frame_transforms` to find which prims are
unchanged -- the same predicate over the same dictionary. Folding them into
one walk that materialises the `unchanged` set and passes it down made the
still row go from **3.07 ms to 5.97 ms** and the 1% row from 41.4 to 44.8. The
second walk was never the cost: it is that the repeat check bails on the first
prim that moved and allocates nothing at all, while a materialised set of
15,000 ids costs 15,000 `set.add` calls on every frame including the ones the
check was about to answer in a hundred comparisons.

Which is the constraint the next pass has to respect: **track the small set,
never materialise the big one.** `changed` is 150 of 15,000 and collecting it
is cheap; `unchanged` is 14,850 and collecting it is not.

So the next pass is: keep `transforms`
and the resolved placement across frames and patch the entries that changed,
rather than rebuild them. The scan is not the cost -- that is the lesson of
`_nothing_moved`, and it is measured -- so the design does not need `WorldView`
to say what changed. It needs a parent-to-children index kept across frames so
a moved root can recompose its own linkset and nothing else, and it needs
removals handled explicitly rather than falling out of building fresh
dictionaries. That is state to keep correct, and the equivalence harness
already written for this pass is what it should be built against.

**And it is counted, not just benched.** `scene.repeat_frames` and
`scene.rebuilt_frames` go into the soak log. A bench can say a repeat frame
costs 3 ms instead of 31; only a run can say how often a live region has one,
and a saving that never happens is not a saving -- if the ratio comes back
near zero, the check is pure overhead and the benchmark was measuring a world
this client does not see. Reading them is the first thing to do with the next
soak.

Two mutants on that pair survived the first pass and are worth recording,
because both are the same shape: a wrong thing that is not wrong *enough* to
show up. Swapping the two gauge names passed everything -- the wiring test
asks whether a gauge reaches something, not whether it reaches the right
something, and both counters exist on the same object. And comparing the build
records with `==` rather than `is` was behaviourally identical while walking
four dictionaries of fifteen thousand entries a frame -- a deeper walk than
the one the fast path exists to avoid, for the same answer.
`_BuiltEntities` is now `eq=False`, which makes writing it the slow way
impossible rather than merely unwise.

**Mutation, 12 mutants: 10 killed, 2 equivalent.** Both equivalents are the
`self._built = None` lines in the region reset and the no-view path. Neither is
observable: both aliases the reset already clears, and a new region's prims are
never the same instances anyway. They are kept, and the comments now say
plainly that they are not load-bearing -- the invalidation belongs where the
invalidation happens rather than resting on a second mechanism noticing in
time.

**A -- 168 ms of every terrain rebuild, and the two hours of soak that
blamed the wrong thing (2026-09-07).** The soak said 6.2 frames a second over
two hours on a region holding three prims. `bench_scene_refresh.py` says three
prims cost nothing to derive and `bench_render_frame.py` says a thousand cost
1.6 ms to draw, so neither of the two benches this project had could account
for 160 ms a frame. What they had in common is that both were written to
answer "what does a *big* region cost", and `bench_render_frame` switches the
sky, the terrain and the water off to ask it.

`tools/bench_frame_phases.py` asks the other question: hold the world at the
size the soak actually ran against and turn the scenery on and off instead.

    everything on                       0.62 ms  (1623 fps ceiling)
    after a heightmap revision bump   168.22 ms  (   6 fps ceiling)

`_upload_terrain_mesh` rebuilds the whole 256x256 sheet whenever
`RegionHeightmap.revision` moves, and `revision` moves once per *patch* --
`apply_patch` bumps it, and a region sends its ground a patch at a time. The
168 ms was 61 ms building 327,680 vertices a component at a time, 30 ms
building 390,150 triangle indices, 33 ms building 261,120 line indices, and
34 ms in three `struct.pack(f"{n}f", *values)` calls, each of which unpacks a
list into a third of a million positional arguments.

Three changes, no new dependency:

* A row of the grid agrees with every other row on two of its five components
  and differs on three, so the two that agree are laid out once and the three
  that differ go in as strided slice assignments on an `array("f")`. 61 ms to
  6.
* The indices depend on the *shape* of the grid and on nothing else -- not the
  samples, not the origin, not the z scale -- so they are built once per shape
  and shared. `functools.lru_cache(maxsize=8)`, bounded, because there are two
  shapes in play: 256 for the region underfoot and 65 for a neighbour's.
* An `array` goes into `moderngl.Context.buffer` as it stands. 34 ms to under
  one.

**168 ms to 14.4 ms**, a 6 fps ceiling to 70. The tests that hold it are the
obvious implementation, kept in the test file and compared against on five
grid shapes including two non-square ones, because this is a change of *how*
and the only thing worth asserting is that it is not a change of *what*.

Found on the way, in the same function's release path: `_terrain_texture_vao`
was released with the rest of the terrain handles and then, alone among the
seven, not set to `None`. The draw path guards on `is not None`, which a
released `VertexArray` passes. Released-and-cleared are two lists in that
function and they are now pinned as the same list.

**Mutation, 24 mutants: 23 killed, one equivalent** -- multiplying by a
`z_scale` of exactly 1.0 instead of skipping the multiply is the same answer
by a slower road, and no test can or should tell them apart.

One survivor was real and worth the battery on its own. Handing a *neighbour's*
sheet the region's own index array -- 256x256 indices for a 65x65 grid --
passed all 273 tests. What it does at the GPU is read four thousand vertices
past the end of the buffer. Nothing had ever asserted that the indices a sheet
is drawn with address vertices that sheet has, so nothing noticed; the
invariant is now pinned in `NeighbourSheetIndexesItsOwnVerticesTests`, along
with the index *count* recorded on the mesh, which the draw call passes to
`render` and which no test had tied to the buffer either.

**What this did not explain.** `region.layer_blobs` went 0 to 2 across run 1,
so the terrain was not being rebuilt every frame for two hours and this is not
where the 6.2 fps went. The honest answer to that turned up separately: two
`pytest` processes orphaned by an earlier killed mutation battery had been
spinning at 92% of a core each for nearly four hours, holding the machine at
load 14. Run 3, started after they were killed, sits at **29.8 fps** against a
30 fps cap. So the soak's frame-rate figures before 2026-09-07 03:20 measure
the machine, not the viewer, and the terrain finding stands on the bench
rather than on them.

**A -- the leak that no gauge names, and the instrument for it
(2026-09-07).** Two hours against the quiet local region: `proc.rss_bytes`
climbed 45 MB an hour and `proc.py_blocks` 52,000 an hour, both still climbing
at the end, while every one of the forty-odd container gauges came back
`settled` or `flat`. Fifty-two thousand blocks an hour against forty-five
megabytes is about 865 bytes a block, so whatever it is, it is not small and
there is a lot of it.

Adding another gauge cannot find this. A gauge names a container somebody
thought of, and the whole content of the finding is that nobody thought of
this one. So `TypeCensus` counts *every* live object by type, on the same
cadence as everything else, and the report ranks the type names exactly the
way it ranks the gauges. `--soak-objects` turns it on; it is off by default
because it walks the heap, and it times itself into `obj._census_ms` so a
long `longest_gap_s` in the report can be told from the instrument stopping
the world to count.

Two properties make the record readable afterwards, and both are the kind of
thing that looks like a detail until the run is over.

*A name once reported keeps being reported.* The naive instrument writes down
the current top forty, and the leak is precisely the type that is **not** in
the first top forty -- it climbs into it halfway through. Its series would
then start halfway through the run with nothing to compare the end against.
So the census follows a name for ever once it has seen it, reporting zero if
the type is gone, which is a fact rather than a gap.

*And it is bounded*, because an instrument that grows without bound while
hunting a thing that grows without bound is not funny twice. 512 names, and
`obj._untracked_types` says how many were dropped rather than dropping them
quietly -- a process minting classes at runtime would hit that ceiling, and
that would itself be the finding.

**What it cannot see, stated up front.** `gc.get_objects()` returns only what
the collector tracks, which excludes `str`, `bytes`, `int` and `float`. A
million leaked strings held in one list reads here as that list's type being
perfectly ordinary. So a census that finds nothing is not "no leak": it is a
leak in something untracked or something native, and `tracemalloc` grouped by
allocation site is the next instrument after this one, not a fallback.

**What run 1's own numbers already say about the shape.** Read minute by
minute rather than as one rate, the leak has a start. `proc.py_blocks` fills
to about 425,000 in the first twenty minutes -- caches, and expected -- then
sits between 425,000 and 430,000 for twenty more, and from roughly minute
forty climbs at a steady 750 blocks a minute for the remaining eighty, all
the way to 489,539. Not a curve that flattens: a straight line.

`proc.rss_bytes` is the *worse* of the two readings and it is worth knowing
why, because it reads like a second finding and is not one. RSS sits at
exactly 443.2 MB from minute twelve to minute sixty and only then starts to
climb. Blocks were already growing for twenty minutes of that. The allocator
was handing out memory it already held, and RSS moved when the free pool ran
out -- so RSS *lags*, and its "45 MB an hour" is an average across a flat
stretch and a steep one. `proc.py_blocks` is the leading indicator, and the
one to read first.

750 blocks a minute at seven frames a second is under two blocks a frame, and
at 718 bytes a block whatever it is is not small. Which is exactly the size of
thing a type histogram names in one run.

**Mutation:** 29 mutants, all killed, over two batteries. The first battery's
baseline was red and the reason is worth writing down: killing an earlier
battery with `pkill` left one mutant applied, the next battery captured *that*
as its original, and so it measured everything against a file that was already
wrong -- two of its anchors "not found", one test failing throughout, and one
mutant reported as surviving that could not have been. This is the hazard the
standing rule exists for, and `pkill` is a second way to trip it: check `git
diff` on the mutated file before trusting a battery's baseline.

**Not covered, and said rather than glossed:** nothing drives the viewer's own
frame loop, so a mutant that passes `soak_log=None` at the call site in
`main`'s loop would survive. `probe_for_args` exists to shrink that gap to one
line -- everything downstream of the command line is now testable -- but the
one line is still untested.

**A -- a soak that dies reads exactly like a soak that was short
(2026-09-07).** The local grid has one avatar. `local/vibestorm-login.env`
and `local/vibestorm-login-tester.env` are the same account, so two sessions
cannot run at once: the newer login makes OpenSim close the older circuit,
and the older process shuts down *cleanly*, exit code 0.

A three-minute run of `tools/probe_neighbour_acks.py` therefore killed a
two-hour soak at the thirteen-minute mark, and **nothing said so**. Not the
viewer's log, which ended normally. Not the report, which described thirteen
minutes as though thirteen minutes were the question. Every verdict in it was
computed over an eighth of the data that had been asked for, and every one of
them looked exactly as confident as usual.

That is the same failure this instrument keeps finding in itself and the
sharpest version of it yet: not a wrong number, but a *right* number
answering a question nobody asked. The probe now records `--run-seconds` in
every sample and the report opens with a loud `** CUT SHORT **` line when the
span falls more than one interval below it. A log written before the change
says nothing rather than guessing, which is the same rule the cadence
follows.

The operational half is worth stating too, because it will catch the next
agent: **while a soak is running, the sim is off limits.** Check
`pgrep -f vibestorm.viewer3d.app` before anything that logs in.

**A -- and the region next door had the same hole, plus one of its own
(2026-09-07).** `NeighbourCircuit` sends three kinds of reliable packet --
`UseCircuitCode`, `AgentThrottle`, and a `RegionHandshakeReply` for every
handshake -- and forgot all of them the same way the root circuit did. Losing
the first does not degrade the neighbour: it means there *is* no neighbour.
The region next door simply never appears, which from the outside is
indistinguishable from a viewer that does not draw neighbours at all.

It also read no acks whatsoever. `PacketAck` was counted in `received` and
thrown away; appended acks were never looked at. Harmless while nothing
resends -- and a burst of five per packet the moment something does. So the
ack handling and the resends had to land together, and they did.

`vibestorm/udp/reliable.py` now holds what both circuits need: the record,
the timings, the bound, and `marked_resent`. The two differ only in their
clock -- the root circuit has `now` passed into nearly everything, a child
circuit has none and is driven entirely by packets arriving -- so the child's
sweep gets its `now` from `_pump_neighbours`, on the same call as the acks.
Deliberately the same call: one place for both to fall out of instead of two.

**Which is exactly what the battery caught, on the line where that comment
is.** A mutant that deleted the resend half of the pump survived: every test
of the sweep tested the *method*, and the method was fine. That is the third
time in one day the same shape has appeared -- `_pump_neighbours` itself, then
`drain_resends`, now the child's -- and the rule it keeps writing is worth
stating plainly: **a correct function reached from nowhere passes every test
of the function.** The loop-driven tests are cheap and they are the only ones
that catch it.

Twenty planted over the child circuit and the shared record, seventeen killed
first time. The three survivors were all real:

- an ack that cleared *everything* rather than the one sequence it names --
  silent and total, since it stops resending the packet that was actually
  lost, which is the only case the path exists for;
- dropping the newest rather than the oldest when the bound is reached, so
  the packet most likely still in flight is the one that never gets a second
  chance;
- and the missing call site above.

Twenty of twenty now.

**A -- this client had never resent a packet in its life (2026-09-07).** The
same soak that found the unbounded set left six entries in
`udp.pending_reliable` at the end of two hours: six reliable packets sent,
never acknowledged, and nothing ever tried again. The gauge read `settled`,
which is the report being right about the shape and silent about the meaning
-- a container that stops growing because the losses stopped is
indistinguishable from one that stops growing because nothing ever leaves.

`_build_outbound_packet` recorded `pending_reliable[sequence] = label`. The
label. So a resend was not merely absent, it was **impossible**: the packet
was gone by the time anyone could want it back. The only readers were the ack
handler, which pops, and the session snapshot, which sorts the keys to print
them.

The simulator's half is pinned: it resends every RTO for as long as the client
is talking and gives up never. We did the opposite, once, and then went quiet.
A lost `CompleteAgentMovement` or `AgentThrottle` strands the session with no
symptom except a thing that never happens, which is the hardest kind of bug to
go looking for and one of the easiest to measure.

`pending_reliable` now holds the bytes as they went out, the send time and the
attempt count. `drain_resends` sends anything older than a second again, with
`MSG_RESENT` or-ed into the flags byte and **the same sequence number** --
which is the one rule that matters. A resend with a fresh number is a second
packet, invisible to the simulator's own duplicate detection, and a client
that did that would be inventing the duplicate storm it spent this same pass
learning to survive. It is bounded twice: five attempts, then the packet is
abandoned out loud, and 256 packets held at all, because the container now
holds whole packets rather than short labels and a simulator that stopped
acking would otherwise turn a stalled session into a growing one.

Three decisions are worth keeping hold of.

*It is not part of `drain_due_packets`*, which is where it obviously belongs
and would have been wrong. That returns nothing until `movement_completed` --
and `UseCircuitCode` and `CompleteAgentMovement`, the two packets whose loss
strands a session outright, both go out before that is true. A resend path
that only worked once the session was up would have covered every case except
the one that matters.

*The send time may be unknown.* `_build_outbound_packet` takes `now`
optionally and `start` is one of the callers that leaves it out, so the first
version of `PendingReliable` defaulted `sent_at` to `0.0` -- which made those
two packets overdue by the whole monotonic clock, and the very first sweep
resent both instantly. The test that caught it was the negative control, the
one asserting nothing goes out *before* the timeout, on its first run. The
field is `float | None` now and the sweep starts the clock, which costs one
interval and cannot fire early.

*And the tests go at the call site.* Straight from the ack bug an hour
earlier: `_pump_neighbours` was always a correct function, so a test of the
function would have passed throughout its entire broken life. Two tests drive
the real loop with a fake socket -- one that a packet is resent, one that it
is not resent early -- and both fail if the sweep is folded in behind the
movement guard.

Fifteen mutants planted, thirteen killed on the first pass. Both survivors
were real gaps: nothing tested that a packet built without a clock is ever
resent (which is to say, nothing tested `start`'s two packets), and nothing
tested the interval *between* resends, so dropping the timer restart -- which
turns a retry into a burst four times a second -- went unnoticed. Fifteen of
fifteen now.

Still open, and the reason `udp.reliable_resends` and
`udp.reliable_abandoned` are on the soak report from here: a resend is not a
failure on its own, but a session that resends steadily is one whose acks are
not arriving, and nothing else on that report would say so. (The neighbour
circuits had the same hole, and it is closed too -- see the entry above,
which also cost them an ack path they had never had.)

**A fix that was designed, then talked out of on the strength of two
constants, and then put back by one measurement (2026-09-07).**
`_pump_neighbours` -- which flushes the acks a child circuit owes -- was
called from exactly one place: the `except TimeoutError` branch of the receive
loop. That reads like starvation, and a time-based flush was drafted.

Two constants seemed to say it could not matter. `ACK_BATCH` is 10, so a busy
circuit flushes on its own; `receive_timeout_seconds` is 0.25, so a quiet one
flushes four times a second. The uncovered case looked like a narrow middle,
and this document said so, in a paragraph explaining that the reasoning was
*finished* rather than wasted.

Then the probe ran, and it was not narrow at all:

    180 s against the region next door       before   after
    reliable packets                             53      31
    marked MSG_RESENT                            22       0
    sequences that arrived more than once        12       0
    RegionHandshake copies                        2       1
    queued acks, high-water mark                  9       1

Forty per cent of the neighbour's reliable traffic was retransmission. One
`LayerData` arrived six times in a second and a half. The reasoning failed on
a third constant that neither of the first two mentions: OpenSim sets its RTO
to five times the measured round trip, **clamped below at `m_minRTO`, 250 ms**
(pinned in `test/test_opensim_source_pins.py`). On a local sim the round trip
is nothing, so the simulator's resend timer *is* 250 ms -- exactly the timeout
that was the only thing flushing our acks. Two timers of the same length race,
and this one lost about half the time. `ACK_BATCH` never rescued it either:
nine queued at the worst moment, against a batch of ten.

The fix is moving one call out of the `except` and into the loop body. It
costs an ack packet per inbound reliable packet in the worst case -- about
fourteen bytes -- to stop retransmissions of full-size terrain packets, so it
is a large win in bytes as well as a correct one.

`test/test_udp_session_neighbours.py` holds it, and holds it at the call site
rather than at the function: `_pump_neighbours` was always correct, so a test
of it would have passed throughout. Five reliable packets arrive back to back
with no idle moment between them, and something must have been acked. Both the
old wiring and no wiring at all fail it.

**Which is what the repeated handshakes were, all along.** Four
`RegionHandshake` packets in ninety seconds had been on the gap list for weeks
as "harmless, and unexplained", with a guess attached: that OpenSim resends on
region-info changes. It does not -- there is no such path in the source. They
were retransmits of one handshake we were too slow to ack. After the fix the
same probe sees one handshake, once.

The source reading that went alongside is still worth having, and is pinned:
there are exactly three `.SendRegionHandshake()` call sites, a *child* circuit
can reach two of them (the circuit being created, and `SendInitialData` behind
the terrain-PBR flag), neither is periodic, and a repeated `UseCircuitCode`
gets none -- while the circuit is being made the resend is acked and dropped
under a comment reading "ignore viewer resends", and once it exists the packet
never reaches the handler at all.

That count caught an error of its own on the first run. Grepping
`SendRegionHandshake()` undotted also matches the method's definition and a
call inside a comment block; an eyeballed pass had two senders and a puzzle,
and asserting the number found the third, in `LLUDPServer`, before it reached
this document.

**The order of those two paragraphs is the lesson.** The reading was correct
and answered the wrong question -- it established where handshakes *come*
from, which is not where the extra ones came from. Reading two constants and
concluding "already covered" took a few minutes and was wrong; running the
probe took three, and was not. **Where a measurement is available and cheap,
a chain of correct deductions is not a substitute for it** -- it is only a
way of deciding what to measure.

**A -- 2026-09-05: the frame is no longer the problem, and the world looks
right.** Two more rounds since the note below, both driven by measurement
rather than guesswork.

*The HUD stopped repainting every frame.* It was repainted and re-uploaded 60
times a second for content that changes a few times a second. On a GTX 1660
SUPER at 1920x1080, measured with a hidden window and a real GL context, that
path cost 6.3 ms a frame -- the full-screen texture upload alone was 2.2 ms.
`redraw_hud` now reuses the texture already on the GPU: 0.9 ms, about 7x, and
in the running viewer the phase went 6.0 ms to 0.5 ms.

The check is layered, because a stale HUD is a far worse bug than a slow one.
`LayeredGUIGroup.draw` is one call over `[image, rect, area, blendmode]`
entries, so watching those *is* watching the draw rather than proxying for it;
a pending visibility rebuild counts as changed; any event, a focused text entry
(the cursor blinks by painting into an existing surface) and a hovered element
all force a repaint; and every 0.5 s it repaints regardless, so a case nobody
predicted is half a second late instead of never arriving.

*The diagnostics panel was the most expensive thing in the viewer, and it was
on by default.* It is now closed unless `--diagnostics` asks for it, and the
framerate it existed to show moved to the status bar. `hud_update` went 7.6 ms
to 3.7 ms.

The measurement that explains every expensive widget in this HUD, and is worth
keeping: **`UITextBox`'s layout is roughly quadratic in line count.** One line
costs 1.3 ms, eight cost 23.5 ms, eighteen cost 49 ms. Markup is not the cause
-- the same eight lines without it cost 20.7 ms -- and rendering them by hand
with `pygame.font.render` costs 0.74 ms, thirty-two times faster.
`append_html_text` is 9 ms, so even appending one line pays most of a rebuild.

The chat ticker was the last caller, at 23.5 ms whenever anyone spoke, and it
is now drawn by hand into a `UIImage` at 3.8 ms -- `viewer3d/chat_ticker.py`,
which imports no pygame_gui and takes the font as anything with `size` and
`render_premul`, so it draws in the HUD's own typeface and its wrapping is
testable against a stub. Two thirds of what remained after the first cut was
`UIImage.set_image` running `convert_alpha` *and* `premul_alpha`; the surface
is opaque with premultiplied text, so the second pass is skipped.

**No known hitch is left in the frame.**

*The world looks like a world now.* `--screenshot PATH` was added first,
because none of this was visible without it -- it reads the framebuffer back
through GL (in an OPENGL display the surface pygame hands out is not the one
the driver drew into, so saving that gives a black image and a confident report
that it worked) and runs under Xvfb, so looking at a frame never needs a window
on anyone's desktop. Two things the screenshots showed:

1. **Terrain was textured with the region map tile** -- the 256x256 world-map
   overview, one pixel per metre, with the objects already painted into it.
   Stretched over 256 m, its few dark object pixels became large blurry black
   patches on the ground. `RegionHandshake` names four real ground textures and
   the elevation band each covers, and the parser had been skipping exactly
   those bytes to reach `RegionID` beyond them. It reads them now, they join
   the texture queue ahead of prim textures, and a shader blends the four by
   height with the per-corner bands interpolated across the region.
2. **Water ended at the region edge**, so the sea met the sky in a hard
   straight line. It now reaches the far plane.

The sky was a flat colour and is now a gradient with the sun drawn into it
(`render_sky` toggles it).

**A -- the fifth pass (2026-09-06): a region-sized world, in a frame.**
Everything before this was measured on the local test region, which holds
about thirty prims. A Second Life mainland region holds thousands, mostly in
linksets, and two new benchmarks say what that costs:
`tools/bench_scene_refresh.py` builds regions of a given size and times
`Scene.refresh_from_world_view`; `tools/bench_render_frame.py` draws one into
an off-screen framebuffer through a standalone GL context, so it opens no
window. At 15,000 prims the answer was **699 ms to derive the scene and 283 ms
to draw it** -- one frame a second, before anything else in the loop.

Almost none of the drawing was the GPU. The draw calls and the fence together
came to 5 ms of that 283 ms frame; the rest was Python, and all of it was work
being repeated.

*The scene was rebuilt from nothing every frame.* Every prim in view got its
extra params decoded, its shape classified and a twenty-field frozen dataclass
constructed, sixty times a second, for a world in which a couple of dozen
objects had moved. `WorldView` never edits an object in place -- every update
puts a *new* frozen instance in the dict -- so `is` is an exact answer to "did
this prim change?", and an unchanged prim now keeps the entity it had. A child
also has to be rebuilt when its *parent* moved, which the cached placement
carries. Composing linkset children moved ahead of building the entities, too,
so a child is constructed once with the transform it ends up with rather than
being rebuilt by `dataclasses.replace` afterwards.

*The parent resolve then recomposed every linkset every frame to arrive back
at last frame's answer.* `resolve_world_transforms` now takes the ids whose own
transform is unchanged and the answer it gave last time, and carries those
across **as the same tuple** -- which is what lets the entity cache go on
recognising a still child by identity. The subtlety is that "unchanged" is
about a prim's *own* transform, and a child that did not move is somewhere else
entirely if its root did, so the composing tracks which world transforms
actually changed, seeded with the roots and grown outward.

*A prim was drawn six times.* A cube is six meshes so a `TextureEntry` can put
a different texture on each side, and each of those passes walked every prim in
the region again -- rebuilding the same nineteen floats of model matrix and
tint per prim per face. Those are packed once now and kept beside the entity.
And most prims do not need the split at all: `face_texture_ids` empty means
every face resolves to the default, which is the ordinary box in-world, and
those go down the whole-mesh path in one draw call.

                                    before    after
    scene, 1000 single prims         23.0 ms    1.7 ms
    scene, 15000 in linksets        698.9 ms   35.5 ms
    frame, 1000 prims                17.3 ms    1.2 ms
    frame, 15000 prims              283.2 ms   15.9 ms

A still 15,000-prim region went from about one frame a second to about twenty,
and the local region measures 317 fps unbounded with `VIBESTORM_PROFILE_FRAMES`.
What is left at that size is the scene refresh's walk over every object, which
is the part that cannot be skipped without a change signal on `WorldView`
itself; the largest single phase in the running viewer is now `hud_update` at
1.4 ms.

The shortcut that could be seen is checked by being looked at: the same
uniformly textured cube, cylinder and prism are rendered both ways and the
framebuffers compared pixel for pixel, with the prim turned off every axis so a
symmetric silhouette cannot pass by accident.

**A -- and the avatar stops being a cloud to other people (2026-09-06).**
`RebakeAvatarTextures` arrived 45 times across the recorded sessions, was
decoded to a message name and dropped. It is the simulator saying: your
`AgentSetAppearance` named a baked texture whose asset I do not have. Until
something answers it the avatar is a cloud to everyone else -- a goal-A
visualization failure that happens on other people's screens rather than this
one's, and so exactly the kind that goes unnoticed here.

There is no rasterizer in this client to bake a fresh texture with. What it can
do is assert the appearance it already holds, which is the fix when the asset
exists and only the simulator's cache entry went missing. The re-send has to
carry a **higher serial**: the simulator keeps the last one it saw, so
repeating it re-sends the appearance it has already decided is stale while the
client believes it has answered. The answer goes straight out of
`handle_incoming` rather than waiting for the next agent-update tick, since a
cloud is visible to other people now.

**A -- the sixth pass (2026-09-06): the region's own weather.** Water colour
and sky came out of constants compiled into the shaders, so every region looked
like the same afternoon at the same hour. This was named as the largest
remaining visual gap and it is closed.

It is not a UDP message. `RegionHandshake` carries the four terrain textures,
the elevation bands they cover and the water *height*, and nothing at all about
colour. The colour is behind a capability, and the local sim offers two:
`EnvironmentSettings`, the legacy Windlight document, and `ExtEnvironment`, the
EEP one. The second is read -- `world/environment.py` for the document,
`viewer3d/atmosphere.py` for what to draw with it, the two kept apart because
the first is protocol with one right answer and the second is a rendering
choice with several.

Zenith is `blue_density`, horizon is `blue_horizon` washed toward the colour of
the light by `haze_horizon`, `ambient` is how bright the whole thing is, and the
sea is its own `water_fog_color` with some sky reflected in it. Windlight's
actual sky is an integral through a modelled atmosphere -- `rayleigh_config`,
`mie_config` and `absorption_config` sit in every frame beside the colours
these use -- and reproducing it is a project of its own. What this does is take
each parameter in the direction it plainly means.

Four things the document does not say, all found by looking:

- **The capability answers 503 until the agent is in the region.** Resolving
  the URL from the seed capability straight after login succeeds, and the fetch
  then fails in a way that reads like a broken URL rather than like being
  early. The fetch is deferred until `movement_completed`.
- **The five tracks are positional** -- 0 water, 1 the sky at ground level, 2
  to 4 the sky above each `track_altitudes` entry. Nothing labels a track; only
  a *frame* carries a `type`, so the two can disagree, and a water frame read
  as a sky is a plausible and entirely wrong sky. Each frame is checked against
  the track it is listed in.
- **The sun is in the document.** Every sky keyframe carries a `sun_rotation`,
  and the default cycle's eight trace an exact arc once they are read as
  turning **+X**: straight down at midnight, +5.4 degrees at 0.125, straight up
  at 0.5, +4.3 at 0.875, down again at 0.95. Which axis was found by trying
  each of the three against all eight.
- **The simulator does not send a sun at all.** OpenSim's
  `SimulatorViewerTimeMessage.SunDirection` is `(0, 0, 0)`, every message. It
  is not a missing answer that `None` would signal -- it is a well-formed
  direction of length nothing, and the renderer's normalise stepped straight
  over it into a fixed fallback. **The sun had never moved in any session.**

The cycle is indexed by the simulator's clock. `UsecSinceStart` is misnamed: it
is a Unix timestamp in microseconds, checked against this machine's own clock.
`SunPhase` cannot serve -- measured twenty minutes apart it ran at 2.18e-4 and
4.36e-4 rad/s, a factor of exactly two, on a region reporting the same
`SecPerDay` both times, so a single window's rate is that part of the day's
rate and nothing more. `tools/probe_sun.py` is that measurement and prints all
three findings.

Everything solid dims and takes colour with the sky, or a region at midnight is
a black sky over a field in full sun. The shaders' two light terms became
colours rather than scalars, which is the right shape: `ambient` is the light
off the sky and carries the interesting tint (pink at dawn, warm at dusk, blue
at midnight), `sunlight_color` is the light straight from the sun. Brightness
is divided out of both, because `sunlight_color` is *brighter than one* at dawn
and dusk -- 2.8 at the sunset keyframe -- and cannot double as a level. A night
floor keeps the world from going absolutely black, since a viewer that cannot
be used at midnight is not much of a viewer.

Twenty-three mutations were killed across the parse, the derivation and the
plumbing. The ones that matter most are the plumbing: a derivation that is
perfect and never reaches a uniform draws exactly the sky it drew before, and
every unit test still passes. Seven GL tests read the pixels back.

**The first run of that battery was wrong, in the way the handoff already
warns about.** Restoring a file with `cp` between mutations gives it the same
mtime to the second, and Python reuses the cached `.pyc` -- so a mutation reads
as caught when it is not. Re-run with `PYTHONDONTWRITEBYTECODE=1`, one of the
twenty-three survived: sending **only** `u_horizon` back to its constant passed
every test. The sky camera looks 80 degrees up, where the gradient is 99 per
cent zenith, so a wrong horizon moved the pixel by less than one level of
quantisation. A test that looks *level* is what closes it, and finding that at
all is the argument for running the battery twice.

**A -- the sea shows the sky back, and now that is checkable (2026-09-06).**
The sun reached the water in the pass before this one on one argument: *a
mirror does not need a reflection model, it needs the thing being reflected.*
The cloud layer is the same argument again, and it was the last piece of sky
the sea did not have. A heavily clouded sky over a plain grey-blue sea is what
the live frame showed, and it is the wrong picture: an overcast sea is bright
and mottled, and it is bright and mottled because it is showing the overcast
back.

So `_CLOUD_IN_SKY_GLSL` joins `_SUN_IN_SKY_GLSL`: one GLSL string, compiled
into both programs, carrying its own uniforms so a pass that includes it does
not have to know what it reads. The sky calls `cloud_over(rgb, dir)` where the
open-coded layer used to sit; the sea calls it on the reflected ray. On the
Python side `_cloud_layer(scene)` is the one reading of the scene and
`_bind_cloud_layer` the one place the seven uniforms are set, so the two passes
cannot be handed different clouds. `_render_sky` lost four loose keyword
arguments to it.

Two details worth keeping.

**The layer is hit from the eye, not from the patch of surface.** Physically
the reflection of a layer at a finite altitude should start where the ray
leaves the water. But the sky pass anchors the layer at the eye as well --
`ground = (dir.xy / dir.z) * altitude`, with no eye position in it at all --
so taking it from the eye makes the sea's cloud *exactly* the sky's cloud and
not a second one arrived at another way. The layer is 320 metres up where the
eye is metres above the water, so there is nothing in the difference to see
either.

**And the sea can now be checked against the sky rather than against a
prediction.** Every other sea test in the file switches the sky quad off so
that what is read back is the water pass alone; `SeaShowsTheSkyBackGLTests`
does the opposite. A camera looking dead level puts the horizon exactly
between the two middle rows, and the ray through the pixel *k* rows below the
middle is the ray through the pixel *k* rows above it with its height turned
over -- which is what a flat sea does to a ray. Make the surface a perfect
mirror (`water_fresnel = (1, 0)`) and switch the waves off, and the two pixels
have to be **the same colour**. Measured: 119 levels of variation across the
sea being compared, and a worst disagreement of **one level** over all 1920
pairs.

That single assertion covers the gradient, the sun and the cloud layer at
once, and it fails the moment either pass grows a term the other has not got
-- which has now happened twice. It is the test that should have existed
before the sun did.

It cannot see one thing, though, and that is the point of the two tests beside
it: a sea and a sky that agree on *no* cloud pass it perfectly. So one frame
with the layer and one without have to differ over the water, and -- with the
sky pass switched off entirely, so nothing else has bound unit 5 -- a painted
overcast field and a painted clear one have to differ too. Without that
second one the sea would draw the right thing in every frame that has a sky in
it and the terrain in every frame that has not.

One existing test had to give ground. `test_the_water_takes_the_regions_colour`
predicts the sea pixel in Python instead of remembering it, and it already
switches the waves off because a prediction cannot guess which part of a
ripple the centre pixel is on. It switches the clouds off now for the same
reason one step further: predicting the layer would mean a fourth copy of
three octaves of value noise, and the colour the region asked for is carried
by the clear sky between the clouds just as well.

Nine mutations, all nine killed, and one of them was loud in a way worth
knowing about: dropping `cloud_over` from the water shader takes eighty-three
tests down rather than one, because GLSL then optimises the cloud uniforms out
of that program and `_bind_cloud_layer` raises a `KeyError` on the first
frame. Setting them unconditionally is what makes a shader that stops reading
them fail immediately instead of quietly.

And it is close to free. Measured on llvmpipe at 1280x800 with the sea filling
the entire frame -- a camera a hundred metres up looking down -- the water pass
is **0.87 ms without the layer and 0.92 with it**, which is inside the spread
between runs. The sky's own cloud costs 1.4 ms over the same frame, so the
asymmetry is real and not a mistake in the measurement: a reflected ray off
water leaves at a steep angle over most of the sea, so the samples land close
together, where the sky pass fans its rays across a whole hemisphere including
the horizon, where `dir.xy / dir.z` runs away and every neighbouring pixel
reads a different part of the field.


**A -- the region next door will not send its ground until you knock on its
front door (2026-09-06).** With a second local region standing beside the
first, a child circuit to it did everything the wire asks: `UseCircuitCode`,
`AgentThrottle`, a `RegionHandshakeReply` to every handshake, an ack for
every reliable packet, and `AgentUpdate` naming a camera inside the
neighbour with a 512 m draw distance. It learned the region's name and its
water height, and it received **no terrain at all** -- forty seconds of
layer type 0x37, which is cloud.

Nothing on the wire says why, and nothing on the wire fixes it. OpenSim's
`ScenePresence.SendInitialData` returns early unless *both*
`m_gotRegionHandShake` and `Caps.CapsFlags.SentSeeds` are set, and
`SentSeeds` is set at the end of `BunchOfCaps.SeedCapRequest` -- the handler
for an HTTP POST to the seed capability URL. That URL arrives beside
`EnableSimulator`, in `EstablishAgentCommunication`, and this client had
been ignoring it. One POST to it, on the same circuit that had been getting
cloud, and the whole 256x256 heightmap arrives in eleven packets:

                          no seed fetch    seed fetch
      LayerData                       3            14
      ParcelOverlay                   0             4
      terrain patches                 0           256
      layer types             cloud only   land + cloud

The diagnostic that got there was worth the detour and is worth repeating:
`SendTerrainUpdatesByViewDistance = true` in `OpenSimDefaults.ini` was the
obvious suspect, so it was set to false on the local sim and the probe run
again -- **no change**, which ruled out the whole view-distance path in one
experiment and sent the search into the initial-data guard instead. The
setting was put back afterwards; making the simulator accommodate the client
is not a fix, because a real grid will not have it.

`src/vibestorm/udp/neighbour.py` is the circuit that came out of it, with
`test/test_udp_neighbour.py` beside it -- 24 tests, and a twenty-mutant
battery that killed twenty (the survivor, "the handshake reply goes out
unreliably", is now a test: the simulator latches that flag once and never
asks again, so a lost reply is a region that stays a name with no ground
under it).

`run_live_session` now opens them. `LiveCircuitSession` keeps the two
halves -- `neighbour_announcements` keyed by region handle,
`neighbour_seed_caps` keyed by "ip:port" -- because the two events arrive in
either order and share no key at all: one names a handle, an ip and a port,
the other names a string and no handle. The run loop POSTs the seed cap,
dials the circuit, and routes inbound packets by **source address**, since
one socket carries every simulator. A neighbour that will not answer is
recorded in `neighbour_failures` and never retried, so a dead region costs
one HTTP timeout rather than one per pass. Live, against the two local
regions: `Vibestorm North` opens, 256 patches, heights -0.13 to 25.0,
offset (0, 256) m. 18 more tests in `test/test_udp_session_neighbours.py`,
thirteen mutants planted and thirteen killed.

And it is drawn. `Scene.neighbour_terrain` carries one entry per region
whose ground has actually arrived -- a circuit opens a second or two before
its first patch lands, and drawing it then paints a flat sheet at zero
metres over the sea, which looks far more broken than the sea did -- and the
renderer builds one sheet per neighbour, offset into our frame. Three things
were worth getting right:

- The sheet is **resampled to 65 a side**, not 256. Full resolution is 65k
  vertices per neighbour and there can be eight; at 4 m spacing for ground
  that is never nearer than a region away, it is a sixteenth of the
  geometry. Both edges are kept, or a seam of sky opens along the border.
- It is drawn with the **fill** shader, not the four-texture splat. A
  neighbour's own ground textures are named in *its* handshake and are not
  fetched, so the alternative to shaded ground is not textured ground but no
  ground.
- Each neighbour gets **its own height band** in the ramp. Lighting one
  region's hills with another region's range makes a flat neighbour read as
  a cliff.

Rebuilt only when a heightmap's revision moves or its offset does. The
offset moving is not hypothetical: cross the border and the region you came
from becomes the neighbour to the south, so every offset shifts by a region
while every handle and revision stays exactly as it was.

19 tests, five of them pictures rendered through a real GL context;
fourteen mutants planted and fourteen killed. Live, `Vibestorm North` shows
up past the north edge as green-tinted sea -- which is correct, and worth
knowing before anyone reads it as a bug: that region's ground has a mean of
3.0 m under a water table at 20 m, so nearly all of it is seabed.

**And then textured, which turned out to be four lines and a shader
choice.** A region's four ground textures are named in its own
`RegionHandshake`, which the circuit was parsing and throwing away, and
they are ordinary asset ids -- so *this* region's `GetTexture` capability
fetches them and a neighbour needs no second texture pipeline at all. They
go into the same fetch queue, right behind the region's own ground and
ahead of every prim, on the same argument: a neighbouring region is a lot
of square metres. Live: all four of `Vibestorm North`'s fetched, with its
own blend bands (start 10, range 60).

Which shader draws a region is decided per frame *and per region*, because
the heightmap arrives seconds before the textures do and on a busy grid one
neighbour's ground can be cached while another's is not. Uploaded texture
sets are keyed by the four paths rather than by the region: neighbours on
one grid usually share a palette, and eight copies of four images is VRAM
for nothing. Bands are set per region -- shared, a neighbour gets our sand
where its grass should be. Late textures do not rebuild the sheet.

Thirteen more tests; thirteen mutants planted, twelve killed. The survivor
is equivalent: dropping the `any(path is None)` half of the guard makes the
loader try to open a file called "None", which raises and falls back to
shading exactly as the guard did.

**And then a defect the local grid could never have shown.** The seed-cap
POST was being *awaited* inside the run loop -- the loop that acks the
region the avatar is standing in. On 127.0.0.1 that is a millisecond and
invisible. On a real grid it is a round trip with a ten-second timeout, and
an eight-way corner is eight of them back to back: up to eighty seconds in
which this client sends no acks and no `AgentUpdate`, which a simulator
reads as a viewer that has gone away. It would have shown up as "the main
grid disconnects me when I walk near a corner" and looked nothing like a
neighbour bug.

Requests are now started and collected separately:
`_start_neighbour_seed_requests` schedules one future per announced region
and returns before the coroutine has even begun running, and
`_open_finished_neighbours` opens circuits for the ones that have answered.
Anything still in flight when the session ends is cancelled, so a dying
session does not hold a thread on a region nobody will look at. Seven more
tests, one of which asserts the strongest available form of "nobody
waited": immediately after the call the request has not started at all.
Nine mutants planted, eight killed; the survivor swaps `ensure_future` for
`create_task`, which is the same thing.

**A -- and then its buildings, which is where the local ids bite
(2026-09-06).** A child circuit was already being sent them and throwing them
away. Measured against `Vibestorm North` with three prims standing in it: six
object messages in forty seconds, `ObjectUpdate`, `ObjectUpdateCompressed`
and `ObjectUpdateCached` all arriving on a circuit that had never done
anything but look. A neighbouring region showing as an empty hillside was
never a limit of the protocol.

`NeighbourCircuit` now owns a `WorldView` and a `WorldUpdater` of its own and
hands the five object messages straight to it -- the shapes are identical to
the ones underfoot, so there is nothing to translate. `ObjectUpdateCached` is
answered with a `RequestMultipleObjects`, reliably: a cached update is the
simulator saying "you already know these", a circuit that has just opened
knows nothing, and the request is the one packet in the exchange that cannot
be re-derived. Lose it and those prims are named and never described -- a
region with holes in it and no error anywhere.

**A world of its own rather than a corner of the root region's, because local
ids are assigned per region.** Object 42 next door and object 42 underfoot
are two different prims; one dictionary keyed by local id silently loses one
of them, and every cache downstream keyed the same way hands one prim the
other's data. That shaped the whole drawing half:

- `SceneEntity` gained `region_handle`, 0 for the region the avatar is in.
- The renderer's packed-instance cache is now keyed by `(region_handle,
  local_id)`. It was keyed by local id alone, and the failure that would have
  produced is a prim drawn at another prim's position -- which reads as a
  physics glitch, not as a cache bug.
- The avatar pose lookup is keyed the same way and now refuses a neighbour
  outright, rather than bending it into whatever pose belongs to that id here.
- `pick()` still walks `object_entities` only, so a neighbour prim is drawn
  and not selectable. Deliberate: the id it would return means something only
  on that region's circuit, and every selection this client sends goes out on
  the root one. It would select whatever prim holds that id underfoot.
- Hover text and name tags skip the neighbours. Their nearest prim is 256 m
  away and their furthest over seven hundred; text that reads as a label up
  close is a smear at that range, and every one of them is drawn.

The entity-building loop came out of `refresh_from_world_view` as
`_build_entities`: one region's `WorldView`, one cache, one placement map,
and an `offset` added to the **final** position. The order is load-bearing. A
child reports where it is relative to its parent, so offsetting before
composing puts the offset in twice for a child and once for a root, which
scatters every linkset next door across half a region. Each neighbour keeps
its own cache, thrown away when its offset moves -- crossing a border
renumbers every offset at once while every handle and revision stays exactly
as it was.

Their textures and their mesh assets go through this region's capabilities
too, last in the queue behind our own prims. Not tidiness: an untextured prim
is a grey box and a mesh prim without its asset is the fallback cube, and
next door that is a skyline of boxes where the buildings are. Asset ids are
grid-wide, so the only thing between them and the existing pipeline was
somebody walking the neighbour's world view.

Ground waits for its patches; prims do not. A flat sheet at zero metres over
the sea looks worse than the sea did, but a prim is at the height it reported
whether or not the hill under it has arrived.

Two smaller things came out of the same pass. The render-settings panel
gained a **Regions Next Door** toggle -- the one setting there that can cost
a region's worth of geometry each, and on a mainland corner there are eight
of them. And the panel's read-back from the scene was dropping `render_sky`
and `render_clouds`: it *replaces* the dict the buttons are drawn from, and a
missing key reads as on, so turning the sky off changed the world and left
the button saying `[x]`. The diagnostics line for neighbours now reads
"announced, with ground, prims, avatars", because the two ways this can break
are indistinguishable from the picture -- announced with no ground is the
seed capability never being POSTed to, ground with no prims is a circuit
nobody listened to, and from a camera at the border both are "the neighbour
is empty".

Live, against the two local regions: three prims rezzed into `Vibestorm
North` at (125-132, 125-130, 28) in its frame arrive on the child circuit,
reach the scene at (125-132, 381-386) in ours, and have their textures
fetched -- and a pair of frames rendered from just inside the north border,
one with `render_neighbours` on and one with it off, is the difference
between the neighbour's island with its prims standing on it and open sea.

63 tests. Twenty-nine mutants in the first battery, twenty-four killed; the
five survivors are five of those tests now (the request's reliable flag,
`KillObject`, `ImprovedTerseObjectUpdate`, a prim's per-face textures, and an
empty cached update being *dropped* rather than turned into a failed encode
-- which sends nothing either way, so only the error counter can tell them
apart). A second battery of twelve, over those five plus the HUD and the pose
guard, killed twelve.

**And the seed-capability finding is now pinned to the source it rests on.**
`test/test_opensim_source_pins.py` is the first of its kind here: seven tests
that find the exact lines the claim quotes in the committed copies under
`referencedocs/`, and fail if a later copy drops them. It cannot tell whether
the reasoning is still right, only whether the lines are still there, which
is the whole ambition -- a claim whose source moved underneath it has to be
re-read by a person, and a green test matching nothing is the one outcome
worth ruling out. It also pinned something the live runs had been showing
without anyone naming it: after both gates open, `SendInitialData` waits four
more heartbeats on purpose (`if (++NeedInitialData < 6)`), so the pause before
a neighbour's terrain starts is not the client doing anything wrong.
`ScenePresence.cs` was added to `referencedocs/` for it. Three lines are
queued in `spec/divergence-queue.md`.

**And a defect the neighbours only made visible (2026-09-06).** Walking
the regions next door for their prims' textures was nine times a cost that
was already too large, so it got measured, which is the first time anyone
had measured it: `_next_pending_object_texture_id` found its next asset by
walking **every object in view, once per receive-loop tick**. At 15,000
prims that is 135 ms a tick; with a region announced on every side, 1.15 s.
Both numbers are time the session spends not acking and not sending
`AgentUpdate`, which is the same failure the awaited seed-cap POST had and
would have shown up in the same place -- a real grid, near a corner.

`WorldView` now carries `objects_pending_textures` and
`objects_pending_meshes`, and `remember_object` is the one door a new or
re-described object comes through. Only a full `ObjectUpdate` queues
anything: a terse update replaces the object but carries its asset fields
over by reference, and so does `ObjectPropertiesFamily`, so neither can
introduce an asset that was not already queued. `KillObject` takes ids back
out. Two queues rather than one, because whichever drain ran first would
otherwise hide every object from the other.

Two things were needed beyond the queue itself, and one of them was a
surprise. `pop()` rather than `next(iter(...))`: CPython remembers where the
last pop left off, while a fresh iterator rescans the set's table from the
start every time, which made emptying a large queue quadratic -- the first
tick after a region of already-cached prims arrived measured **29 seconds**,
far worse than the scan it replaced. And a budget, `ASSET_SCAN_BUDGET = 64`,
shared across every region rather than per region: a drain returns the
moment it finds something to fetch, so the bound only bites when a great
many queued prims have nothing left to ask for, which is exactly what a
region of cached textures looks like.

    15,000 prims, no neighbours    135 ms a tick  ->  0.7 ms
    135,000 prims, eight of them  1149 ms a tick  ->  0.7 ms

Flat in both, which is the point: a tick's cost no longer grows with the
world. Eleven tests; twelve mutants planted and eleven killed, the survivor
equivalent (a prim carries one mesh asset, so putting it back in the queue
after answering for it changes nothing). One of those tests exists because
of a survivor worth naming: every other test here puts a prim in with
`remember_object`, and none of them noticed `apply_object_update` going back
to assigning `objects[...]` -- the only path a prim ever actually arrives
by. Two others hold the budget: the tests patch it so they say what they
mean, so something separate has to hold the shipped value to a number a tick
can afford.

The tests that inserted straight into `world_view.objects` now go through
`remember_object`, which is the better test anyway: a prim put into the
world around the model's own door is one the fetches will never look at.

**Twenty per cent off the frame's floor, and no new state to keep
(2026-09-06).** `refresh_from_world_view` runs once a frame and walks every
prim in view whether or not anything moved, so it is the ceiling on the frame
rate before a triangle is drawn. `tools/bench_scene_refresh.py` said 40.3 ms
for a still region of 15,000 prims in linksets -- a 25 fps ceiling for a
world in which *nothing was happening*. A profile said where it went, and
none of it was where it looked like going.

    15,000 prims in linksets       before     after
    0% moving                     40.30 ms   31.12 ms
    1% moving                     48.69 ms   38.00 ms
    5% moving                     81.73 ms   67.05 ms

Three changes, no new caches and no new invariants to keep.

`getattr(obj, "position", None)` against `obj.position`. The composing loop
is the one thing that runs for every prim every frame, and the defensive
form costs three times as much -- 5.2 ms against 1.7 ms per 15,000 prims for
the three fields it reads. The default was never reachable: they are
required fields on `WorldObject` and `remember_object` is the only door into
that dict. 270,000 `getattr` calls a profile became 40.

`resolve_world_transforms` was rebuilding `list(pending)` -- the whole region
-- on each outward pass and deleting from the dict as it went, to make a
second pass that had nothing left to do. It now carries forward only what it
could not resolve, which for a region of ordinary linksets is nothing.

And the scene compared a child's remembered transform with `==`, walking two
nested tuples of floats per child per frame. The resolver hands back *the
same tuple* for anything that did not move, and says so in its docstring, so
`is` reaches the same answer without the walk. Where `is` is wrong it is
wrong in the safe direction: an equal-but-rebuilt transform rebuilds an
entity that need not have been.

That last one is a contract nothing had ever pinned, and it is the sort that
breaks silently -- the viewer stays correct and quietly rebuilds every entity
in the region, every frame. `test/test_viewer3d_refresh_cost.py` exists for
it: no test in it asserts a number, because a test that says "under n
milliseconds" fails on a busy machine and gets deleted, while one that says
"this must still be the same tuple" fails exactly when someone removes the
reason it was fast.

What is *not* done, and what to reach for next if this needs to be faster:
the 84,000 `dict.get` calls a refresh still makes for 15,000 prims. The
honest fix is incremental -- the world saying which prims changed rather than
the scene asking each one -- and that is a design with new state to keep
correct, which is a pass of its own and not a hurried addition to this one. A
version counter alone will not do it: something is always moving on a real
region, so a whole-frame skip almost never fires. It has to be per prim.

**The camera sees round things now (2026-09-06).** Two halves of one
complaint, and the first half was a bug hiding inside a fix. The ground
march has been there since the eleventh pass, but it opened with `if not
blocked(eye): return eye` -- so it only ever ran when the camera was
*itself* underground. A camera in clear air with a ridge between it and the
avatar answered "I am not underground", stayed there, and drew the inside of
the hill across the picture. The march now always runs, from the target
outward, and the eye is just the last sample: a blocked eye is found the same
way a ridge is, and the early return is gone.

The other half is prims, which nothing had ever looked at. `_ray_hits_entity`
is the slab test lifted out of `pick` -- one function now, where there were a
picker's worth of inline loops -- and `_first_prim_in_the_way` walks the
region with it and comes back with how far along target -> eye the nearest
prim stands, as a fraction. `Camera3D` gained `sight_blocked` beside
`ground_height`, injected by the renderer once a frame for the same reason:
a camera that walked the prims itself would be a camera that knew what a
prim was.

The order matters and there is a test that pins it. The ground runs first
and the prim question is asked about the segment that came back, so the
nearer of the two wins whichever it is; the other order can hand the camera
back to the hill it was just pulled out of. First person is exempt from both
-- the eye there is the avatar's own head, and where that goes is the
simulator's business.

`SIGHT_CLEARANCE_M` is 0.25 m and is deliberately larger than the ground's
0.5 m is small: the ground is *under* the camera and a near plane of 0.1 m
keeps it there, while a wall is *across* the view, and a camera stopping in
its surface shows what is behind it through the hole the near plane cuts.
The clearance is subtracted in metres, not as a fraction, or a longer boom
would stop further from the wall than a short one.

What it costs: 4 to 7 ms a frame at 15,000 prims, depending on what else the
machine is doing, against a scene refresh that costs 40 ms at that size.
Nearly all of it is the walk and the per-prim reach. Two things were
measured and should not be re-derived. Indexing the position and unpacking
it into locals cost the *same*, interleaved, best of twelve -- an earlier
reading that said otherwise was the owner's own compile running beside it,
which is a good reminder that a benchmark on a busy machine is a rumour. And
the per-prim reach is worth what it costs: a fixed margin is about a third
faster and wrong for any prim wider than twice the margin, which is exactly
what a megaprim is, and a camera that fails to avoid a megaprim wall is the
bug this pass is about. If it ever needs to be cheaper the answer is an
index built where the entities already are, not a guess about how big a prim
can be.

The closure the renderer hands over remembers its answers, because the
camera is asked for its eye several times a frame -- the view matrix, the
water pass, the picker -- and each answer walks the whole region. A fresh
closure each frame is what clears that memory; a prim that moved must not be
answered for out of last frame's.

**And a defect the battery found by accident.** One mutant hung the suite --
two and a half hours, main thread in a futex, SDL threads idle, no spin. It
did not reproduce afterwards, so the deadlock itself is the software GL stack
under the owner's own compiles and not something in this repo. But chasing it
turned up a real hazard, and one that was already shipped: an eye pulled
*onto* its target has no direction to look in, and `look_at` says so in the
worst way available. `forward` normalises to (0, 0, 0), every axis of the
view matrix goes to zero with it, and the entire world maps to the origin
with w = 0. Nothing raises. The frame is simply wrong, and what a software
rasteriser does with a w of zero is its own business.

`eye_clear_of_the_view` could reach that exactly -- `max(0.0, ...)` -- any
time a wall stood closer to the avatar than the clearance in front of it,
which is ordinary play. `MINIMUM_BOOM_M` is a tenth of a metre, the near
plane, and the boom is never shortened past it; a boom already shorter than
that is left alone, since the eye cannot be pushed *out*. When a wall really
is that close there is no third-person view to be had and the camera clips
into it, which is the better of the two.

Sixteen mutants planted, fourteen killed. Of the two survivors one was real
-- the march stopping a sample short of the eye, which is the camera buried
in a hillside and reported as clear -- and the other is equivalent by
construction: the reject box in front of the slab test can only ever be made
*wider* by dropping a check, and correctness lives in the slab test. Only
shrinking it is a bug, and the mutant that shrank it died.

**And the sea, which two regions are allowed to disagree about
(2026-09-06).** Water height is announced per region, in each region's own
`RegionHandshake`. The plane the renderer draws is one rectangle 2304 m
across -- nine regions wide, so that the horizon never ends in a straight
line with sky under it -- at *our* region's level. Which means a neighbour
whose sea sits a metre below ours had our water drawn a metre up its beach,
and one whose sea sits well below ours had its island drawn looking sunk.
The height was already being parsed off the neighbour's handshake and kept
on the circuit; nothing downstream had ever read it.

`_water_quads` now cuts the plane along the region edges that disagree.
Every footprint that wants a different level contributes its four edges,
the two sorted edge lists cut the plane into a grid, and each cell is drawn
at the height of the region whose footprint contains its centre -- our own
everywhere else. The cells tile the plane exactly and share their corner
coordinates, so there is nothing overlapping to fight over the depth buffer
and no gap between two pieces at the same height; corners are *not* shared
between rectangles, because two pieces that meet along an edge sit at
different heights and a shared corner would drag one to the other's level.

One rectangle is still the answer in almost every frame: a neighbour that
agrees with us, or has not sent its handshake yet, costs nothing. That
matters more than it sounds -- the common case must not turn one rectangle
into nine for no visible difference, and `None` must not be read as a sea
level of zero for the frame or two before the handshake lands.

Cutting the plane into pieces at different heights leaves a slot between
them, and the first rendered frame said what a slot in the sea looks like:
the sky, straight through it, in a bright band along the whole border. So
each step gets a wall of water from the lower level up to the higher, the
way a terrain skirt closes the same kind of gap -- four of them round a
region next door. Nothing culls faces here, so a wall shows from either
side and its winding does not matter. The walls are *striped*, because the
sea's shader takes its normal from a wave function of world x and y, which
on a vertical face varies along one axis only; from eye height in our own
region they read as a thin line along the border, and closing the hole is
worth that. ~~Making them shade properly means a per-vertex flag and a branch
in the water shader, which is not this pass's work.~~ Done on 2026-09-07, and
by exactly that route -- see the entry above.

Two decisions worth keeping. The cut is only made for a region whose
*ground* is being drawn, which `neighbour_terrain` already filters to:
over open void there is nothing for a step in the sea to be a step against,
and a lone rectangle of slightly lower water in open ocean is a worse
picture than the seam it would fix. And the underwater fog is still one
uniform at this region's level, because the camera is over this region in
every case that matters.

The sea's buffers had to become growable for this -- four vertices and six
indices was the whole of it before -- so they now double the way the
instance buffer does, and the vertex array is rebuilt with them because a
vertex array remembers the buffers it was built against. Their capacity
counts *faces*, not rectangles, since the walls are faces too.

Twenty-five tests, four of them rendering a frame and reading a pixel back.
Two mutants were worth the battery on their own. Dropping
`_water_index_count` leaves the draw call asking for one rectangle's worth
of indices, so the cut-up plane loses eight ninths of itself -- our own
region's sea included -- while a test that only looks at the neighbour goes
on passing; the test that catches it looks at our own water instead. And
the setup used to record `None` for what the buffers held while writing the
default plane into them, which is two statements of the same fact that can
drift apart: it now builds the buffers from the same two functions the frame
uses and records exactly what it put there.

Verified live, which is where the claim about *whose* handshake carries the
level was actually settled. `Vibestorm North` was set to a water height of
12 m against `Vibestorm Test`'s 20 m: the child circuit's handshake carries
12.0, the plane comes back as nine rectangles with 12 m in the one that is
the neighbour's footprint, and the rendered frame shows its island standing
properly out of a lower sea where before it was drowned to a hump.

**The client could crash the simulator two ways, and now cannot
(2026-09-06).** The local sim had been failing to persist one object every
eighteen seconds since 2026-09-05 23:26 -- 2970 times by the time anyone
looked. `SceneGraph.DelinkObjects` had thrown a `NullReferenceException`
part-way through a delink *this client sent*, leaving the object half
delinked and un-storable for as long as the region runs.

`tools/delete_prims.py` already carried a comment blaming a duplicated local
id, and deduplicated. The guard was in the one caller that remembered, which
is not where a guard belongs -- and nobody had checked that the duplicate was
actually the cause.

Both were settled by measurement, each on its own freshly rezzed two-prim
linkset, with the simulator's console log as the evidence:

- **`ObjectDelink` with a local id named twice** -> `NullReferenceException`
  in `SceneObjectGroup.ScheduleGroupForFullAnimUpdate` (SceneObjectGroup.cs
  2980), via `SceneGraph.DelinkObjects` (2067). Reproduced exactly, including
  the permanent un-storable object it leaves behind.
- **A local id that is not in the region at all** -> nothing. Harmless.
- **An honest pair** -> nothing. Harmless.
- **`ObjectLink` with a local id named twice** -> a *different* crash,
  `ArgumentException: An item with the same key has already been added` out of
  `MapAndArray.Add` in `SceneObjectGroup.LinkToGroup` (3305). Tested rather
  than assumed by symmetry, and it is a good thing it was: the same guard is
  needed, but the evidence for it is its own.

So it is the repeat and nothing else, and both encoders refuse one now --
raising rather than quietly deduplicating, because a caller that asks to
delink the same prim twice has a bug of its own and rewriting the request
would hide it. `delete_prims.py` keeps its own dedup: choosing *which* of two
entries to keep is a decision the caller has to make. Four mutations, four
killed.

The lesson is the one about where a guard lives. A crash caused by a message
this client sends belongs to the encoder, not to whichever tool happened to
learn about it.


**A -- and the wall between two seas stopped being a ladder (2026-09-07).**
The pass that cut the water plane at region borders left a named debt: the
walls that close the step between two sea levels came out *striped*, and the
note said fixing it meant "a per-vertex flag and a branch in the water
shader, which is not this pass's work". It is this pass's work.

The cause is one line of the shader nobody had to change: every wave term is a
function of world x and y. On the sea that is a surface. On a wall it varies
along one axis only -- so the wall drew as a ladder of bands along the whole
border, and from inside our own region that reads as the border being broken
rather than as water.

So the water mesh carries six floats a vertex instead of three: where the
corner is, then which way its face points before any wave leans it. `(0, 0, 1)`
for the sea, horizontal for a wall, and the shader skips the entire wave block
for anything that is not facing up. A wall is the *side* of a step, and a
vertical face of water reflecting the sky at a grazing angle is what it should
have been all along.

Two details are load-bearing.

The wall's normal points **away from the higher sea**, which is the direction
a back-face cull would want -- and nothing culls here, so the shader turns it
toward the eye instead. Without that the wall is drawn from its far side with
the Fresnel term inside out, reflecting the ground where the sky should be.
And the underwater flip is for the surface only: a wall is vertical and has no
up side, so flipping it there would undo the turn it just got.

**The test is not a picture of stripes.** That would need a threshold nobody
can defend. It is what stripes *do*: they travel. Advance the two wave phases
and a striped wall changes; a wall that takes its own normal does not, and the
frame is byte-for-byte identical.

That alone would pass for a viewer that had quietly switched the sea's waves
off, so it is paired with **the same camera looking at the same place with the
step removed** -- no wall, ordinary sea, and it has to ripple. Measured: with
the step, 0 of 16,384 channels move; without it, 4,025 do. A third test
forbids the cheapest fix of all, which is not drawing the wall: a hole there
shows the sky through it, which is the picture the walls exist to prevent.

Two more pairs came out of the battery, aimed at the normal itself. A wrong
normal does not draw a wrong-*looking* colour, it draws a plausible one, so
what checks it is the Fresnel term -- the one thing in this shader that asks
the normal a sharp question. Standing square in front of a wall, how much sky
the surface shows *at a grazing angle* cannot matter: turn that term from
nothing to everything and the frame must not move. A normal left pointing up
is seen edge-on from there and one never turned toward the eye points away
from it, and both read as fully grazing, which is the opposite of the truth.
The far-side camera is the half that bites: the near-side one stands where the
built normal already faces and cannot see the missing turn at all.

Twelve mutants, eleven killed. The survivor is equivalent by construction --
flipping a wall underwater is undone by the turn toward the viewer directly
below it. One timed out at ten minutes under a load average of 17, which is
the software GL stack and not this repo; planted by hand afterwards it fails
in 0.67 s.

**A -- nothing here had ever run for more than a few minutes (2026-09-07).**
Every measurement in this project so far has been of one frame or one tick.
That is the wrong shape for half of *without crashes*: a viewer does not
usually fall over on the frame it was wrong on, it is fine for an hour and
swapping at four, and by the time it dies the frame it died on says nothing
at all about which container filled. There was no way to ask that question,
so there is one now.

`viewer3d/health.py` is a probe over a named set of numbers, sampled on a
cadence and written one JSON object per line; `--soak-log PATH`,
`--soak-interval` and `--run-seconds` turn it on, and `tools/soak_report.py`
reads the log back. Fifty-odd gauges, chosen by reading for the *shape*
rather than by suspicion: a dict or a set keyed by something the world
supplies -- an asset id, a packet sequence, a line of text -- has no ceiling
of its own. The list is deliberately longer than the number of leaks anyone
expects to find.

Four decisions are what make the record worth reading.

**A cache filling is not a leak; a cache still filling at the end is.** The
report ranks by the rate over the *second half* of the run, not by the total
change. Sorting on the total puts them exactly the wrong way round: a texture
cache that filled in the first minute and sat still for four hours is the
largest number in the file and the least interesting thing in it.

**A counter is not a leak, and a counter that stops is its own failure.**
Packets received and frames drawn rise for the whole run by design, so they
are declared apart and never ranked as growth. They earn their place the
other way up: a session that went deaf an hour in raises nothing, drops no
frames, and leaves *every gauge in the report perfectly flat* -- which is
indistinguishable from a quiet region unless something is counting what
arrives. That reads as `stalled`.

**A gauge may not kill the viewer.** Every one is called inside a guard and an
unreadable one is recorded as `None`. A probe that ends the run at minute
three has told you nothing about hour four.

**And an unreadable gauge must not be a zero.** This is the one that needed a
guard rather than a decision. A gauge naming a cache somebody has since
renamed would read zero on every sample -- perfectly flat, which in this
report is exactly what a container that never grew looks like. A four-hour run
would come back clean because it was blind. So the walk tells *absent* (no
circuit before login, no world view before a handshake: zero, and ordinary)
from *misspelt* (raise), and a test builds a real scene, a real renderer and a
real circuit and fails if any gauge is unreadable against them.

`gc.get_count()` was tried and dropped. It counts allocations since the last
collection, so it swings every frame and reports "growing" in about half of
all runs whatever the viewer is doing, and one false row in every report is
how a report stops being read to the bottom. `sys.getallocatedblocks()`
answers the same question without the noise, and sits beside RSS and the
thread count.

43 tests, none of which runs a viewer -- the probe's contract is that it reads
other people's containers without raising, and the report's is that it can
tell a filling cache from a leaking one, and neither becomes truer for having
a GL context in the room. Twenty-seven mutants planted, twenty-six killed. Two
of the three that survived the first pass are tests now, and both are the same
lesson in different clothes: **a wrong number that is still a plausible number
needs a test that separates it from the right one, not a range check.**

- RSS read from `/proc/self/statm`'s *first* field is the whole address space
  rather than what is in RAM. Both are byte counts of an entirely plausible
  size, so no bound tells them apart -- and reading the first would report a
  reservation as a leak and miss a real one behind an allocator that had
  already reserved the room. Half a gigabyte of anonymous mapping nobody
  touches separates them exactly: 537 MB of address space, 0.1 MB resident.
- A missing *counter* reading zero rather than raising, which is worse than the
  gauge case it was already guarded against: a counter flat at zero for the
  whole run is reported as `stalled`, which is a loud and completely wrong
  finding about the session having gone deaf.

The one survivor is equivalent by construction: dropping the "fewer than two
points" guard in the rate is caught by the "zero span" guard immediately below
it.

**A -- rubbish into every decoder, before anyone logs in to the main grid
(2026-09-06).** The other half of the first priority is *without crashes*, and
the largest single source of one is still ahead of this project: B. A grid
this client has never met sends packets this client has never seen, and the
difference between a decoder that raises `ValueError` and one that raises
`IndexError` is the difference between a dropped packet and a viewer that goes
down.

`test_decoder_fuzz.py` puts random bytes through all sixteen decoders that
read bytes off the wire -- the message template, zerocode, terrain layers,
`TextureEntry`, `ExtraParams`, the compressed object blob, the parcel overlay,
texture animation -- and fails on any exception outside `ValueError`,
`TypeError`, `KeyError` and the project's own two decode errors. Nothing
raised, across four seeds and about eight hundred thousand calls off the
suite, and 385 inputs x 16 decoders inside it.

"Nothing raised" is worth nothing on its own, so the same two guards the HUD
sweep uses are here: every decoder has a floor for how many of the random
inputs it must have *accepted* -- run its body rather than its length check --
and a second test fails if a decoder is added to the sweep with no floor
beside it.

Two things are worth keeping from building it.

**A corpus of uniform noise is not enough for a decoder with a marker byte.**
Deleting zerocode's "a marker at the end of the packet has no count byte"
guard survived the first version of this sweep: reaching it needs the zerocode
flag set in byte 0 *and* a zero as the last byte, which random bytes produce
about once in five hundred. Three shapes noise almost never makes were added
-- flag set with a zero tail, a field of nothing, a field of all-ones -- and
that mutant dies now. Four planted crashes, all four caught.

**Fuzzing terrain is expensive and the interesting cases are short.** A 2 kB
blob of noise is thirty-odd patches, each an inverse cosine transform, and the
two terrain decoders were four of the file's five seconds. They are fed the
first 256 bytes: the cases worth having are an empty blob, a header with no
body, a patch cut off in the middle, and truncation makes more of those, not
fewer. The file runs in two seconds now.


**A -- the camera stopped standing inside the hill (2026-09-06).** Found the
way the last three were: render the thing and look at it. A third-person
camera at the foot of a ridge drew a flat green wall across the top of the
frame with the sea visible underneath it, which is what the *inside* of the
terrain looks like -- the underside of the ground above, and the world seen
out past the edge of it below.

Nothing in the camera had ever heard of terrain. The default preset sits ten
metres behind the avatar and 3.2 m up, so any slope steeper than about
eighteen degrees put it in the ground; the orbit camera pitched down did the
same on flat land the moment it went below the target. Both are ordinary
things to do.

`eye_clear_of_the_ground` pulls the eye in along the line to the target until
it clears, rather than lifting it: shortening the distance keeps the direction
the viewer asked to look from, where raising the eye silently changes the
angle. It is also what a camera does in any viewer when you back it into a
wall.

Three details are load-bearing and each has a test that fails without it:

- **It is a march, not a bisection.** A heightfield along a line is not
  monotone. Given a ridge near the avatar, clear air beyond it and the eye
  buried further out, a bisection probes the middle, finds the pocket clear and
  converges *behind* the ridge -- a camera nine metres away with a hill in the
  way. The march starts at the target and stops at the first blocked sample,
  which is a ray cast and cannot do that. This was the one mutant that survived
  the first battery: the original test's ridge happened to sit where the
  bisection's first probe landed, so both answers agreed.
- **The sampler is the drawn surface, not the heightmap by the metre.**
  `terrain_mesh_from_heightmap` puts the corner samples on the region's
  corners, so 256 samples are laid out 256/255 m apart and the middle of the
  region falls *between* samples 127 and 128. A sampler indexing by `int(x)`
  would agree at the corners and be most of a metre out at the far edge --
  wider than the clearance it is being compared against.
  `RegionHeightmap.height_at` interpolates the same span, and there is a test
  that asks it for every vertex the mesh builder emits.
- **Off the region there is no ground.** The mesh covers exactly the region's
  square and draws nothing beyond it, so a camera out over the void is not
  inside anything; clamping to the edge sample there would invent a hill that
  is not on the screen and shove the camera up over it.

First person is deliberately *not* held off the ground. The eye there is the
avatar's own head; if the simulator has put that inside a hill, moving the
camera only makes the picture disagree with where the avatar is standing. That
exemption is also how the GL test shows its own negative: the same buried point
rendered in `eye` mode gives back the upside-down frame -- terrain at the top,
sky at the bottom -- that the held camera no longer draws.

The eye was being worked out in *three* places with the same three-way branch
copied out: `view_matrix`, the water pass, and `pick`. They are one method now,
`Camera3D.eye()`. That was not tidying: with three copies, a camera held off
the ground in the picture would still have cast its pick ray from underground,
and the water fog would have been computed for a viewer who was not where the
picture was taken from. There is a test that picks a prim lying between the two
eyes and requires the held one to miss it.

Twelve mutations, twelve killed after the ridge test was rebuilt.

What this does *not* do is occlusion. The camera still looks through hills and
through prims that stand between it and the avatar; only its own position is
constrained. That is a raycast against all geometry rather than against the
heightfield, and it is a separate piece of work.


**A -- and the moon, which is what a night sea is recognised by
(2026-09-06).** The third and last application of the same argument, in the
same shape: `_MOON_IN_SKY_GLSL` beside `_SUN_IN_SKY_GLSL` and
`_CLOUD_IN_SKY_GLSL`, one string compiled into both programs, with
`_moon_in_sky(scene)` the one reading of the scene and `_bind_moon_in_sky` the
one place its seven uniforms are set. `_render_sky` lost five more loose
keyword arguments. The water pass binds the moon itself rather than living off
whatever the sky pass left on unit 4 -- that pass may not have run at all, and
an inherited sampler would put the terrain in the sea.

Order matters and is now stated once instead of twice: gradient, then moon,
then the cloud layer over both, then the sun over everything. The sea builds
the same stack along the reflected ray. Folding the sun in was algebraically
free -- `mix(colour, sky, m) + m*sun` is `mix(colour, sky + sun, m)` -- and the
moon and the cloud join it there.

**The stars are deliberately left out, and that is now asserted.**
`star_field` is a field of points a twentieth of a degree across, sampled once
per pixel with no filter of any kind. In the sky that is fine, because the ray
varies smoothly from pixel to pixel. Off a rippled sea it does not: the
surface turns the reflected ray by degrees between neighbours, so the field
would be sampled at random and come back as white speckle -- worse than no
stars. The moon has no such problem; a disc smeared along a rippled surface is
a moonpath, which is exactly what a moonlit sea looks like. There is a test
that says the sea is unchanged between `star_level` 0 and 500, so a future
reading of *"the sea shows the sky back"* cannot quietly put them in.

Ten mutations, and the tenth pass is the reason to keep running these: nine
died and **one survived every single test in the file**. Deleting the
`* u_moon_level` from `moon_in_sky` -- drawing the disc at full brightness
whatever the region asked for -- passed 218 of 218. The cause is that every
test ran the moon at nine tenths, and at nine tenths the middle of the disc
already clamps at 255, so nine tenths and a full moon are the same pixel. The
test that closes it compares the *peak rise over a moonless frame* at levels
0.1 and 0.3, where nothing clamps: measured 25 and 74 levels, and a third of
the light has to arrive as a third of the rise. Under the mutation both come
back as 138. This is the same failure as the `u_horizon` survivor recorded
above -- a test that measures where the quantity saturates cannot see the
quantity -- and it is the second time in two passes that it has been the one
survivor.


**A -- every control in both HUDs, pressed (2026-09-06).** The other half of
the owner's first priority is *without crashes*, and a viewer's crashes do not
mostly live in the renderer. They live in the widgets. `LoginScreen.resize`
was exactly that: a call to a method pygame_gui has not got, raising out of
the event loop the moment anyone dragged the window, uncaught for as long as
it has existed because no test had ever called the method.

`test_viewer3d_hud_events.py` presses all sixty-odd buttons in the 3D HUD and
the twenty in the map HUD, finishes every text entry, moves the slider, picks
a row that does not exist from every list, closes and resizes every window,
and sends the keys the app forwards -- with every window open and every
callback supplied. Nothing raised, which is the answer one wants and not one
worth much on its own, so two things guard against it being vacuous: the sweep
counts what it touched and fails if it found almost nothing, and it names the
callbacks a press can reach with no selection made and fails if any of them
never fired. Planting one bad call in `_hide_all_menus` takes two of the four
tests down.

**A -- the two textures nobody draws, settled (2026-09-06).** The live day
cycle names *five* real texture ids, not three. Two of them nothing here has
ever read: the water's `transparent_texture` and the sky's `bloom_id` -- and
the second was not even on the gap list, which is how an omission hides.

Both were fetched from this OpenSim's own asset service and looked at, which
is the only way to answer what they are.

- **`transparent_texture` (2bfd3884) is opaque.** A 256x256 sheet of flat
  blue with faint ripples painted into it, and no alpha channel anywhere --
  every pixel measured at 255. Despite the name it is not a transparency map;
  it is the picture a viewer lays on the water when it is not drawing a
  surface. This one draws a surface: the region's own normal map, its own
  Fresnel numbers, and its own sky in the mirror. The sheet is a lower-fidelity
  version of what is already there.
- **`bloom_id` (3c59f7fe) is a glow sprite.** A white disc with a radial alpha
  falloff. It is what a bloom pass multiplies over bright things, and there is
  no bloom pass here -- the same standing as `blur_multiplier`, which is the
  other half of that same absent pass. Whether it is *the sun's* halo in
  particular the document does not say, and the sky already draws its own from
  `sun_disc`; using it there would be a guess wearing the region's colours.

So both are parsed -- the document says them, and a later pass that grows a
bloom wants the id -- and both are kept out of `texture_assets`, which is the
one list the fetcher works from. A texture nobody draws costs a round trip and
a slot in the texture budget for nothing. Six mutations, six killed, including
the two that put either id back into the fetch list.

The point worth keeping is the method: *fetch the asset and look at it.* Two
passes of this handoff described `transparent_texture` from its name alone and
got it backwards.


**A -- the HUD was drawn for the monitor, not for its own window
(2026-09-06).** Found by taking a screenshot of the running viewer at
1280x800 and looking at it, which is the second time that has been the whole
technique. Two thirds of the frame was an empty chat window with "no chat
yet" in it, and the world was behind it.

The chat window was not the bug. `_auto_ui_scale` asks how large a pixel is
on the *monitor* -- 2.0 on this one -- and nothing then asked whether the
window could hold a HUD at that size. At scale 2 in a 1280-wide frame the
layout has 640x410 logical pixels to work with, and it does not fit in that:
the chat window came out 890x550, the heightmap window sat with thirty-five
of its 750 pixels on screen, the inspector and the asset viewer were each
larger than the whole frame, and the login panel ran off the top and the
bottom. Every widget was equally oversized.

`vibestorm/viewer/ui_scale.py` caps it: the scale the monitor asks for,
brought down to what the window holds of `UI_DESIGN_SIZE`, in the same
quarter steps and rounded down, asking both sides -- a letterbox window holds
two of the layout across and one of it down, and scaling to the wide side
puts the status bar off the bottom. The cap stops at 1.0, so a small window
at a scale of one is left alone: shrinking the type in an ordinary small
window was never the complaint. The HUD and the login screen both read it,
which also means the type does not change size between logging in and
arriving, and both keep the scale *as asked for* beside the one in use --
deriving the next from the current would ratchet, and every shrink would be
permanent.

The owner's default window is 1180x820 at a scale of one, so at two it is
2360x1640 and holds the layout exactly; nothing there changes. What changes
is the shrunken window, and the owner shrinks the window -- *"we have around
14fps. that raises to 20fps if i shrink the window"*.

**And the status bar had never been on the frame at all.** It is anchored to
the bottom, and pygame_gui reads a bottom-anchored rect's `y` as an offset *up
from the bottom edge*. It was given `sh - status_h`, an absolute coordinate,
so the bar landed at `sh + sh - status_h`: one whole window below the window,
at every size and every scale since it was written. Both HUDs had the same
line. That is why the framerate could be moved out of the diagnostics panel
into the status bar in the fourth pass without anyone noticing it had gone --
`Pos: 128.0, 128.0, 6.0 | Sim: Vibestorm Test | Parcel: Your Parcel` and
`fps=62 objects=32 avatars=1` are on screen for the first time now.

And once it was visible, the type in it was clipped. A `UIPanel` spends three
pixels at each edge on its border and shadow, and it is the *container* inside
those that clips: at 24 pixels tall both status labels hung five pixels over
the bottom of it, and every menu-bar button hung three. The bars are 34 and 30
now, and a test walks each bar's container and asserts every child is inside
it, so a theme with thicker chrome fails rather than shaving the text.

**And the login screen's checkbox pushed its own caption off the panel.**
pygame_gui puts a checkbox's text to the right of the *rect*, not beside the
box, and this one had been given the same 280-pixel width as the text entries
above it -- so "Remember Credentials" sat a hundred and twenty pixels past the
edge of the glass, on the starfield. It is square now. The panel's rectangle
was also worked out twice from the same four lines, once in `_build_ui` and
once in `draw`; it is `panel_rect`, set where the widgets are placed and read
where the glass is drawn, and every widget is asserted to be inside it.

**And `resize` on the login screen crashed.** It called
`manager.clear()`, which pygame_gui has not got, so dragging the window while
the login screen was up raised `AttributeError` out of the event loop. It is
`clear_and_reset()`. Nothing caught it because nothing in the suite had ever
resized anything -- the whole method was unreachable from the tests, and
writing the first test that called it is what found it. Nine mutations on the
cap, all nine killed, two of them only after a letterbox case was added: with
every test window a scaled copy of the design frame, asking only the width
and asking only the height give the same answer.


**A -- the sky turns, and the stars turn with it (2026-09-06).** The
last thing in the night sky that was this client's own idea rather than the
region's. `star_field` hashed a *world* direction, so the field was nailed to
the region: the moon crossed a sky that never moved, and the same two stars
sat over the same two hills at every hour of every night.

The document does say how the sky turns, in a field already being read for
something else. `sun_rotation` takes `SUN_REFERENCE_DIRECTION` to where the
sun is -- which is to say it takes the celestial sphere's own frame to the
world -- so the images of the three unit axes are that frame, and dotting a
ray with them is the way back into it. `celestial_axes` returns those three,
the sky shader turns each ray in before hashing it, and the stars are fixed to
the sphere while the sphere turns under the day cycle.

Two things make this more than a guess.

**Either rotation would have served, and that is the evidence.** At every
keyframe of the live cycle `moon_rotation` is `sun_rotation` followed by a
half turn about Y -- the two bases come out identical with X and Z negated --
so the sun and the moon are on *one* turning sphere rather than two. The sun's
is taken because it is meaningful by day as well, and there is a test holding
the pair to that agreement, so a cycle where they part company is a failing
test rather than a sky that quietly drifts. A consequence worth stating: this
fixes the moon among the stars too. The real moon drifts about thirteen
degrees a day against them; the document describes no such drift and inventing
one is not this viewer's business.

**A frame turned the wrong way looks exactly as right.** Turning a ray *into*
the sphere's frame and turning it *out of* the sphere's frame both give stars
that move with the night, in opposite directions -- so a transposed frame
passes any test that merely asks whether the field moved, and drifts the stars
backwards past the moon. What closes it is a quarter turn about the camera's
own up axis: turn the sphere about +Z and turn the camera by the same quarter
about +Z, and the camera's basis turns with it exactly, with no roll, so the
two frames are the same picture of the same sky. Measured: 17 of 20 star
pixels land on the same pixel, and turned the other way, none of 20 do.

Ten mutations and ten dead, with two familiar shapes in them. Hashing the
world direction again takes 117 tests down rather than one, because
`fixed_to_the_sky` then has no reader, GLSL optimises the three uniforms out,
and binding them raises `KeyError` on the first frame -- the same loud failure
the cloud layer gave. And the one survivor of the first run was the reset
branch: dropping `celestial_axes` from what a *lost* environment restores
passed, because the test that watches that branch rolled the region's moon
without turning its sun, so "back to the default" and "left as this region had
it" were the same answer. It turns the sun now.


**A -- the moon hangs the way the document hangs it (2026-09-06).** A
correction, and the handoff is the thing being corrected. It has said for two
passes that *"the document says nothing about which way up a moon hangs"*, and
that the face therefore takes world up. Both halves are wrong.

`moon_rotation` is a **quaternion**, and a direction would have done if the
orientation were not meant to be read. It takes `MOON_REFERENCE_DIRECTION` to
where the moon is; it takes the two axes perpendicular to that reference to the
two across the moon's face. `moon_face_axes` reads them, and they go to the sky
shader as two uniforms rather than being manufactured in it.

Crossing the moon's direction with world up was not merely less well founded.
**It flips sign at the meridian** -- `cross(up, moon)` points one way while the
moon is east and the other way once it is west -- so the face turned a half
circle in a single frame, at the top of the moon's arc, every night. It also
had a direction with no answer at all, straight up, where the cross product is
nothing and every texture coordinate on the disc came out NaN; that needed a
special case, and the special case is gone with it. There is no direction left
that is special.

On the cycle in hand the two readings agree everywhere except that flip: the
default cycle turns its moon about -Y throughout, which leaves +Y fixed and
swings +Z along with the moon, and that is exactly what the cross product
computes while the moon is in the east. So a screenshot cannot tell them apart
and a keyframe cannot either. What can is a *rolled* moon -- two documents may
put the moon in the same place and hang it differently -- which is what the
tests use, and what a hand-written day cycle is free to do.

Twelve mutations, all killed, three only after the tests grew. One of the three
is worth keeping: **turning a vector by a quaternion is only a rotation while
the quaternion is a unit one**, and the parser takes what the document writes.
A half-root-of-two about X sends the moon's +Y to 2e-16 of itself -- not a
short vector, a rounding error -- and normalising that returns whichever way
the error happened to point, in three texture coordinates on a disc. So the
guard is a *tolerance* and not a test against zero, which is where it started
and where nothing could reach it.

**A -- there is a sun in the water (2026-09-06).** The gap the handoff has
been naming for three passes: *"a specular highlight off the wave crests is the
most recognisable thing about the SL sea from a low camera, and nothing draws
one."* There is one now, and it is not a specular model.

It is the sun the sky pass already draws, seen in a mirror. `_SUN_IN_SKY_GLSL`
is one string compiled into both programs -- the disc at the size `sun_scale`
asks for, and the haze around it -- so the sun in the water cannot drift from
the sun in the sky. That is the same argument `sky_at_height` was factored out
for one pass ago, taken one step further, and it is the reason there is no
shininess constant anywhere in this: **a mirror does not need a reflection
model, it needs the thing being reflected.**

What it cost the shader was the reflected ray. The water pass had been working
out only its *height*, because that is all a vertical gradient wants; a sun is
a direction and not a height, so `reflect` is computed in full now. Three
multiplies.

**And what breaks one round highlight into a glittering path is the region's
own normal map**, which landed in the pass above. Drawn on the sines it is a
line of repeating blobs; drawn on the map it is flakes, converging toward the
sun and widening toward the viewer, which is what a sea looks like.

**The wall at the horizon came back, by another route.** The far sea turns into
the sky it meets, and "the sky it meets" had been the horizon *colour* -- which
is the whole sky at the horizon only for as long as nothing else is drawn
there. The sun is drawn there, and its haze reaches tens of degrees, so a low
sun put the far water several levels under the sky directly above it: a line
along the entire horizon, the exact defect the haze exists to remove. The far
sea now mixes toward the horizon colour *plus the sun along this pixel's own
bearing*. Measured on a column three degrees wide with the sun twelve degrees
off it and six degrees up, the step across the horizon is 42 levels the old way
and 17 the new.

Fourteen mutations, thirteen killed. The survivor is taking that horizon sun
off the whole view ray rather than off its flattened bearing: haze only reaches
full strength past nine hundred metres, and from any camera near the water a
ray that far out is level to within a degree, so the two agree everywhere the
answer is used. The flattened one stays because it is the one that is *right* --
the far edge of the plane is at the horizon, and a ray sloping below the
horizon is asking the sky for a direction the sky does not have.

One thing to know before touching this again: **the sun's own bearing is not a
usable test camera.** A frame looking straight at a low sun has the disc in it,
and a disc is a hard edge by design, so any measurement of how smoothly the sea
meets the sky reads the disc's rim instead. Ten to fifteen degrees off is where
the haze is still most of its strength and the disc is outside the frame.

**A -- the sea is drawn on the region's own surface (2026-09-06).** The last
of the three textures the day cycle names. `normal_map` is a 256x256
tangent-space normal map -- a wind-ripple sheet whose crests run along v and
travel along u, blue 234 to 255 so the surface is nearly flat and the slope
lives in red -- and six sines had been standing in for it. Screenshot the sea
from forty metres up with the sines and it is corduroy: regular stripes running
to the horizon, which is what three octaves of two waves each still is.
Screenshot it with the map and it is chop.

The map is laid **in each wave's own frame**, along its heading and across it,
and slid along that heading by that wave's phase -- so the region still decides
which way its water runs and how fast, and what the texture supplies is the
shape. Two samples replace six sines, and no harmonics at all: the harmonics
exist because a small sum of sines is periodic, and a normal map is already a
whole spectrum.

Four things came out of it.

**It is the one texture in this renderer drawn without anisotropic
filtering**, and it is the surface that looks most like it needs it -- a
two-kilometre plane seen almost edge on is the case anisotropy exists for. It
is also the most expensive possible place to spend it: the sea fills half the
frame and is sampled twice, once per wave. Measured in one run on llvmpipe at
1280x800, the water pass alone is **28.4 ms at sixteen samples against 10.4 at
one**, with the sines it replaces at 8.2. And nothing is lost by dropping it: a
normal map on a mirror is read for which way the surface leans, not for its
detail, so a mip level that blurs several ripples into one draws a calmer sea
rather than a wrong one -- which is what the far water is meant to look like,
and is exactly why the sines fade themselves out with distance. Screenshotted
at one sample and at sixteen from both cameras, the frames are
indistinguishable.

**A tile of a texture is not one cycle of what is drawn on it -- again.** This
is the cloud mistake exactly, made a second time within a day, and it is now
the thing to check first whenever a document's number meets a texture. Laid a
wavelength to a tile the sea came out at ten centimetres a ripple, which mips
to a flat sheet over all but the nearest water; the first screenshot of it was
a sea with no waves in it at all. The map's own dominant frequency is the
answer, and it is measurable: the power spectrum of its red channel across u
peaks at **nine cycles a tile**, with a broadband tail out to the pixel.
`WATER_NORMAL_RIPPLES_PER_TILE` is nine, so a ripple on the map comes out the
length `normal_scale` and the dispersion relation asked for, and the tail
underneath it is the fine chop the sines had to invent.

**A normal map is a normal, and the sines beside it are a height.** The
mapped path inherited the sine path's minus sign -- `vec3(-slope, 1)`, which is
right for the gradient of a height field and wrong for a stored normal. It
turns every crest into a trough. From above that is the same sea; from the
waterline it is the opposite one, because a surface leaning into the camera
sends the reflected ray up into the zenith and one leaning away sends it down
past the horizon. The mutation battery is what found it: the sign flip survived
every test, which is a way of saying no test looked along the water.

**A camera looking straight down cannot see which way a surface leans, only
how far.** That is why the frame tests here need two cameras between them.
Straight down, a lean of a given size reflects the same sky whichever way it
points, so a painted map with 0 on one half and 255 on the other -- opposite
leans, equal size -- draws exactly the same sea for both, and three separate
mutations survived on it: the map laid on the ground axes instead of the wave's,
the tangent never turned out of tangent space, and the across half of it
dropped. The painted halves are 0 and 128 now, which differ in *size*, and the
tests that ask which way a thing leans use a uniform sheet and the grazing
camera.

Twenty-four mutations, twenty-two killed, eight of them only after the tests
grew -- and one of the eight was the real defect above rather than a missing
assertion. Two of the eight closed by moving the assertion out of a frame:
**a texture unit collision cannot be seen from a frame that turns terrain
off**, and every test that reads the sky or the sea turns terrain off in order
to read it. So the units are asserted directly, as a set: the moon's, the
cloud field's and the sea's are distinct and none of them is one of the
terrain pass's four.

The two survivors are both accepted, and both for the same reason as
`WAVE_SLOPE_TOTAL` before them -- **there is nothing on the wire that could
disagree**:

- *Halving the lean the map produces.* `u_ripple.z` is `scale_above` times
  `WATER_WAVE_STEEPNESS`, and that constant is this viewer's invention, so a
  uniform factor between the two is a sea no document contradicts. What it
  would be visible against is the *sines*, since the two paths swap over mid
  session and a jump in roughness as the texture lands is a real artefact --
  but the sine path is three turned octaves and the map is a spectrum, and
  matching their roughness in a test is matching two things neither of which
  is defined.
- *Turning the across axis the other way.* That is the OpenGL/DirectX
  normal-map handedness question, green up or green down, and the document
  says nothing at all. The test that asks about the across half of the tangent
  therefore asserts the two leans *differ*, not which is which.

**A -- the clouds are the region's now, not a hash (2026-09-06).** `cloud_id`
is a 512x512 greyscale texture that **tiles seamlessly** -- measured: the mean
absolute difference between its left and right edge columns is 1.1, against
48 for an arbitrary pair. Three octaves of value noise had been standing in for
it. The noise stays as the fallback, because the asset arrives seconds into a
session and a sky with nothing in it for that long is worse than a sky with
invented cloud in it.

Two things about the numbers came out of having the texture at last.

**A density is a coverage, not a threshold.** `CLOUD_EDGE_LOW`/`HIGH` exist
because value noise is a field of smooth hills with no gaps: without a
threshold to carve holes it draws an even grey haze rather than clouds with sky
between them. A cloud texture already has the holes, so the density multiplies
it and the answer *is* the coverage. The two readings differ in linearity --
three flat fields evenly spaced in level come back evenly spaced in brightness
under a multiply and nothing like evenly under a threshold, which is what the
test asks.

**`cloud_scale` is a texture scale, and always was.** It had been read as a
noise cell -- 260 metres at scale 1 -- and one tile of the real texture holds a
sky's worth of shapes where a noise cell holds about one. Drawn a cell to a
tile the sky is a fine repeating mesh, which is what the first screenshot of it
was. `CLOUD_SCALE_METRES` is 2400 now, so the default cycle's 0.42 puts a tile
at about a kilometre against a layer 320 metres up, and the repeat falls where
perspective has already crushed it toward the horizon.
`CLOUD_NOISE_CELLS_PER_TILE` is the ratio that keeps the fallback reading at
the same size as the thing it stands in for, and `CLOUD_DRIFT_PER_SECOND` moved
with the unit -- the layer still crosses the sky at just under a metre a
second.

That also spends **`cloud_pos_density1` and `2`'s first two components**, which
have been sitting parsed since the sixth pass with nothing to offset into. They
are equal in all eight keyframes, so the second layer sits exactly on the
first and the sky is one field at the sum of two densities. That is what the
document asks for, oddly, and it is not a reason to invent a second scale for
the second layer to be interesting at -- the previous pass had drawn it at 3.7
times the frequency, which with a real texture is visible tiling. **Nothing in
a captured region can tell a shader that reads the second pair from one that
reads the first twice**, so the test hands them different offsets, which is the
only way to ask.

Eighteen mutations, all killed, three only after tests were added -- and all
three were the same lesson as the moon's: *the default cycle cannot see these*.
Both layers share an offset in it, and the two-tone field a plumbing test paints
is binary, so a threshold and a multiply agree on it exactly. Painted fields
have to be chosen to disagree.

One test detail worth keeping: **the cloud layer is mip-mapped**, so a painted
field at the region's own tile size averages to a flat grey before it reaches a
pixel. `CLOUD_TEST_TILE_M` is 400 metres against the region's thousand for that
reason -- narrow enough to put three or four stripes across the frame, wide
enough to stay above one pixel.

**A -- the day cycle names textures, and they are real (2026-09-06).** The
handoff has been saying for three passes that the moon is "procedural because
`moon_id` names a texture nobody here has fetched", and the same for `cloud_id`
and the water's `normal_map`. Nobody had tried. All three are **ordinary
textures behind the ordinary `GetTexture` capability**, served by this
OpenSim's own asset service, and all three fetch in a few milliseconds:
`822ded49` is a 256x256 tangent-space water normal map, `1dc1368f` a 512x512
cloud field, `d07f6eed` a 256x256 moon with maria and an alpha cut-out.
`bloom_id`, `halo_id` and `rainbow_id` are there too and fetch as well.

So a client that reads the day cycle's numbers and skips its ids is inventing
three things it could have downloaded. `RegionEnvironment.texture_assets()`
lists them and the session queues them **after the ground and before the
prims** -- the ground argument again, only stronger: there is one moon and one
cloud layer, against a region's worth of prims covering a few square metres
each.

`sun_id` is in the same document and is the **null UUID in all eight
keyframes**. That is the document writing *no texture*, not a field it forgot,
and a client that queues it waits forever for an answer that cannot come. Null
reads as absent here, everywhere.

This pass spends the moon. The disc's size still comes from `moon_scale`; what
is new is what is drawn inside it. There is no disc geometry in the sky -- the
whole moon is a dot product against a direction -- so the face needs two axes
across it, and this pass claimed the document says nothing about which way up
a moon hangs -- so it took world up, and swung to world north for a moon
directly overhead, where up and the moon are the same direction and their cross
product is nothing. **That claim was wrong**, and is corrected in the pass
above: `moon_rotation` is a quaternion and carries the face's own two axes.

Two things worth keeping:

- **The texture is bound to unit 4, not unit 0.** The terrain pass owns 0
  through 3 for its four ground textures. The two passes never run together,
  but a sampler left pointing at unit 0 reads whatever was bound there last,
  which on a frame with terrain in it is the ground -- stretched across the
  moon. The GL test paints the moon in two flat halves rather than using a
  photograph, so that "the moon is not one colour" and "the moon is not the
  ground" are the same assertion.
- **A named texture that has not arrived is not a hole in the sky.** It is
  fetched over the network in the middle of a session and there are seconds
  between the day cycle naming it and the bytes landing. The plain disc is the
  shape the texture goes on to fill, and it is drawn until then.
- **The face's alpha cuts the disc rather than fading toward the fallback.**
  Where a moon texture is transparent what is behind it is the sky, not a
  paler moon -- and this asset is a photograph inscribed in its own square, so
  its four corners are transparent and the wrong reading puts four pale spurs
  off the moon. Costing a test: a face whose alpha is only ever 0 or 255
  cannot tell "the alpha cuts the disc" from "the alpha multiplies the colour"
  from both at once, and the real asset's rim is neither. A half-transparent
  white patch answers all three at once -- 128 if the alpha is spent once, 255
  if it is ignored, 64 if it is spent twice.

Twenty-four mutations, all killed, but seven of them only after the tests grew.
Three were the same shape: **the moon's face is a square mapped onto a circle,
and most ways of getting that wrong are invisible to most tests.** Scaling the
face about its centre leaves every quadrant a quadrant, so a test asking which
colour is where cannot see it; collapsing the two axes onto one still shows
both halves of a two-tone texture. The face the tests paint has a corner in
each quadrant *and* a patch in the middle, which answers "are the axes two
axes" and "is the face the size of the disc" separately.

The cloud field and the normal map are fetched and cached by this change and
not yet drawn -- they are the next two passes, and both replace inventions this
handoff has been apologising for. (Both landed the same day, in the two passes
above.)

**A -- the two waves were secretly the same wave (2026-09-06).** Screenshot
the sea from forty metres up and it is a woven mesh: a regular diamond lattice
running to the horizon, which reads as a broken renderer rather than as water.
From a camera at eye height it looks fine, which is why it survived the pass
that drew it -- a wave seen almost edge on shows only its profile.

The cause was not filtering. Both of the document's waves were being drawn at
one wave number, and **two sines of the same length crossing at an angle are a
perfect lattice.** No amount of fading or harmonics removes that; it is what
the surface *is*.

The document does say how far apart the two lengths are, in the same place it
says the speeds. `wave1_direction` and `wave2_direction` are 1.13 and 1.61
long, and that length is a speed -- and in deep water a wave's phase speed goes
as the square root of its length. So the ratio of the two lengths is the square
of the ratio of the two speeds: about two to one, and nothing about the pair
repeats in any direction a camera looks along. `normal_scale` still sets the
scale of the sea; what is new is that it sets the *mean* of the two rather
than both.

Only the ratio is physical, and deliberately so. Taken absolutely, dispersion
puts a 0.6 m/s wave at 23 centimetres long, which is a puddle -- the absolute
scale has to stay the viewer's, because it comes from `normal_scale` and
`normal_scale` is a texture repeat count and not a length. The clamp exists for
the same reason in reverse: a document whose second wave barely moves asks for
a first one hundreds of metres long, which is not a wave any more but a tilt in
the whole sea.

One consequence is worth stating because it looks like a bug: **making a wave
faster no longer makes it come round more often.** It is also longer, by
exactly enough that the period is unchanged. What the direction's length sets
is the speed the crests travel at, which is the phase rate over the wave
number, and that is what the test asserts now.

The harmonics from the previous pass stay and became a loop: three octaves
rather than two extra terms, each the pair before it at 2.3 times the frequency
and 0.7 radians off its heading. Dispersion alone still leaves a fine lattice
from above -- screenshotted at one octave to check -- and three octaves span
two radians of heading rather than one. Not four: on llvmpipe the water pass
measures 4.0 ms at one octave, 5.2 at three and 6.5 at four, for a difference
nobody can see.

**This whole class of defect is invisible to the tests here.** A GL test reads
single pixels and an interference pattern is not a pixel; the sea passed every
one of them while looking like woven mesh. What the tests can hold is the
plumbing under it, and one of those needed care: a frame drawn with the second
wave number wrong differs from a correct one *anyway*, through the distance
fade, which is measured per wave number. At 64 by 64 one pixel of that frame is
most of a metre, so the test uses 126-metre and 42-metre waves -- far enough
inside the fade that a difference has to have come from the crest.

Seventeen mutations, fifteen killed. One survivor was a badly written mutation
rather than a gap; the other is `WAVE_SLOPE_TOTAL`, the divisor that keeps a
sea with more octaves in it from being a steeper sea, and it is **not
observable**: `WATER_WAVE_STEEPNESS` is itself an invented constant, so a
uniform 1.65 factor between the two is a change no document can contradict.
The divisor stays because `WAVE_OCTAVES` is meant to be a number someone can
turn without re-tuning the steepness beside it.

**A -- the sun is the size the region asks for, and cloud finally casts
(2026-09-06).** Three more fields spent. `sun_scale` and `moon_scale` had never
even been parsed; `cloud_shadow` had been parsed a pass ago and read by
nothing.

The two scales replaced a pair of magic numbers that no region could reach: the
moon was `smoothstep(0.9990, 0.9994)` against the alignment and the sun was
`pow(alignment, 900.0)`. Both are now an angular radius scaled by the
document's own number, turned into the two cosines a `smoothstep` wants --
cosines because a dot product is all the sky shader has up there, there being
no disc geometry in the sky at all.

**The two constants are the old thresholds, read back out.** The moon's pair
reproduces to five places, and it is also where `DISC_EDGE_FRACTION` comes
from: 0.9994 is 0.9990's angle times 0.7746, so the soft edge was already
22.54 per cent of the radius and did not need inventing. The sun's is pinned
differently, because a 900th power and a smoothstep are not the same curve and
cannot agree everywhere -- what is kept is the angle at which the old falloff
had fallen to half, which the middle of the new edge is set to. That is a real
test rather than a tautology only because it is tight: the two radii are one
per cent apart, so a tolerance loose enough to be comfortable is loose enough
to let the sun draw at the moon's size and say nothing.

Every keyframe of the default cycle has both scales at 1.0, so *the document
gives no evidence at all for the direction*. What it gives is that 1.0 is the
unchanged size -- which is exactly what makes the direction readable, since a
value that never moves still fixes where the scale starts. Bigger is bigger and
zero is floored rather than allowed to collapse the two edges: a smoothstep
whose edges meet is a step, which draws an aliased dot rather than no sun.

`cloud_shadow` is the more interesting one, because *which* light it takes is
the whole of it. **It takes the direct light and leaves the ambient.** Cloud
over a landscape dims the sun and leaves the sky lighting everything, which is
why an overcast day has soft shadows rather than dark ones; taking it off the
ambient as well would make a cloudy noon read as dusk. It is also independent
of the clouds actually drawn, which are procedural noise and line up with
nothing -- the region says how much shade there is, and where it falls is not
in the document.

Two things about testing it. The first: **a face that takes no direct light
cannot show a change in the direct light.** The first version of the GL test
looked level at the side of a prim at noon, when the sun is straight overhead,
and read the same 597 with the shade at full and at a tenth -- a passing-looking
number that was measuring ambient twice. Looking down at the prim's top is what
makes the claim testable. The second: the ambient half of the same test has to
stay, because "it takes the direct and leaves the ambient" is two claims and a
test of the first alone passes on a shader that dims everything.

Twenty-three mutations, twenty-one killed and both survivors were redundant
code rather than missing tests -- the clamp in `_light_uniforms` duplicated the
one `cloud_shadow_scale` already does on the way in, and flooring a negative
scale to zero was dead under a floor that catches it anyway. Both are gone; the
clamp that remains is at the boundary where the document's number arrives,
which is the one place that has to have it.

**A -- and there is a world under it (2026-09-06).** A camera below the water
plane got a clear afternoon. The sky gradient, the sun, the clouds and every
prim in the region at full brightness, with the surface overhead as a faint
translucent sheet -- so the one state a swimmer is in was the one state the
viewer could not show, and a person who walked into the sea could not tell.

Four more numbers off the wire, three of them now spent. `water_fog_density`
(16) and `underwater_fog_mod` (0.25) together say how far a viewer can see,
and `scale_below` (0.2) says how far the surface bends the view from
underneath -- seven times further than `scale_above`'s 0.03 does from over it,
which is the right way round: from below a surface is a lens rather than a
mirror. `blur_multiplier` is the fourth and is still unread; there is no blur
pass to give it to.

The density is the interesting one, because **it is a ratio and not a
coefficient**. Taken literally as an extinction per metre, 16 x 0.25 puts
visibility at six centimetres. Nothing in the document says what the unit is,
so `UNDERWATER_REFERENCE_M` supplies one: the default cycle comes out at thirty
metres of clear sea, and a region that doubles its density halves that.

*Every pass that draws the world fogs.* Prims and avatars, the map-tile ground,
the region's own four ground textures and the flat terrain fill are four
programs and the fog had to go in all four; the shared GLSL is substituted
rather than copied, because four copies of it is four things to get wrong. Two
details in it are worth keeping:

- **Only the part of the line of sight actually in the water fogs.** A tower on
  the shore is seen through the water in front of it and clear air beyond, and
  fogging the whole distance would grey it out as though the sea reached the
  horizon at eye level.
- **The distance is from the eye, not the depth along the view axis.** Depth is
  cheaper and wrong at the edges of the frame: it draws water that thins
  towards the corners and slides as the camera turns, which is a thing being
  *more* visible the further off-centre you look at it.

*The sky is still drawn, and that turned out to be the right answer.* The first
version replaced it with the water's colour outright, which is true of almost
every direction and false of the one that matters. What a submerged camera sees
of the sky is the sky through however much water the ray crosses on the way
out, and that is the depth divided by how steeply the ray climbs: six metres
straight up, thirty-five at ten degrees above the horizontal. **The bright
circle overhead a swimmer sees comes out of that arithmetic rather than being
drawn.** Below one per cent the shader returns the fog and skips the sun, the
stars and three octaves of cloud noise, which is nearly all of a submerged
frame.

*The surface from below is a ceiling.* Its normal points down, so the angle is
measured the same way; and the two sides are not symmetrical the way they look.
Straight on they nearly are -- half sky and half sea from either side, which is
`fresnel_offset` -- but along the surface they are opposites: from above almost
everything coming back is sky, from below almost all of it is sea. So
underneath it draws as the water's own colour at `mirror` opacity and lets the
sky pass behind it supply the rest. The two agree without either knowing about
the other, because both take the same water off the same ray.

Above water this costs nothing measurable: A/B against the previous commit over
four runs on llvmpipe put the water pass at 4.7-5.6 ms either way. The fog is
one comparison against a uniform, and the uniform is zero in air.

Thirty-one mutations, thirty killed, and the survivor was **equivalent rather
than untested**: guarding each of the density's two factors against a negative
document was dead the moment the divisor was floored, since a negative product
floors to the same very long reach a zero does. The guards are gone.

Four of the thirty needed new tests written for them and three of those were
the same mistake in different clothes -- the fixture *is* the fallback, so
"refresh from the region and find the region's numbers" proves nothing at all.
Every plumbing test here now builds a day cycle whose water is unlike
`WaterSettings`' defaults in every field.

**A -- the sea has a surface now (2026-09-06).** Four things the region's
document says about water had been parsed and never used: `wave1_direction`,
`wave2_direction`, `fresnel_offset` and `fresnel_scale`. The sea was a flat
sheet of one colour -- `water_tint`, which is the water's own fog with a fixed
35 per cent of sky mixed into it -- with a static sine pattern laid over it to
stop it reading as a painted floor.

`WATER_SKY_REFLECTANCE`'s own comment had said what was wrong with that: *"A
real surface is nearly all reflection at a grazing angle and nearly all fog
looking straight down; without a Fresnel term one mixture has to serve for
both."* There is a Fresnel term now, so the mixture is per pixel:
`fresnel_offset` is how much sky comes back looking straight down and
`fresnel_scale` how much more there is along the surface, read as Schlick's
shape -- reflectance rising as the fifth power of one minus the cosine of the
viewing angle. That is a reading of the two names rather than of any
implementation, and it is worth writing down that **0.5 is nothing like
water's real reflectance straight down**, which is about 0.02. The number is
artistic and is used as given.

What comes back is not one colour of sky either. The reflected ray is worked
out per pixel and the sky is sampled *along it*, with the same gradient the sky
shader itself draws -- so looking down at the sea returns the zenith and looking
along it returns the horizon. Getting that backwards is not subtle: it draws
the sea as a second sky with the gradient upside down.

The waves are the two the document names, running the way it says at the speed
it says. The speed is the part nobody would guess: `wave1_direction` is a
heading **and** a speed in one two-vector, and its *length* is how fast. A
reader that normalises loses the speed; one that does not ties speed to
wavelength, so a faster wave comes out a shorter one. They are separated here
and the phases advance per frame, wrapped at a full turn -- shader floats are
single precision and an hour of viewing would otherwise put the angle past ten
thousand radians, where the waves visibly quantise.

Three things about it are this viewer's invention rather than the region's, and
two were forced:

- ~~**`normal_map` is still unused.**~~ Fetched and drawn in the eighth pass;
  the sines below are the fallback until it arrives. The *wavelength* and the
  *steepness* are still constants in `atmosphere` (`WATER_WAVE_LENGTH_M`
  divided by `normal_scale`, and `scale_above` times `WATER_WAVE_STEEPNESS`)
  rather than numbers off the wire.
- **There are four waves, not two.** Two sines draw a cross-hatch: a regular
  diamond grid that reads as corrugated iron, which the first screenshot showed
  immediately. Each documented wave is drawn again at 2.3 times the frequency,
  turned 0.7 radians off its parent's heading and at 45 per cent of its height.
  Deriving the extra pair from the two the region gave keeps the sea pointing
  where the region said even though the shape is invented. 2.3 rather than 2:
  an octave puts every second crest of the harmonic on a crest of its parent
  and the grid comes back at half the spacing.
- **Ripples fade out before they get too fine to draw.** Waves are about four
  metres long and the plane runs for two kilometres, so most of it is being
  asked for a ripple narrower than a pixel. Sampled once per pixel that is not
  water, it is moire -- a coarse pattern that crawls when the camera moves, and
  the one artefact that reads as a broken renderer rather than as rough water.
  `fwidth` gives how much a wave's phase changes across a pixel, and each wave
  fades as that approaches the rate it can no longer be carried: a mip level,
  computed the only way open to a surface with no texture to have mips of.

**The cost, measured on llvmpipe at 1280x800 with the sea filling half the
frame.** The water pass was 2.9 ms and is 5.1 ms; the waves are 2.8 ms of that
and the whole thing skips them when `u_ripple.y` is zero. Two changes paid for
most of the Fresnel term: `pow(x, 5.0)` is an exponential and a logarithm on a
software rasteriser and is multiplied out instead, and the reflected ray is
never built because only its *height* is wanted. Flat water is now cheaper than
it was before any of this.

Thirty-one mutations, thirty killed. Two survived the first pass:

- **The captured document's water frame is the fallback.** `WaterSettings`'
  defaults were read off it in the first place, so a `Scene` that ignored the
  region entirely passed every plumbing test -- the fixture's `fresnel_offset`
  *is* `DEFAULT_WATER_FRESNEL[0]`. The test now builds a day cycle whose every
  water field differs and checks each one arrives. This is the same shape of
  vacuous test as the one recorded under "Two Ways A Test Can Agree With A
  Bug", and it will happen again wherever a fixture is where a default came
  from.
- **Nothing measured the harmonics.** Making `turned` the identity -- so all
  four waves run along the document's two headings -- changed no test, which
  is to say the cross-hatch could come back unnoticed. What catches it is a sea
  handed *one* heading for both documented waves: built from those alone it
  varies along that heading and not at all across it, so the frame is measured
  along both screen axes and the smaller has to be substantial.

One is left deliberately alive: `WAVE_HARMONIC` set to 1.0, which makes the
finer waves the same size as their parents. It is a look rather than a claim --
the sea is still four-directional and still not a grid -- and a test pinning
the ratio would be asserting a constant against itself.

**A -- the sea stopped ending in a wall (2026-09-06).** Found by measuring
rather than by looking: a column of pixels from a camera three metres above the
water, with the elevation of each row printed beside it, showed a **step of
sixty-seven levels** across one row at the horizon. The reason is arithmetic
rather than a bug -- the water plane is drawn as the sky's own horizon colour
with a dark tint blended over it, so the two are bound to differ by exactly the
tint, and they simply abut. What is missing is the air in between.

Distant sea now fades into the sky it meets, and the alpha fades with it: the
opacity slider exists so a viewer can see what is *under* the surface, and at
the horizon there is nothing under it but sky, so leaving it translucent there
lets the sky through at the wrong brightness -- the same wall by another route.
The worst step across a row is now fifteen, and the ramp runs over about two
degrees of elevation.

`WaterSettings.fog_density` is deliberately **not** what drives this. That is
the fog seen from *under* the surface -- a different quantity in a different
medium -- and using it here would be reading the document to mean something it
does not say. `WATER_HAZE_NEAR_M` and `WATER_HAZE_FAR_M` are a rendering choice
and are sized off the region, not off the wire.

Eleven mutations, all killed. The one that had already happened is in the tests
as a docstring: the first version measured the distance in the **vertex**
shader, and the plane is two triangles more than two kilometres across, so
every fragment got the average of three corners a kilometre away and the whole
sea came out sky-coloured. A horizon with no wall in it, because there was no
sea either. It is measured per fragment now.

Two of the eleven survived the first pass, and both survived for the same
reason: **every camera in these tests stood near the world origin**, which is
also the region's corner. A shader handed a zero eye position draws almost the
right sea from there, and a haze that starts at the viewer's feet instead of a
region away shifts the near water by a few levels that no screenshot shows.
What kills them is a camera nine hundred metres out -- where the sea underfoot
is a kilometre from (0, 0) and would be drawn as sky -- and pushing the water
tint and the sky to opposite extremes so that a few levels of drift becomes
fifty.

**A -- the sky has weather in it (2026-09-06).** Every cloud parameter was in
the day cycle and none was drawn. Four of them were not even parsed:
`cloud_pos_density1`, `cloud_pos_density2`, `cloud_scroll_rate` and
`cloud_variance` are all in every sky frame, and `cloud_scroll_rate` is the
one worth noting -- it is a **pair** where nearly every other vector in the
document is a triple, so a reader that assumes three either raises or pads a
zero into a real axis.

Three of the four choices in this are choices, and the module says so:

- **Coverage.** The third component of each `cloud_pos_density` is how much
  cloud there is; the other two are an offset into `cloud_id`, a texture
  nothing here has fetched. Taken literally the coarse density is permanent
  overcast -- it never drops below 0.88 anywhere in the default cycle -- and
  that is because in the document it multiplies a *texture*, and the texture
  is what has the holes in it. With no texture, four octaves of value noise
  stand in for it and `CLOUD_EDGE_LOW`/`HIGH` say where its edge falls.
- **Scale and altitude.** `cloud_scale` is a bare number with no unit and
  `max_y` reads like an altitude without saying whose, so `CLOUD_SCALE_METRES`
  and `CLOUD_ALTITUDE_METRES` turn them into a size and a height. The layer is
  flat and the ray crosses it, which is what gives it perspective; near the
  horizon the crossing distance runs away, so it fades out before it can
  alias -- also where real cloud goes into the haze.
- **Drift.** `cloud_scroll_rate` has no unit either. Read as cells per second
  the default cycle's 0.5 blows the sky past in a blink; `CLOUD_DRIFT_PER_
  SECOND` puts it at about a minute to a cell, which is weather. It is
  accumulated **per frame**, not derived from the region's clock: that clock is
  a real time and would give the right answer, but it arrives with a time
  message every few seconds, so a layer driven by it sits still and jumps.

The fourth is not a choice, it is a fix. **`cloud_color` is an albedo, not a
drawn colour.** It is 0.41 grey at noon, against a sky this same module puts at
about 0.5 blue -- so used raw the clouds are *darker than what is behind them*,
which is a permanent thunderstorm over every region. Lit by the two lights the
frame already has (`ambient + sunlight_color`) it behaves: white with some blue
in it at noon, bright and warm at dusk when `sunlight_color` reaches 2.84, and
a dark blue-grey at midnight. None of that is chosen; all of it falls out of the
region's own numbers.

**It costs about five milliseconds of a 1280x800 frame on llvmpipe**, which is
what this machine renders with. The first version cost thirteen. Three things
got it down: three octaves rather than four, a two-component hash instead of
packing a vec2 into the three-component one, and -- the largest -- not
evaluating a whole noise field to multiply it by zero, which is what the
variance term was doing in every keyframe of the default cycle. Five
milliseconds is still real money at fourteen frames a second, so there is a
**Clouds** toggle in the render settings beside Sky and Water, and it is spent
at the uniform rather than in the shader: a zero cover skips the noise
entirely.

Twenty-five mutations, all killed -- but only after three survived the first
pass, and all three were the same blind spot: **the tests could see that there
were clouds and not what shape they were.** Brightness, colour, drift and
screen-independence were all covered; the layer's *geometry* was not. So
`dir.xy * altitude` instead of `dir.xy / dir.z * altitude` -- a flat sheet
pasted on the sky with no perspective at all -- passed everything, as did
removing the horizon fade, as did dividing by a hard-coded 100 instead of by
the region's own cell size.

What closes them is one measurement: how many pixels differ sharply from the
pixel to their left, counted in a band of the frame. It is a proxy for how
*fine* the cloud is there, and the three cases separate cleanly:

| | near the horizon | high up |
| --- | --- | --- |
| with perspective | 1004 | 17 |
| flat sheet | 0 | 0 |

and a 109-metre cell gives 1479 edges where a 400-metre one gives none, where
the hard-coded divide gives the same number for both. The horizon fade is the
same measurement read as brightness: eleven levels away from a clear sky just
above the horizon with the fade, a hundred and thirty-six without.

**A -- the night sky has a moon and stars in it (2026-09-06).** Both were in
the day cycle already, parsed and unread. `star_brightness` is the emphatic
one: the default cycle writes exactly **500** in both night keyframes and
exactly **0** in all six daytime ones, with nothing in between to interpolate a
guess from, so all a viewer has to do is normalise it.

The moon took a small piece of evidence. Nothing names the vector a
`moon_rotation` turns, and one keyframe agreeing with a guess proves nothing --
so the check is all eight at once: read as turning **+X**, the same vector as
the sun, the moon's elevation is the exact negative of the sun's at every
keyframe in the cycle, straight up at midnight and straight down at noon. That
is what `MOON_REFERENCE_DIRECTION` rests on.

Both were drawn procedurally in this pass. (`moon_id` names a real asset and
is fetched and drawn from the eighth pass on; there is no `star_id` in the live
document at all.) So the moon was a disc with a soft edge and no phase, and the
stars are a hash of the view direction: the sky is cut into cells, about one in
thirty holds a star at a hashed position inside it, and each is a small round
falloff with a hashed magnitude so the field does not read as a pattern. The
hash is of the *cell*, not of the screen, which is what keeps the stars still
while the camera turns under them.

Two things are deliberately not gated on night. The moon is up whenever its own
rotation puts it up, including in daylight, where the brighter gradient washes
it out on its own -- which is roughly how a daytime moon looks. And the moon
disc and the sun blob are both drawn a few times larger than half a degree,
because at this field of view the true angular size is a speck.

Sixteen mutations. Fourteen killed, one deleted as dead code, one equivalent
-- and the first run of the battery is the whole reason this section is worth
reading, because **six of the fourteen survived and every one of them was the
same test being vacuous.**

`test_stars_come_out_at_night_and_not_before` had been counting the *moon's
edge*. The star camera looks 80 degrees up; at midnight the moon is at the
zenith, comfortably inside a 60-degree frame, and its rim is exactly the sharp
bright thing the test was measuring. It passed with the star uniform wired to
zero. It also passed with a star in every single cell -- because when every
neighbour is bright too, a local-contrast measure reads nothing.

The second problem underneath it was resolution. A star is about a twentieth
of a degree across, and the shared 64-pixel test buffer spans sixty degrees --
so a star covers a twentieth of a pixel and whether it registers at all is
down to where it lands. At 256 the same frame reliably shows a couple of dozen.
The repaired tests turn the moon off, render into their own larger buffer, and
count sharp local peaks; the night frame has 22 and the daytime frame has none.

The one that could not have been caught by looking is *the star field is view
dependent*: add `gl_FragCoord` to the hash input and the night sky is still
completely convincing in a screenshot, and the stars swim about the moment
anyone turns their head. Testing it needs the same world direction to land on
a different pixel, and the way to do that without touching the camera is to
shift the **viewport** inside a larger buffer: every NDC is unchanged, so every
ray is unchanged, and only `gl_FragCoord` moves. Sixty pixels differ under the
mutation and none without it.

The two that did not die are worth naming as well, because neither is a gap.
`dir.z > 0.0` beside the star term was dead: `smoothstep(0.0, 0.12, dir.z)`
clamps, so it is already zero for every ray below the horizon -- the mutation
proved the code redundant rather than the test weak, and the guard is gone.
`u_star_level > 0.0` is likewise only an early-out for a multiply by zero.
Replacing the horizon fade itself with `1.0` does fail, which is the check
that matters.

**A -- prim textures have a memory ceiling (2026-09-06).** The renderer had a
prune and no bound. `_prune_object_textures` released whatever the region had
stopped referencing, which is exactly right and is not a limit: a region may
reference as much as it likes, and the uploaded set grew with every texture the
camera had *ever* passed over rather than with what was on screen. A mainland
region with a few thousand distinct 512-square textures is gigabytes.

The reason it had gone unfixed is a good one, and it is written into
`test/test_viewer3d_label_cache.py`: a least-recently-used *count* cap was
tried twice (4e8de78, 782c9f4) and reverted, because uploads happen inside the
per-frame draw loop, so the moment a region holds more than the cap it evicts
things that are still on screen and re-decodes them every single frame,
forever. That objection is right. It is answered here rather than ignored:

- The bound is in **bytes**, `OBJECT_TEXTURE_BUDGET_BYTES`, not in count.
- **Nothing the previous frame drew is eligible for eviction.** A frame's
  visible set barely moves from one frame to the next, so what that protects
  is, to within one frame, exactly what is about to be asked for again.
- If the previous frame's own set is over budget there is nothing safe to
  release, and the prune **stops rather than thrashing** -- and says so.
- `MAX_OBJECT_TEXTURE_EDGE` is the other half, and the half that cannot
  thrash at all: a 2048-square texture is 22 MB once mipmapped, and the
  simulator advertises `MaxTextureResolution: 2048`. Downscaling happens once,
  on upload, with `smoothscale` rather than `scale` -- the nearest-neighbour
  reduction throws away three texels in four and aliases, which is the same
  mistake as sampling without mipmaps.

**The budget is on the diagnostics panel**, which is not decoration. Its
failure mode is a viewer that gets slow and stays slow, indistinguishable from
a heavy region; `OVER BUDGET` is the line that separates them. The summary is
published at the *end* of the frame, not with the prune at the start, or it
would report the state the frame began in and never count what the frame
uploaded -- on the first frame of a region, zero megabytes.

Eighteen mutations, all killed, run with `PYTHONDONTWRITEBYTECODE=1` from the
start this time. Two were worth the trouble on their own: *the frame counter
never advances*, which makes every texture look like frame zero and quietly
disables eviction entirely, and *the summary is never published*, which leaves
a budget that is enforced perfectly and reported to nobody.

**Do not edit a source file while a mutation battery is running against it.**
The harness restores from a snapshot taken at its start, so an edit made
mid-run is silently reverted on the next restore -- and if the run is killed
part-way, the file is left holding whichever mutation was in flight. Both
happened here. `git diff` against the snapshot is the check.

**A -- textures stopped crawling (2026-09-06).** `Texture.filter` is
`(minification, magnification)` and every world texture set the first half to
`LINEAR`, so a texture was point-sampled however small it was on screen. The
terrain textures made it plain: they already called `build_mipmaps()` and then
set a filter that never consults one, so the memory bought nothing. Object
textures and the region map tile built none at all.

Mipmaps alone then over-correct, and the ground is where that shows -- a
surface seen along itself is squashed hard in one direction and a level coarse
enough for that axis throws away everything across the other. Anisotropic
filtering is the answer to exactly that, and the GPU spends it only where the
squash is. The benchmark is unchanged at every region size.

Both are measured rather than eyeballed, on a receding textured slab: how much
neighbouring pixels disagree must *drop* far away when mipmaps arrive (a third,
here), must *not* drop close up, and must come back up when anisotropy is added
-- the last two quantities being the same measurement, separable only because
the minification filter is a mipmap filter in both frames, so a pixel cannot be
a random texel and what is left is detail.

**A -- an attachment is a child of the avatar, watched rather than assumed
(2026-09-06).** `viewer3d/linkset.py` had said so since the linkset fix and
nothing had ever looked. `ObjectAttach` (Low 112) and `ObjectDetach` (Low 113)
are encoded now, and `tools/verify_attachment_frame.py` rezzes a prim, wears
it, and reads back what the simulator says:

    prim parent_id=176204259 (avatar local=176204259)
    prim position now (0.0, 0.0, 0.0)
    magnitude 0.00 m  ->  PARENT-RELATIVE
    scene draws it at (128.0, 128.0, 25.94)
    the avatar is at (128.0, 128.0, 25.94)

So the composition already in the viewer draws attachments correctly, and the
docstring cites the observation instead of asserting it. The attachment point
is passed through as a number and nothing here claims to know the simulator's
numbering; 0 asks for the object's default.

**A -- a killed linkset used to leave its children behind (2026-09-06).**
Found while making the probes clean up after themselves. Linking two rezzed
prims and taking the root away produced exactly one `KillObject`, naming the
root and *not* the child:

    KillObject events since link:
        local_ids=176204334
    root 176204334: gone
    child 176204335: STILL IN WORLD VIEW

`apply_kill_object` removed only what it was told, so the child stayed in the
world view for the rest of the session -- and a session walking a real region
would keep a phantom prim for every linkset that ever left view, plus a
`local_id_to_full_id` entry pointing a reusable local id at a prim that is
gone. A killed object's descendants now go with it, to any depth. The exception
is an avatar: sitting is parenting, so a seated avatar looks exactly like a
linkset child here, and deleting a chair does not delete whoever was sitting in
it. A prim attached to an avatar that leaves *does* go, so the exception is the
avatar itself, not everything under one.

**A -- the third pass (2026-09-05): the avatar, and the normal transform.**
Avatars were seven merged boxes in one flat yellow. Two separate things made
them look like that, and both were about the space the mesh was authored in.

The mesh is multiplied by the avatar's `ObjectUpdate` scale, roughly
`0.45 x 0.60 x 1.90`, so anything written as a cube in the mesh's own space
comes out four times taller than it is deep -- which is how the placeholder
ended up with a half-metre head. `viewer3d/avatar_mesh.py` writes parts in
**metres** and divides by that nominal scale on the way in, so the numbers in
the table mean what they say.

And a figure drawn as one instance can only carry one tint. Each part now
points its vertices at one texel of a six-entry palette strip -- skin, hair,
shirt, trousers, shoes, eyes -- which costs one small texture and no extra draw
calls. The instance tint is deliberately bypassed for avatars: it is the 2D
map's marker colour and is identical for everyone in the region.

Nineteen parts: an ellipsoid head under a hair cap set back from the face,
eight-sided tapered tubes for trunk and limbs, boxes for hands and shoes. The
nose and eyes exist because a bare ellipsoid head looks the same coming and
going.

*A real shader bug came out of it.* `mat3(in_model) * normal` is exactly right
for a uniform scale and for every axis-aligned box face -- which is most of
what a test reaches for, and why it survived -- and wrong for every other
normal under a non-uniform scale, which SL prims are constantly. It goes
through the inverse transpose now. The test squashes a sphere into a disc and
samples a ring around its face: correct, it shades evenly whatever the sun is
doing; before, the ring ran bright to dark across a spread of 67 out of 255.

The 2D map viewer also got the hand-drawn chat ticker (it repaints the whole
screen every frame, so there was nothing for dirty-tracking to save) and a
`--screenshot` flag of its own.

**A -- the fourth pass (2026-09-05): the walk.**
The figure now has a nine-bone skeleton -- two arms, two forearms, two thighs,
two shins, and a root -- and each bone is one instanced draw of its own vertex
buffer, so the cost is nine draws for the whole region rather than nine per
avatar.

What poses it is not an animation asset. An SL animation is keyframe data
against a skeleton that ships inside viewers, and `AvatarAnimation` names what
is playing only as UUIDs whose meaning comes from a table this project has no
source for. Acting on them would be guessing, and a wrong guess looks exactly
like a bug. So the gait is *derived*: from where each avatar has been, frame
after frame. That works for every avatar in the region, including one running a
custom animation this client could never have decoded, and it is honest about
being this client's own idea of walking.

Two things in `viewer3d/avatar_pose.py` are load-bearing:

- **The stride advances with distance, not with time.** A foot plants at the
  same point in the cycle however irregularly the updates arrive, so a stutter
  in the network is not a stutter in the walk. Live: 9.12 cycles for 13.67 m,
  against 9.12 expected.
- **Speed is measured between *moves*, not between frames.** Positions arrive
  as events at the simulator's rate; the viewer samples once a frame, so most
  frames see the same position twice. Dividing "did not move" by a frame time
  made the reading alternate between zero and several times the truth -- the
  first live run measured a 9.28 m/s peak on a 1.9 m/s walk, which is every
  avatar in the region breaking into a sprint between packets. Measuring across
  the interval since the last actual move puts that at 2.57.

A stop is a gap in the stream long enough to mean stopped (0.35 s) rather than
merely late, which is also what absorbs a correction snap: halting, the sim
moved the avatar 0.46 m in one 50 ms sample, and the stillness that follows
zeroes it before the second is out. `tools/verify_avatar_gait.py` is the live
check -- stand, walk, stop -- and it drives the derivation off the sim's own
position stream at whatever cadence the sim chooses.

Deliberately not attempted: a hip sink measured from the actual sole corners
keeps the feet on the ground through the swing (drift under 2 mm across a
stride) -- the obvious `1 - cos` formula over-corrects by 6.5 cm, because the
shoe reaches forward of the ankle and the loss depends on which way the leg is
swinging.

Still open under A at the time: **prim textures are the only thing
textured** -- terrain, water and sky are all shader-generated. Terrain was
answered in the same pass and the sky and sea in the sixth; what is left is
the cloud, star and water-surface textures the day cycle names.

**A -- and a seated avatar is a child too (2026-09-05).** Sitting reparents the
avatar onto the seat: `tools/verify_seated_avatar.py` rezzes a prim, sits on
it, and the avatar's `ObjectUpdate` starts reporting `parent_id` = the seat's
local id and a position of `(-0.415, 0.0, 0.9)` -- the seat's frame, not the
region's, exactly as for a linkset's child prims.

So before the fix below, **every seated avatar in every region was drawn near
the region corner**, not only every linked prim. The viewer now places the
seated avatar at (129.09, 128.0, 26.12), on its seat.

Sitting also needed two messages this client did not have: `AgentRequestSit`
asks, the simulator answers with `AvatarSitResponse`, and `AgentSit` commits.
Standing back up is the `STAND_UP` control flag, which already existed.

It is posed sitting, too. `parent_id` is the one thing about what an avatar is
*doing* that can be read without decoding an animation asset, so a seated
avatar gets knees forward and shins down instead of standing to attention on
its chair. What that cannot know is *how* they are sitting -- a poseball can
put an avatar in any shape at all, and none of that is readable here.

*Two sign errors in the walk came out of it.* Rendering candidate seated poses
to look at them meant working out which way a bone pitch turns a limb, and the
answer said the gait was wrong: the knee bent on the leg swung **forward**
rather than the trailing one, and it bent the knee **backwards**, so the ankle
finished in front of the knee. Both had shipped, and the test asserted
`shin <= 0` -- which is the bug written down. The two errors also partly hid
each other on screen. The replacement measures the skeleton instead of the
numbers: bending a knee has to move that foot backwards, which is true of every
knee and cannot be satisfied by a wrong sign.

**A -- linksets were drawn at the region corner (2026-09-05).** `ObjectUpdate`
carries a `parent_id`; the 3D scene stored it and never used it, drawing every
prim at the position the update reported. Nothing in this tree recorded what
frame that position is in, and no viewer source may be consulted to find out,
so it was observed instead.

The local test region contains no linksets, so `tools/verify_child_prim_frame.py`
makes one: it rezzes two prims 4 m apart, links them with the new `ObjectLink`,
and reads back what the simulator then says about the child.

    before link:  (134.0, 128.0, 27.121)     -- a region position
    after  link:  (  4.0,   0.0,  0.000)     -- its offset from the root

**A child's position is in its parent's frame.** Every child of every linkset,
and every attachment on every avatar, was being drawn a few metres from the
region corner. It never showed locally because the test region is 30-odd
single prims -- and it would have been the first thing wrong on the main grid,
where almost nothing is a single prim.

`viewer3d/linkset.py` composes each child back through its parent
(`world = parent_position + parent_rotation * child_position`, rotations
multiplied), working outward from the roots so an attachment's own children
resolve too, and leaving out a child whose parent has not arrived yet rather
than drawing it in the wrong place for a frame. Verified live: the linked child
reports (4, 0, 0), its root sits at (130, 128, 27.121), and the scene now
places it at (134, 128, 27.121).

Two things fell out of it. `ObjectAdd` moves from `handled` to `verified` --
it had never been confirmed to rez anything. And the client can now link
objects, which it could not before.

**A -- the framerate, answered (2026-09-05).** The owner reported "we have
around 14fps, that raises to 20fps if I shrink the window. this strongly
suggests that the desktop application uses software rendering as well." It does
not. Measured on the real GPU with the new `--hidden` flag, which opens the
window hidden so a run can be profiled without a window appearing on anyone's
desktop -- Xvfb cannot answer this question, because it has no GPU and falls
back to llvmpipe:

    hidden window on the real display:  NVIDIA GeForce GTX 1660 SUPER
    under Xvfb:                         llvmpipe (Mesa)

Two separate causes, both fixed:

1. **The frame cap was 20.** `--max-fps` defaulted to 20.0, set back in May
   with the first terrain pass and never revisited. The "20 fps" in the report
   was not a measurement of anything -- it was the ceiling. It is 60 now.
2. **The diagnostics panel cost more than the cap's whole budget.** With it
   open the frame was 23.1 ms; closed, 10.8. `UITextBox.set_text` on its
   eighteen lines measured **73 ms**, and its first line is the framerate, so
   it paid that every second: the panel opened to find out why the viewer was
   slow was itself dropping four frames a second. Drawn by hand instead
   (`viewer3d/text_panel.py`, the same move the chat ticker made) the frame is
   **10.1 ms with the panel open** -- level with having it shut.

That also explains the window-size effect, which never fitted the software-
rendering theory: the HUD surface work scales with area, so a smaller window
made the per-frame cost small enough to actually reach the cap.

Sustained over 70 s against local OpenSim: 1320 frames before, 3780 after.

**A -- the earlier pass (2026-09-03).** The viewer crashed on
the first `AvatarAnimation`, which arrives within seconds of any local session,
so in practice it never survived a minute. Fixed in 395a58c. It now runs
indefinitely against local OpenSim and draws terrain, water, 32 prims and the
avatar nametag. The terrain debug wireframe was also on by default and made a
working world look broken; it is off now.

Three separate things were making it slow, and all three are fixed:

1. **The HUD rebuilt its text every frame.** `pygame_gui`'s `UITextBox.set_text`
   costs ~41 ms for eight lines of chat and ~48 ms for eighteen; both viewers
   paid it 60 times a second for text that had not changed. The 2D viewer spent
   48 ms of a 60 ms frame there while the map itself took 5.7 ms -- that was the
   owner's 2 fps. Refreshes are now throttled to 4 Hz and each widget is written
   only when its text actually differs; hidden windows cost nothing at all.
2. **Every inbound message did a durable sqlite commit on the event loop.**
   `UnknownsDatabase` opened a fresh connection and fdatasync'd per packet:
   10.2 ms each, a ceiling of ~98 inbound messages a second for the entire
   client. One shared WAL connection makes it 0.083 ms, a 123x improvement --
   and, incidentally, took the test suite from 460 s to 53 s by removing the
   lock contention it had been causing between tests.
3. **The viewers sent `AgentUpdate` at 1 Hz.** A keypress waited up to a second
   to reach the simulator and its release waited another. Now 10 Hz, plus an
   immediate send whenever the control flags or rotation change.

Measured on the owner's hardware (GTX 1660 SUPER, NVIDIA 580, local OpenSim):
**14 fps before, 40 fps after** -- 71 ms a frame down to 24.8 ms. The GL context
was never the problem; it is hardware NVIDIA GL on all three SDL video drivers.
What made it resolution-dependent, and so look like software rendering, was
per-frame full-screen CPU work:

4. **Two full-screen surfaces were converted and uploaded every frame.**
   `pygame.image.tobytes(surface, "RGBA")` copies and converts the whole
   surface on the CPU -- 11.2 ms at 1920x1080, and it ran twice. The pixels are
   already in a layout GL can read, so the compositor now hands them over
   directly and swizzles in the shader. 4.5x on the compositing path at every
   resolution.
5. **The world surface was pure waste in 3D mode.** `PerspectiveRenderer.render`
   fills it with one flat sky colour; that was then converted and uploaded as a
   full-screen texture every frame to say what a GL clear says for free. A
   renderer can now return `world_background()` and the frame loop skips the
   surface entirely. `composite_world` went from ~12 ms to 0.1 ms.
6. **The 3D HUD's chat ticker was never throttled** the way the 2D one was --
   6.1 ms of a 30.7 ms frame, every frame. Same treatment: 4 Hz, change guards,
   and a skip when the chat window is closed.

The remaining 24.8 ms is `hud_update` 7.8, `hud_draw` 6.0,
`composite_hud_flip` 5.2, `render_gl` 5.0. **The 3D pass itself is now the
smallest item.** The next real win is dirty-tracking the HUD: `hud_draw` plus
the HUD upload is 11 ms a frame spent redrawing a surface that changes a few
times a second. It was not attempted here because a stale HUD is a worse bug
than a slow one, and detecting "nothing changed" through pygame_gui (hover,
focus, the text cursor blink) needs care.

Worth knowing: the diagnostics panel is open by default in 3D mode and costs
~3 ms a frame amortised, refreshing once a second. Closing it buys that back.

Also fixed under A: the viewer started in the `sim` camera preset, a 190 m
region overview that does not follow the avatar, so neither walking nor turning
was visible at all. It now starts behind the avatar; `--camera sim` and F1 still
give the overview.

**Turning was broken outright, and is fixed.** The cursor keys were mapped to
`AGENT_CONTROL_TURN_LEFT`/`TURN_RIGHT` and those bits reached the wire, but the
simulator does not turn the avatar in response to them -- holding TURN_LEFT for
eight seconds left the reported yaw at exactly 0.00. The client owns its own
rotation. `LiveCircuitSession` now integrates the turn bits into the
`BodyRotation` it sends, at `turn_rate_degrees_per_second` (default 90). Walking
follows the new facing: ~12 m per six-second leg on all four compass headings.
`tools/verify_avatar_turn.py` is the live check.

**B -- launcher done, unverified.** `run.sh` now defaults SL sessions to
`home` rather than `last`. Nothing about this has been exercised against the
live grid, because that needs the owner's SL credentials. Treat as untested.

**C -- done for the CLI, live-verified 2026-09-05.** Object task inventory
downloads to `local/asset-downloads/<task-id>/`, and

    ./run.sh tester sync-object --object <uuid> --folder ./work --pull --all-assets

now brings out the types sync cannot author as well -- textures, wearables,
animations, sounds. They come out **read-only**: push refuses to send one back,
because nothing here knows how to build one and a round trip that silently
truncates an asset is worse than no round trip. Each type's suffix is backed by
bytes OpenSim actually wrote rather than by what the extension "ought" to be;
an unverified type gets its own *name* (`.sound`, `.object`) instead of a
guess, because a wrong `.ogg` invites a tool to fail confusingly later.

`tools/verify_binary_export.py` is the check: it drops a body part and a
texture into the test prim, pulls with and without the flag, compares the files
byte for byte against what `fetch_task_asset` serves, and asserts the push
refuses both. Off by default, so a folder someone is watching does not fill up
with binaries on the next pull.

Still text-only: the *viewer's* "save all text assets" button. The CLI is the
complete path.

**D -- done, live-verified 2026-09-05.** Both asset kinds now go in.

*Scripts* were closed on 2026-09-02: unmatched `.lsl` files go through
`RezScript` to make the row, then the contents upload onto it.
`tools/verify_folder_sync.py` confirms row created, `compiled=True`, and the
row's asset id moved off `Constants.DefaultScriptID` -- which is what separates
"the upload landed" from "a row exists".

*Notecards* were closed on 2026-09-05. There is no create-from-nothing message
for a notecard inside a prim: `Scene.UpdateTaskInventory` rejects a zero item
id, and an unknown one it looks up in *agent* inventory and copies in. So the
route is two hops, and `sync/notecards.py` now does both:

1. `CreateInventoryItem`, then `UpdateNotecardAgentInventory` to give it text.
2. `UpdateTaskInventory` to copy that item into the prim.

Two traps on hop 2, both found live and neither visible in the message
template:

- **The copy may be renamed.** If the prim already holds an item by that name,
  the copy arrives as `<name> 1`. Nothing in the reply says so.
  `copy_item_into_object` diffs the inventory item ids across the copy and
  returns the name the sim actually assigned. Trusting the requested name meant
  the next push found no matching row and made a *second* notecard.
- **A no-copy item is moved, not copied.** The sim removes it from agent
  inventory. Items we create full-perm are unaffected, but a sync that drops
  user-supplied items must not assume otherwise.

That rename is why names are no longer the primary key. Both planners consult
the recorded bindings in `.vibestorm-sync.json` first and fall back to names
only for rows they have never seen, so a rename in world -- by us or by anyone
-- moves the binding instead of spawning a duplicate.

A framing trap worth recording, because it cost a debugging round: a message
encoder returns a bare body, and `WorldClient.queue_outbound_packet` expects a
**framed** packet -- header, sequence, flags. Passing the encoder's output
straight in produces no error anywhere; the sim silently drops it and logs
nothing, which reads exactly like a permissions denial. Always go through the
session's `build_*_packet`.

**The viewer's Upload button now goes through that engine (2026-09-05).** It
used to be ~180 lines of its own -- its own name matching, its own row
creation, its own upload loop, its own copy of the script capability names --
which meant a bug fixed in the engine stayed broken in the button and a green
suite said nothing about it. It resolves caps and calls `push_folder_to_object`
now, reporting progress as chat alerts, and inherits everything the engine was
verified on.

Moving the tests to where the code went turned up three bugs, all of the quiet
kind:

- `match_files_to_rows` could hand one inventory row to **two** files. The
  lookups are case-insensitive and sanitising collapses distinct names, so
  `Greeter.lsl` and `greeter.lsl` both claimed one row: each uploaded over the
  other and both were reported as successes.
- A sync folder that did not exist resolved to its **parent**, so one typo in a
  folder name pushed whatever the folder above happened to hold into the
  object.
- `first_resolved` -- which picks `UpdateScriptTask` over the legacy
  `UpdateScriptTaskInventory` -- had four tests and, once the viewer's copy
  went, no reachable caller. It is a real function in the engine again.

What is still not exercised is the *button press itself*: reaching it needs a
window and a live simulator together. Everything behind it is verified
headlessly, and the one piece of judgement left in the closure -- deciding
which folder a chosen path means -- is a module-level `sync_folder_for_task`
with its own tests.

**E -- delivered, live-verified 2026-09-05.** `vibestorm.sync` is a package
now, and `sync-object` on the CLI drives it:

    ./run.sh tester sync-object --object <uuid> --folder ./work --pull
    ./run.sh tester sync-object --object <uuid> --folder ./work --push
    ./run.sh tester sync-object --object <uuid> --folder ./work --watch

- **Pull** writes the object's contents to disk and records what it wrote.
- **Push** uploads what changed, creates rows for files that have none, and
  reports a *conflict* rather than guessing when two items want the same file
  name (`notes` and `notes.lsl` both want `notes.lsl`) or when an untracked
  file would be overwritten.
- **Watch** polls at 2 s, and requires a file to hold the same digest for two
  ticks before uploading it, so a half-written save never reaches the sim.

`.vibestorm-sync.json` in the folder holds the bindings and the last-synced
digest per item; a wrong `task_id` or a version bump discards it rather than
acting on stale state. `tools/verify_object_folder_sync.py` proves the five
properties that matter against the local sim: pull writes real bytes, pull
then push is a no-op, an edit reaches the object and reads back *through the
sim*, a notecard can be created from nothing, and pushing twice uploads once.

Scope limits still stand: no deletes, no recursive folders, no automatic
conflict resolution -- a conflict is reported and skipped.

### Concrete next step

C, D and E are closed for text assets. What is left, in the owner's own order:

1. **B is still untested.** The launcher defaults SL to `home`, but nothing has
   touched the live grid -- that needs the owner's credentials. Everything else
   is guesswork until someone logs in.
2. ~~**C's last gap is the GUI, not the protocol.**~~ Closed on 2026-09-05 in
   86fc0ce: the inspector's Save button goes through `pull_object_to_folder`
   with `include_binary=True`, so it writes textures, sounds, animations and
   body parts as well as text, records the bindings a later push needs, and
   names files by the same rule the CLI does. This entry was stale for a day;
   it is left here struck through rather than deleted because a next-step list
   that quietly loses entries is one nobody trusts.
3. **A's remaining visual gaps are smaller than the last one was.** Water and
   sky now come from the region's own day cycle (sixth pass). What is still
   this client's own idea rather than the region's:
   - ~~**Clouds.**~~ Done: the region's own `cloud_id` texture, at the offsets
     its two `cloud_pos_density` pairs give, with `cloud_shadow` on the
     ground. What is left is `cloud_variance`, which is 0 in every keyframe of
     the live cycle and is drawn as a third field at a scale this viewer
     invented -- so it is spent, but on no evidence at all.
   - ~~**Stars and the moon.**~~ Done in the seventh pass; both are drawn at
     `moon_scale` and `sun_scale` since, and the moon wears its own `moon_id`
     texture. What is left of it is the **stars**, which are a hash rather
     than a catalogue -- and note that the live document names no `star_id` at
     all, so unlike the moon there is nothing to fetch. They do turn with the
     night since the tenth pass: the field is hashed on the celestial
     sphere's own frame, which `sun_rotation` gives, so the sky carries the
     stars round with the sun and the moon rather than nailing them to the
     region's axes.
   - ~~**The water surface itself.**~~ Done in the seventh pass: the two wave
     directions and both Fresnel fields are read and drawn, and the fixed
     sky-reflection mixture in `water_tint` is gone from the 3D path (it stays
     for the 2D one, which has no angle to measure). `normal_map` followed in
     the eighth: the surface is the region's own sheet, laid in each wave's
     frame, with the sines kept as the fallback until it arrives. The
     ~~wavelength and the steepness are still this viewer's constants rather
     than the region's.~~ Stale, and corrected on 2026-09-07 by reading the
     code rather than by changing it: both come off the wire already.
     `normal_scale` sets the wavelength (`water_wave_number`) and
     `scale_above`/`scale_below` set the steepness. What stays constant is
     only the *unit* each is measured in -- how many metres one repeat of a
     normal map is, and what a distortion strength is worth as an angle --
     and those have to be, because the document gives dimensionless factors
     and no lengths at all. Sun glitter followed in the same pass, and is not a
     specular model: it is the sun the sky pass draws, off one shared GLSL
     string, seen in a mirror. `transparent_texture` is closed in the tenth
     pass and closed as a *decision*: it is read, and deliberately neither
     drawn nor fetched -- see below. The sea shows
     back the region's cloud layer as well as its sun since the ninth pass
     and the moon since the tenth, all three off the same one shared GLSL
     string each, and there is a test that compares the two passes against
     each other rather than against a prediction. The only thing in the sky
     the sea does not show back now is the **stars**, and that is deliberate
     -- see below. Screenshot
     the sea before believing any change to it -- the GL tests read single
     pixels, and an interference pattern is the one defect a single pixel
     cannot see. That is how a lattice in it went unnoticed for a whole pass.
   - ~~**Under the water.**~~ Done in the seventh pass: every pass that draws
     the world fogs, the sky comes through the surface only where the water is
     thin enough, and the surface from below is a ceiling. `blur_multiplier`
     is what is left of it -- there is no blur pass to give it to, and the
     sky's `bloom_id` is the sprite that pass would use.
   - ~~**`cloud_shadow`.**~~ Spent: it multiplies the diffuse light and
     leaves the ambient alone, so cloud dims the sun over the ground without
     turning a cloudy noon into dusk.
   - ~~**A neighbour holds nothing but ground.**~~ Spent. Its prims and its
     avatars arrive on the child circuit, go into a `WorldView` of that
     region's own, and are drawn at its offset; its textures and mesh assets
     go through this region's capabilities behind our own prims; and its own
     sea level, off its own handshake, is the level its sea is drawn at.
   - ~~**The neighbour resends its handshake a few times an hour.**~~ Spent,
     and it was never the handshake. Four `RegionHandshake` packets in ninety
     seconds were retransmits of *one*, because this client was too slow to
     ack it: `_pump_neighbours` ran only when the socket went quiet, and on a
     local sim OpenSim's RTO clamps to `m_minRTO`, 250 ms -- the same as the
     receive timeout it was racing. Flushing every pass took the region next
     door from 22 RESENT packets in 53 to none, and the handshake to one
     copy. The guess recorded here for weeks, that OpenSim resends on
     region-info changes, is not in the source at all; the three call sites
     that do exist are pinned in `test/test_opensim_source_pins.py`.
   - ~~**The camera does not see round anything.**~~ Spent. A ridge between
     the camera and the avatar pulls it in, and so does a prim: the ground is
     marched and the prims are cast against, in that order, and the nearer
     wins. What is left of it is avatars, which are deliberately not
     consulted -- an avatar is the thing being looked at as often as it is
     the thing in the way, and a camera that jumped in every time someone
     walked past would be worse than one that did not.
   - **The sky's own unread fields.** `gamma`, `max_y`, `glow`,
     `density_multiplier`, `distance_multiplier` and `haze_density` are all
     parsed and none is drawn. Most belong to the Windlight atmospheric
     integral this viewer deliberately does not attempt (see the module
     docstring in `atmosphere.py`). `glow` is the nearest to reachable -- it is
     the sun's size and focus, which `sun_scale` now half covers -- but it is
     identical in all eight keyframes of the default cycle, so there is no
     evidence for which of its three components means what, and guessing is
     how a viewer ends up with a sky nobody can correct.
   (The gait, sitting, attachments, linkset placement, the frame cost at
   region scale, texture filtering, and the sky and sea themselves, which used
   to be this entry in various forms, are done -- see the fourth, fifth and
   sixth passes.)
4. **A's memory is bounded now, and the bound has never been tested against a
   real grid.** This entry used to say there was no budget, no eviction and no
   resolution cap. There are all three -- `OBJECT_TEXTURE_BUDGET_BYTES` at
   384 MB, `MAX_OBJECT_TEXTURE_EDGE` at 512, and an eviction that never touches
   the previous frame's set -- and the numbers in them are guesses. The local
   region holds a handful of textures, so nothing here has ever been near the
   ceiling; what would say whether 384 MB and a 512 edge are right is a mainland
   region, which needs B. Until then the honest position is that the failure is
   *visible* rather than *solved*: the diagnostics panel says `OVER BUDGET` when
   the visible set alone will not fit, which is the condition that would
   otherwise show up only as a viewer that got slow and stayed slow.

**The local test prim `d7f47f7e-4328-4d17-a665-19feaec7b1e9` now carries
several `vibestorm-sync-*`, `e2e-sync-*` and `verify-note-*` items** left by
probes, including a genuine name collision (`vibestorm-sync-88338` and
`vibestorm-sync-88338.lsl`) that the verify tool reports as a conflict every
run. `tools/clean_test_prim.py` removes them.

**The test region is clean again, and the probes now clear up after
themselves.** `ObjectDelete` is still unhandled by this OpenSim build -- its
own log answers every attempt with

    WARN [CLIENT]: ignoring unhandled packet ObjectDelete

-- but there is another way, found while checking what an attachment is:
**wear the prim, then take it off.** `ObjectDetach` takes an attachment into
*inventory*, so the prim leaves the region. It is not the same as deleting, and
the simulator permission-checks the wearing, so it can only ever reach prims
the agent owns -- both of which are exactly right for a probe tidying up after
itself. `tools/probe_support.py` does it, `verify_child_prim_frame.py` and
`verify_seated_avatar.py` call it, and `tools/delete_prims.py --via-attachment`
is the same route by hand. The four cubes those probes had left behind, and the
linkset made to observe the child frame, are gone.

That module also holds `wait_until_quiet`, because the other half of the mess
was a probe calling late arrivals its own: objects stream in for tens of
seconds after the agent lands, and a snapshot taken on arrival is not a
snapshot of the region. One run of `verify_child_prim_frame.py` linked two
prims from an *earlier* run that way and reported on those.

It is still worth knowing how the `ObjectDelete` finding was nearly missed.
Three prims *did* disappear around the first runs of the tool, which read as
three successful deletes and one stubborn refusal, and that is what was
committed. They had vanished on their own. The simulator's log is what said
otherwise, and the lesson is the familiar one in a new costume: a result that
agrees with what you just built is the one to check hardest.

The same log turned up a real bug here. Passing a duplicate local id to
`ObjectDelink` made OpenSim throw a `NullReferenceException` inside
`SceneGraph.DelinkObjects` -- a crash in the simulator, caused by this client.
The tool deduplicates now.

## Environment Note (2026-09-02)

`uv` was missing and `.venv/` did not exist, so nothing Python-side ran. Both
restored; everything resolves on the system Python 3.14.4 with no pin needed.

`./run.sh test` was using the base dependency set, so on a cold machine the
suite skipped 137 viewer/GL tests and errored on two Pillow ones -- a red
suite, from the command that is the push gate. It now uses the `dev` extra
(50e8019). `README.md` recommended a bare `uv sync` for the same reason.

If the sim is dead rather than the Python side, `docs/runtime-platform-risk.md`
and `docs/local-opensim.md` have the .NET 8 story; do not re-derive it.

**`Vibestorm North` is deliberately set to a water height of 12 m** (2026-09-06),
against `Vibestorm Test`'s 20 m, so that a border between two regions that
disagree about their sea level is a thing this grid actually has. It lives in
`regionsettings.water_height` in `local/opensim/runtime/bin/OpenSim.db` and is
only read when the region starts, so changing it means stopping the simulator
first -- edit the row while it is running and the shutdown writes it back.
`-console=rest` is what it is started with; started without a console argument
it spins on `Console.KeyAvailable` and writes a gigabyte of the same error a
minute.

## Two Ways A Test Can Agree With A Bug

Both of these happened on 2026-09-02, hours apart, and they are the same
mistake wearing different clothes:

- `parse_object_extra_params` skipped the u8 count that a `Variable` block
  carries. Three tests built synthetic packets **without** the count, so they
  agreed with the parser. Nothing contradicted it because OpenSim never sends
  that message at all.
- `Scene.apply_avatar_animation` read `entry.animation_id`; the wire type's
  field is `anim_id`. The test file hand-rolled an `_AnimEntry` declaring
  `animation_id`, so seventeen tests passed against a viewer that died on the
  first live animation.

**Build test data from the real type or the real template, not from a reading
of the code under test.** Where a hand-rolled stand-in is unavoidable, say in
the test why it is faithful.

A corollary worth keeping: `BusDeliveryError` used to report only *how many*
subscribers failed. Since the traceback stops at `publish()`, the cause was
unrecoverable from a crash log -- the first viewer crash log named nothing at
all. It now names and chains the failures, which is what located the bug.

## ExtraParams: Write A Feature To Observe It (2026-09-02)

Five `ExtraParams` decoders -- light, projector, reflection probe, render
materials, mesh flags -- were filed "blocked on region content". They were not.
The sim sends them whenever a prim has the feature; nothing in the region had
one turned on. `session-run --probe-extra-params` sets all five on a prim we
own, reads the echo, and clears them again. Every value came back byte-exact.

This is the third time the blocker turned out to be a missing *outbound*
message rather than missing content, after chat and IM. **Before filing
anything as blocked on world content, ask whether the client can produce the
traffic itself.**

The probe clears what it set. A following `./run.sh census` reporting the five
back under `absent=` is the restore check; leaving them set would make every
later census read them as real region content.

## Object Sync Track

The next coherent file feature track is object-local script/notecard sync, not
more generic user-inventory upload.

Implement it in this order:

1. Add a task-inventory asset update CAP client beside
   `src/vibestorm/caps/asset_upload_client.py`.
   - Resolve `UpdateScriptTask` first, then fall back to
     `UpdateScriptTaskInventory` for script rows.
   - Resolve `UpdateNotecardTaskInventory` for notecard rows.
   - Match OpenSim's two-step shape: POST LLSD metadata to the CAP, receive
     `state=upload` plus `uploader`, then POST raw file bytes to the uploader.
2. Start with updating existing object inventory items only.
   - Script metadata is `item_id`, `task_id`, and `is_script_running`.
   - Notecard task updates appear to share the broader item-asset update path;
     verify the exact request keys against `referencedocs/Caps/BunchOfCaps/UpdateItemAsset.cs`
     before coding.
   - Do not create new object inventory rows yet; that can follow after update
     is proven live.
3. Add a narrow sync planner in `viewer3d`.
   - Use the selected object's `task_id` as the local folder key:
     `local/asset-downloads/<task-id>/`.
   - Match `.lsl` files to visible script inventory rows and `.txt` / `.nc`
     files to visible notecard rows by sanitized item name.
   - For the first pass, upload only exact name matches and report skipped
     files in chat/status.
4. Wire Object Inspector `Upload` to selected-object sync when an object
   inventory row set is loaded; keep the current user-inventory upload as the
   fallback when no selected object context exists.
5. Live verify on local OpenSim with `./run.sh tester viewer3d`:
   download an object's scripts/notecards, edit one local file, upload/sync,
   reload task inventory, and view the item again.

Keep these scope limits for the first pass:

- no bidirectional conflict resolution
- no deletes
- no creating missing object inventory items
- no recursive folder sync
- no automatic upload on every file change

## Update 2026-08-14: Self-Checking Ledgers, IM, Teleport

### The recurring problem, closed

`spec/message-coverage.md` and `spec/capability-coverage.md` have each drifted
twice, in both directions, and every drift was found by hand months later. Both
now re-derive themselves from the code:

- `test/test_message_coverage_ledger.py` — a row claiming support must name
  something the source mentions; every message with a parser must have a row;
  every message the client can *send* must have a row.
- `test/test_capability_coverage_ledger.py` — the same two, plus a scale check,
  because this ledger writes its status scale out in the document and a row can
  contradict it. It immediately caught a row reading `handled`, a status
  borrowed from the other ledger's scale.

Wire names are read from what the code actually does, never from a naming
convention: parsers from their `summary.name != "X"` guard, encoders from the
message-number prefix they write, resolved through the same template the client
dispatches with. The guess would be wrong — `parse_simulator_viewer_time`
decodes `SimulatorViewerTimeMessage`.

Between them these found **22 messages** with working code and no ledger row:
the appearance handshake, the xfer and transfer requests, map blocks, parcel
properties, task inventory, and the teleport request.

Neither checks `tested` versus `verified`. That is a claim about live evidence,
and asserting it offline would be the overclaim the distinction exists to
prevent. Nor can anything catch a message this client neither sends nor
parses — indistinguishable from a message that does not concern us.

### "Blocked on region content" was wrong twice

`ImprovedInstantMessage` was filed as needing a second avatar. It does not:
OpenSim's `InstantMessageModule.OnInstantMessage` routes on `ToAgentID` with no
self-check, so an IM addressed to our own agent id comes back through exactly
the inbound path a second avatar's would take. Sent live, delivered 5.5 s later.
Chat had left the same list a week earlier for the same reason.

**Before filing something as blocked on world content, check whether the client
can produce the traffic itself.** Twice the blocker was a missing outbound
message, not a missing object.

The IM encoder writes the trailing `EstateBlock` and `MetaData` blocks that the
template defines and OpenSim's handler ignores. The packet is deserialised in
full before the handler runs, so a block the deserialiser expects and does not
find is malformed, while trailing bytes it does not expect are not. libomv is
DLL-only in `opensim-source/`, so this was settled by the live round trip
rather than by reading its packet class.

### Teleport

The client could send `TeleportLocationRequest` and understood none of the
replies. Now decodes `TeleportStart`, `TeleportProgress`, `TeleportFailed` and
`TeleportLocal`, with `world/teleport_flags.py` naming the flag word — fully
sourced from `Constants.cs`, so unlike the parcel and region tables this one
has no unnamed bits and the pin test demands every flag be named.

Verified live both ways: a hop inside the region, and a teleport to a region
handle no region occupies (`'The region you tried to teleport to was not
found'`, zero `AlertInfo` blocks — OpenSim never populates that block).

Two corrections the live run forced:

- `TeleportLocal` is the **entire** response to a same-region hop. No
  `TeleportFinish`, no new circuit, no seed capability. The session must take
  its position from it or keep sending `AgentUpdate` from where the avatar used
  to be.
- This module first shipped `is_same_region_teleport` /
  `is_region_crossing_teleport` reading the `FinishedVia*` bits. They looked
  right and were dead code: **OpenSim sets no `FinishedVia*` bit anywhere**,
  it forwards the request's own flag word unchanged. The flags stay named for
  other grids; the predicates are gone, and a test pins the absence to the
  source tree rather than to one reading of it.

### Also

`UpdateScriptTaskInventory` is the name OpenSim marks `//legacy`; the current
name is `UpdateScriptTask`. The object-sync path asked only for the legacy
alias, which works today and would fail as "no task inventory caps available"
the day it is dropped. It now asks for both, current first.

### ViewerAsset

`ViewerAsset` had been resolved every session since the seed-cap list was
written and had no client, so notecards, scripts, animations and sounds could
only come down the UDP `TransferRequest` channel. `caps/viewer_asset_client.py`
now implements it, and `RequestAssetData` prefers it — the session loop sends a
`TransferRequest` only if the HTTP fetch fails, so this adds a path without
removing one. Bytes land in `session.fetched_assets` either way.

Live-verified twice: standalone against the region map texture (4376 bytes,
`image/x-j2c`, byte-identical to `GetTexture`), and end-to-end through the real
`_handle_request_asset_data` with no UDP transfer sent.

Two properties of the capability that are easy to get wrong:

- **The query key selects the asset type.** There is no generic `asset_id`, and
  an unrecognised key is answered 404 *before* the asset service is consulted.
  So `asset_type_query_key` raises rather than falling back to a plausible key,
  which would report "the sim does not have it" when the truth is "this client
  does not know that type".
- **The type check is not enforced.** `GetAssetsHandler` compares `asset.Type`
  against the key's implied type, logs `asset with wrong type`, and serves the
  bytes anyway — the `return` beneath the warning is commented out. Asking for
  the map texture as `notecard_id` returned the same 4376 bytes. A 200 is
  therefore no evidence about an asset's type.

The key table covers only the type numbers LSL pins, because `AssetType` is
libomv's enum and only the DLL ships in `opensim-source/`. `mesh_id` and the
five TGA/WAV/JPEG keys are absent for that reason alone — an unmappable type
stays on UDP rather than being routed to a guaranteed 404.

### The grid library, and what "planned" was hiding

Every capability this document called `planned` was asked for once against the
test sim, to separate "the sim cannot" from "we never asked". **Thirteen of
fifteen resolved.** Only `RegionObjects` and `AgentState` did not. `planned`
had mostly meant the second thing, and the two need completely different work.

The useful one is `FetchLibDescendents2`. OpenSim ships a read-only library
that a stock install populates, and `./run.sh inventory-walk --library` now
reads it: 19 folders, 123 items — 64 textures, 17 scripts, 16 gestures, 12
animations, 7 settings, 4 body parts, 2 clothing, 1 notecard.

That is a second content source for a client whose gap list is mostly "the
region has none of these". One asset of each of the eight types was fetched
through `ViewerAsset` and kept in `test/fixtures/library/`, every one returning the matching
`application/vnd.ll.*` content type — so eight of the twelve query keys are
live-verified rather than merely sourced. The remaining four (`sound_id`,
`landmark_id`, `object_id`, `material_id`) are untried, not suspect: the
library has none. `test_viewer_asset_client.LiveCoverageTests` records which
is which so "supports twelve types" never stands unqualified.

One trap: the owner id must be the **library** owner
(`11111111-1111-0000-0000-000100bba000`), not our agent id.
`FetchLibDescHandler` compares it and answers a mismatch with an empty tree
rather than an error, so the obvious guess looks like an empty library.

### Reading prim physics without touching the region

`ObjectPhysicsProperties` was on the "blocked on consent" list because OpenSim
sends that message only as an echo of an edit the viewer itself made — so
seeing physics data meant editing someone's region first.

It does not. `GetObjectPhysicsData` returns the same five values for any prim,
changes nothing, and needs no permission. `caps/object_physics_client.py`
implements it and `./run.sh census --physics` uses it: 32 of 33 objects
answered, all shape `prim` at OpenSim defaults. The one that did not is our own
avatar, which sits in the same object collection but is not a
`SceneObjectPart`; the sim omits it silently rather than erroring.

**One id per request, and this is not negotiable.** OpenSim's handler closes
the outer LLSD map *inside* its loop:

    for (int i = 0 ; i < object_ids.Count ; i++)
    {
        if (obj != null) { AddMap(uuid); ...; AddEndMap(); }
    AddEndMap(lsl);            // <-- inside the for, once per id
    }

so N ids emit N closing tags for a map opened once. Confirmed live: one id
parses, two ids fail with `mismatched tag`. `fetch_many` loops rather than
batching, and a test pins the asymmetry (one `AddMap`, two `AddEndMap` in the
loop body) so that if OpenSim ever fixes it, we find out.

What remains unverified is the UDP *message*, not the physics. That
distinction is now in `projectstate.md` in place of the old blocker.

### Land impact, and why one cap batches and its neighbour cannot

`GetObjectCost` sits beside the physics fetch under `./run.sh census
--physics`: 32 prims live, every one costing 1, `resource_limiting_type=legacy`.

**Batching works here and not next door.** Same source file, same request
shape, opposite answer — `GetObjectPhysicsData` closes its outer LLSD map
inside the per-id loop, `GetObjectCost` closes it after. So the one-id limit
belongs to that handler, not to the family, and each test pins its own
handler's loop-body shape so a change either way is noticed.

Two traps in the cost response, both confirmed live:

- **A request matching nothing is not an empty map.** OpenSim writes a filler
  entry keyed by the *zero UUID*, all costs 0, `resource_limiting_type` still
  `"legacy"` — shaped exactly like a real prim that costs nothing. The parser
  drops it; keeping it would report a cost for a prim that does not exist.
- Equal prim and linkset costs mean this prim's cost accounts for its linkset,
  not that the linkset has one prim.

### These diagnostics run inside the receive loop

The physics and cost fetches are awaited in the same loop that reads UDP, the
way the texture and mesh fetches already were. That means an awaited fetch is
time the session spends not reading packets and not sending `AgentUpdate`.

For an asset the client actually needs, waiting is right. For a diagnostic
nobody asked for beyond a report at the end, a hung capability must not stall
the circuit for ten seconds per prim — so both use
`DIAGNOSTIC_CAP_TIMEOUT_SECONDS` (3 s) rather than the 10 s the asset fetches
take. Against a local sim they answer in milliseconds, so the bound is far
outside the observed range; it exists for the failure case, not the normal one.

If more per-tick fetches get added, this is the thing to revisit — they are
sequential, so the worst-case stall is their timeouts summed.

### A testing note worth keeping

Mutation-checking with `cp` to restore a file can lie. A restored file the same
size as the mutated one, written in the same second, leaves Python's `.pyc`
stale — so a mutation reads as caught when it is not, or a restore reads as
broken when it is not. Run mutation checks with `PYTHONDONTWRITEBYTECODE=1`.

The IM encoder's first mutation pass also missed a swapped `Offline`/`Dialog`
pair: two adjacent U8s, both 0 in every test, so the swap round-tripped
perfectly. There is now a test that gives them different values.

## Update 2026-05-25: Object Task Inventory Sync (Steps 2–4)

### What Changed

- **`src/vibestorm/caps/task_inventory_upload_client.py`** (new): two-step
  `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` CAP client.
  `upload_task_script()` sends `{item_id, task_id, is_script_running}`, gets an
  uploader URL, then POSTs raw LSL bytes. `upload_task_notecard()` is identical
  except without `is_script_running`.

- **`src/vibestorm/viewer3d/hud.py`**:
  - New `on_upload_object_files` callback on `HUD.__init__`.
  - New `_selected_object_task_context()` method — returns `(task_id, rows)` for
    the currently selected object if its task inventory is loaded, `None` otherwise.
  - "Upload File" and "Upload Dir" buttons now detect object context: when a task
    context is present they open a sync dialog seeded at
    `local/asset-downloads/<task-id>/` and fire `on_upload_object_files`; when no
    object context they fall back to the existing user-inventory upload path.

- **`src/vibestorm/viewer3d/app.py`**:
  - New `_match_files_to_task_selections(upload_dir, asset_rows)` pure helper —
    matches `.lsl`/`.txt`/`.nc` files by safe-filename stem to loaded inventory
    rows (scripts asset_type=10, notecards asset_type=7); returns
    `(matched, unmatched)`.
  - New `sync_files_to_object_task_inventory` coroutine — resolves
    `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` caps, runs the
    match planner, uploads matched files, reports each result and a summary in chat.
  - `on_upload_object_files` wired into the HUD constructor.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/app.py src/vibestorm/caps/task_inventory_upload_client.py`
- `uv run --extra viewer pytest test/test_task_inventory_upload_client.py test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py -q` — 29 passed
- `./run.sh test` — 536 passed, 0 failed

### Concrete Next Step

Live-verify on local OpenSim with `./run.sh tester viewer3d`:
1. Select a scripted object, open Object Inspector, Load Inventory.
2. Save Text to `local/asset-downloads/<task-id>/`.
3. Edit the `.lsl` file locally.
4. Click "Upload File" — confirm dialog seeds `local/asset-downloads/<task-id>/`.
5. Select the edited file; watch chat for `Sync: … compiled OK` or compile errors.
6. Reload task inventory in viewer; confirm the script version changed.

If caps are missing (`Sync: no task inventory caps available`), check that OpenSim
has `UpdateScriptTaskInventory` and `UpdateNotecardTaskInventory` wired in BunchOfCaps.

## Update 2026-05-22: Pygame In-Game Login & Credential Saving

### What Changed

- **Launcher Integration (`run.sh`)**: Bypassed terminal interactive prompting (`prompt_login`) and re-entry/retry prompts for the `viewer` and `viewer3d` commands. Exported active profile paths via environment variables `VIBESTORM_LOGIN_PROFILE` and `VIBESTORM_LOGIN_PROFILE_NAME`.
- **Credentials Utility (`src/vibestorm/util/credentials.py`)**: Implemented a secure shell-compatible parser and writer for `.env` login profiles using `shlex` shell-safe quoting and strict file permissions (`mode 600`). It has robust fallback default credential resolution for the `tester` profile.
- **Login Screen UI (`src/vibestorm/viewer/login_screen.py`)**: Built a highly aesthetic in-game Pygame login screen containing:
  - Translucent glassmorphic center container with glowing highlights.
  - Linear vertical gradient background with custom-rendered, elegantly drifting/glowing background micro-particles.
  - Complete form inputs for Grid Preset Selection, Custom Grid URI, Avatar First/Last Name, Password (masked text entry), Start Location, and Remember Credentials.
  - Grid preset prefilling logic that automatically populates standard grids (Local OpenSim, OSgrid, Second Life) on selector change.
  - Asynchronous login via `LoginClient().login(...)` in the running event loop with a smooth "Connecting..." indicator, "Cancel" button, and descriptive inline error reporting.
  - Protected `asyncio.get_running_loop()` check to support headless synchronous unit testing without event loop crashes.
- **2D App Integration (`src/vibestorm/viewer/app.py`)**: Removed strict command-line argument requirements for credentials and wired the new `LoginScreen` loop to execute first if complete credentials are not supplied via CLI arguments.
- **3D App Integration (`src/vibestorm/viewer3d/app.py`)**: Removed strict command-line argument requirements and updated 3D viewer bootstrap to open a Pygame/ModernGL screen first, drawing the software `LoginScreen` UI onto the composited `world_surface` background quad before transition.

### What Was Verified

- **Unit Tests**:
  - `test/test_credentials.py` verified profile loading/saving, tester fallback defaults, and shlex unquoting/escaping.
  - `test/test_login_screen.py` verified UI widget construction, preset dropdown selection/prefilling, quit request action, and event handler consumption.
  - Full project pytest suite (525 tests) runs and successfully passes.
- **Headless Pygame Execution**: Verified standard startup workflows run flawlessly under the `dummy` SDL video driver.

### Concrete Next Step

- Manual verification: Run `./run.sh viewer` or `./run.sh viewer3d` on a live display. Fill in or load credentials, toggle "Remember Credentials", verify the glassmorphic animations, and successfully connect.

## Update 2026-05-17: Sculpt/Mesh Render Placeholders

### What Changed

- `viewer3d` now decodes the sculpt `ExtraParams` block
  (`ParamType=0x30`, `UUID + sculpt_type`) into renderer-facing scene
  metadata.
- Sculpt placeholders now choose an approximate existing primitive mesh:
  sphere, torus, cylinder, or flat cube/plane.
- SL mesh objects (`sculpt_type=5`) are tagged as `mesh` and keep their asset
  UUID on `SceneEntity.mesh_asset_id`; a follow-up update below adds the first
  actual `GetMesh` fetch/decode path.

### Current Boundary

- This first classification step was visual-only; see the follow-up mesh
  update below for the first real mesh asset path.
- Vibestorm still does not fetch or decode sculpt-map textures.
- Per-face mapping for non-cube primitives is still coarse; cube face-specific
  texture overrides remain the only detailed face mapping.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/scene.py src/vibestorm/viewer3d/perspective.py test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py`
- `uv run --extra viewer pytest test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Continue with live verification and renderer fidelity: normals, UVs,
per-face/material grouping, and viewer-grade sculpt stitching.

## Update 2026-05-17: First Real Mesh Asset Path

### What Changed

- Added `src/vibestorm/caps/get_mesh_client.py` for `GetMesh` asset fetches.
- The session seed-cap prelude now requests `GetMesh2` and `GetMesh`, prefers
  `GetMesh2`, and defers mesh fetches until a mesh object is seen.
- Mesh objects discovered through sculpt `ExtraParams` type `0x30` with
  `sculpt_type=5` are fetched by `mesh_id`, cached as raw `.llmesh` files
  under `local/mesh-cache/`, and republished through `MeshAssetReady`.
- Added `src/vibestorm/assets/sl_mesh.py`, a narrow SL mesh decoder:
  binary LLSD header, high-LOD block lookup, compressed LLSD submesh array
  inflate, `Position` dequantization, and `TriangleList` index assembly.
- `viewer3d` now records mesh cache paths and uploads decoded high-LOD mesh
  geometry into the existing instanced GL renderer keyed by mesh asset UUID.
  If fetch/decode is missing or fails, the existing sphere placeholder remains.

### Current Boundary

- Only `high_lod` is decoded.
- No normals, UVs, skinning/rigging, physics blocks, LOD switching, or
  per-face material grouping yet.
- Mesh asset decoding is covered by synthetic tests; it still needs live
  verification against OpenSim mesh assets.
- Sculpt maps are handled by the follow-up sculpt update below.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/assets/sl_mesh.py src/vibestorm/caps/get_mesh_client.py src/vibestorm/bus/events.py src/vibestorm/udp/session.py src/vibestorm/udp/world_client.py src/vibestorm/viewer3d/scene.py src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/perspective.py test/test_sl_mesh.py test/test_get_mesh_client.py test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py test/test_world_client.py`
- `uv run pytest test/test_sl_mesh.py test/test_get_mesh_client.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py -q`
- `uv run pytest test/test_udp_session.py test/test_world_client.py -q`

### Concrete Next Step

Create or rez a simple OpenSim mesh object, run `./run.sh tester viewer3d`,
and watch for `mesh.cache.ok` followed by visible non-placeholder geometry.
If the mesh appears, add UV/normal decode next; if it does not, inspect the
cached `.llmesh` header/block layout and adjust the LLSD/decompression path.

## Update 2026-05-17: First Sculpt Map Geometry Path

### What Changed

- Sculpted prims (`ExtraParams type=0x30`, sculpt type `1..4`) now enqueue
  their referenced sculpt texture UUID through the existing `GetTexture`
  object-texture fetch path.
- Added `src/vibestorm/assets/sculpt.py`, which converts RGB/RGBA sculpt-map
  pixels into a unit-sized triangle mesh:
  - RGB maps to local `[-0.5, 0.5]` xyz coordinates.
  - sphere/cylinder wrap horizontally.
  - torus wraps horizontally and vertically.
  - plane remains open.
  - sphere top/bottom rows converge to simple pole averages.
  - sculpt flags are preserved and applied: `0x40` reverses triangle winding
    for inside-out/inverted sculpts, and `0x80` mirrors local X.
  - large maps are downsampled to a 32x32 render grid for now.
- `viewer3d` now uploads cached sculpt PNGs as per-asset GL meshes keyed by
  sculpt texture UUID and sculpt type. If the texture is not cached or decode
  fails, the existing approximate primitive placeholder remains.

### Current Boundary

- This is not viewer-grade sculpt tessellation yet.
- No authored normals, UV recovery, exact SL stitching, mirror/invert handling,
  or sculpt LOD behavior.
- The path is covered by synthetic tests; it still needs live verification
  against local OpenSim sculpted prims.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/assets/sculpt.py src/vibestorm/udp/session.py src/vibestorm/viewer3d/perspective.py test/test_sculpt.py test/test_udp_session.py test/test_viewer3d_perspective_gl.py`
- `uv run pytest test/test_sculpt.py test/test_udp_session.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Rez or import a known sculpted prim in local OpenSim, run
`./run.sh tester viewer3d`, and check for its sculpt texture entering
`local/texture-cache/` followed by visible non-placeholder geometry. If the
shape is mirrored or pinched, tune the seam/stitching rules from the cached PNG
and live object's sculpt type.

## Update 2026-05-17: Avatar Placeholder And Camera Presets

### What Changed

- Added a dedicated `avatar_placeholder_mesh()` in `viewer3d.meshes`.
  Avatars now render as a simple human-like silhouette with torso, head, arms,
  legs, and a small forward-facing marker, rather than the cube fallback.
- The avatar mesh faces local +X, so existing ObjectUpdate quaternions visibly
  rotate the placeholder.
- Added camera presets:
  - `F1`: sim-wide orbit view.
  - `F2`: third-person view behind the avatar at roughly 10 m.
  - `F3`: avatar eye view.
- F2/F3 continuously refresh from the current avatar entity transform when
  world updates arrive.
- `docs/viewer-help.md` now lists the 3D camera keys.

### Current Boundary

- Avatar mesh is still a placeholder, not appearance-driven.
- No animations, skeleton, attachments, clothing, or body-shape visual params.
- First-person uses current avatar rotation only; camera collision and mouselook
  controls are not implemented.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/camera.py src/vibestorm/viewer3d/input.py src/vibestorm/viewer3d/meshes.py src/vibestorm/viewer3d/perspective.py src/vibestorm/viewer3d/app.py test/test_viewer3d_camera.py test/test_viewer3d_input.py test/test_viewer3d_meshes.py test/test_viewer3d_perspective_gl.py`
- `uv run --extra viewer pytest test/test_viewer3d_camera.py test/test_viewer3d_camera_matrices.py test/test_viewer3d_input.py test/test_viewer3d_meshes.py test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Live-verify F2/F3 against local OpenSim. If the camera points sideways or
backward, adjust `_avatar_forward()` based on observed avatar quaternion
convention; then add mouse steering for eye/behind modes.

## Update 2026-05-16: Grid Launchers And SL Guardrails

### What Changed

- Added thin launchers:
  - `./local.sh ...` uses `VIBESTORM_GRID_MODE=local` and the default
    `tester` profile.
  - `./opengrid.sh ...` uses `VIBESTORM_GRID_MODE=opengrid` and the default
    `osgrid` profile.
  - `./sl.sh ...` uses `VIBESTORM_GRID_MODE=sl` and the default `sl` profile.
- `run.sh` now derives a grid mode from `VIBESTORM_GRID_MODE`, the profile
  name, or a known login URI. `login-show` prints it.
- SL mode requires explicit confirmation before commands that touch the live
  simulator beyond plain login/cap inspection (`eventq`, `udp`, `handshake`,
  `session`, `console`, `viewer`, `viewer3d`, and `upload-smoke`). In
  non-interactive use, set `VIBESTORM_SL_CONFIRM=1`.
- SL mode passes `--no-auto-bake-upload` to bounded sessions, console, and both
  viewers. Deliberate user actions, including manual uploads, remain possible
  after confirmation.
- The session runtime now has `SessionConfig.auto_upload_bakes`; the Upload
  Baked Texture CAP is resolved but ignored when this flag is false.

### What Was Verified

- `bash -n run.sh local.sh opengrid.sh sl.sh`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/viewer/app.py src/vibestorm/viewer3d/app.py src/vibestorm/udp/session.py`
- `uv run pytest test/test_udp_session.py test/test_viewer3d_app_compositor.py -q`
- `git diff --check`
- `./sl.sh login-show` selects the `sl` profile and Agni login URI without
  requiring credentials.
- A non-interactive `./sl.sh session 0` with dummy env credentials refuses to
  continue without `VIBESTORM_SL_CONFIRM=1`.

### Concrete Next Step

Use `./sl.sh login-show`, then `./sl.sh bootstrap`, then a short
`./sl.sh session 20 --verbose` on a disposable SL account only after accepting
the explicit confirmation prompt.

## Update 2026-05-17: File Dialogs For Viewer File Actions

### What Changed

- Wired `pygame_gui.windows.UIFileDialog` into the 3D Object Inspector file
  actions.
- `Save Item` now opens a save path picker seeded under
  `local/asset-downloads/<task-id>/`.
- `Save Text` now opens a directory picker and saves all visible object
  script/notecard assets into that chosen folder.
- The Object Inspector now has separate upload actions:
  - `Upload File` picks one existing `.lsl`, `.txt`, or `.nc` file.
  - `Upload Dir` picks a folder and uploads all matching files in that folder.
- The app upload path now accepts either one file or one folder. It still uses
  the existing `NewFileAgentInventory` user-inventory upload path.

### Current Boundary

- Multi-save is wired for object/task inventory rows whose asset UUIDs are
  visible and retrievable.
- User-inventory directory save is not wired yet; that needs user-inventory
  asset retrieval plumbing comparable to the current object `TransferRequest`
  path, plus a row-to-folder save planner.
- Uploading back into the selected object's task inventory is still future
  work and remains the next protocol task.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/app.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_app_compositor.py test/test_viewer3d_object_inspector.py -q`
- `git diff --check`

## Update 2026-05-14: Viewer File Actions

### What Changed

- Added Object Inspector buttons for file actions:
  - `Save Item` queues a download for the selected object inventory asset.
  - `Save Text` queues downloads for every visible script/notecard asset in the
    selected object inventory.
  - `Upload` uploads local `.lsl`, `.txt`, and `.nc` files from `local/upload/`
    into the user's inventory root through `NewFileAgentInventory`.
- Downloaded object assets are written under
  `local/asset-downloads/<task-id>/` with `.lsl` for scripts, `.txt` for
  notecards, `.j2k` for textures, and `.bin` for unknown asset types.
- `AssetDataReady` handling now also drains pending file-save requests before
  showing the asset in the viewer window.

### Current Boundary

- Bulk object download is now wired for assets whose UUID is visible in the
  task inventory listing. If OpenSim withholds the asset UUID, the viewer still
  reports that as a permission/protocol limitation rather than issuing a doomed
  transfer request.
- Upload is currently user-inventory upload only. True object upload/sync needs
  the separate task-inventory update caps (`UpdateScriptTaskInventory`,
  `UpdateNotecardTaskInventory`, etc.) or equivalent UDP update flow.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/hud.py test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py -q`

### Concrete Next Step

Implement the task-inventory update capability client and add a sync planner
that compares `local/asset-downloads/<task-id>/` against the selected object's
script/notecard inventory before uploading changes back into the object.

## Update 2026-05-14: Interactive Login Profile

### What Changed

- `run.sh` now loads login details from env vars first, then from ignored
  `local/vibestorm-login.env` if present.
- `run.sh` now accepts a profile name before the command. The default profile
  remains `local/vibestorm-login.env`; named profiles use ignored files like
  `local/vibestorm-login-tester.env`.
- `./run.sh tester ...` has a built-in local OpenSim fallback for the
  `Vibestorm Tester` account if that profile file does not exist yet. Env vars
  and explicit profile files still override the fallback.
- Added `./run.sh login`, `./run.sh login-show`, and `./run.sh login-reset`
  for changing user, password, sim preset, or start location without manually
  editing the profile.
- If a login command is launched with missing details from an interactive
  terminal, `run.sh` prompts for sim location (`localhost`, `opengrid`, `sl`,
  or `custom`), first name, last name, and password.
- Prompted credentials can be stored in `local/vibestorm-login.env` with mode
  `600`. This is local-file storage for development convenience, not encrypted
  OS keyring storage.
- Login-capable Python entrypoints now translate `LoginError` to exit status
  `10`. If a saved login command exits with status `10` from an interactive
  terminal, `run.sh` asks whether to re-enter saved login details and retry
  once. Other crashes/errors keep their original nonzero status and are not
  treated as failed logons.
- The `opengrid`/`osgrid` preset uses OSgrid's published login URI:
  `http://login.osgrid.org/`.
- Fixed a 3D viewer asset-view crash where the `AssetDataReady` subscriber
  unpacked object-inspector asset metadata as three fields even though the HUD
  stores five fields (`asset_id`, `asset_type`, `item_name`, `task_id`,
  `item_id`).

### What Was Verified

- Found the existing local test credential in ignored OpenSim console history,
  not in a tracked env file.
- `Vibestorm Tester` bootstrap succeeded using that ignored local credential.
- The upload smoke test succeeded after the stale "already logged in" presence
  expired.
- Prompt/storage path was checked against a temporary profile; it wrote a
  shell-sourceable env file with mode `600` before the intentionally bad login
  URI failed.
- `./run.sh login-show` shows only non-secret profile fields plus
  `password=set/missing`.
- Failure handling was checked with a temporary stale profile: interactive
  commands now offer one re-entry/retry path, while noninteractive failure
  preserves the underlying nonzero exit status.
- `./run.sh tester login-show` resolves the built-in local test profile when no
  `local/vibestorm-login-tester.env` file exists.
- `bash -n run.sh`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/viewer/app.py src/vibestorm/viewer3d/app.py test/test_viewer3d_app_compositor.py`
- `uv run --extra viewer pytest test/test_viewer3d_app_compositor.py test/test_viewer3d_object_inspector.py -q`
- `uv run pytest test/test_asset_upload_client.py -q`

### Concrete Next Step

Implement object/task-inventory update caps so the new file UI can upload back
into the selected object rather than only into user inventory.

## Update 2026-05-13: NewFileAgentInventory Upload Smoke

### What Changed

- Added `src/vibestorm/caps/asset_upload_client.py` for the generic
  `NewFileAgentInventory` capability flow:
  - LLSD metadata prelude with `asset_type`, `inventory_type`, `folder_id`,
    `name`, `description`, and permission masks.
  - one-shot raw-byte POST to the returned uploader URL.
  - completion parsing for `state`, `new_asset`, `new_inventory_item`, and
    returned permission masks.
- Added `vibestorm upload-empty-text-smoke` and `./run.sh upload-smoke`.
  The command creates `local/upload-smoke/empty-space.txt` as an empty file,
  appends one space, uploads that one byte as a notecard/text item, then
  confirms the returned inventory item through `FetchInventory2`.
- Added focused tests in `test/test_asset_upload_client.py`.

### What Is Now Known

- Local OpenSim source for `NewAgentInventoryRequest` creates both the asset
  UUID and inventory item UUID server-side (`UUID.Random()`), so the new-file
  upload path should always return fresh GUIDs rather than client-chosen IDs.
- The generic upload completion reply shape is close to baked-texture upload
  but includes `new_inventory_item` and permission-mask fields.

### What Remains Unknown / TODO

- This is a CLI smoke path only. Viewer create/save/upload UI is still not
  wired.
- Object/task-inventory update caps (`UpdateScriptTaskInventory` /
  `UpdateNotecardTaskInventory`) are still separate future work.

### What Was Verified

- `uv run ruff check src/vibestorm/caps/asset_upload_client.py test/test_asset_upload_client.py`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/caps/asset_upload_client.py test/test_asset_upload_client.py`
- `uv run pytest test/test_asset_upload_client.py test/test_inventory_caps_client.py -q`
- `uv run pytest -q` -> 487 passed, 28 pygame_gui font warnings
- Live OpenSim smoke with the ignored local `Vibestorm Tester` credential:
  uploaded one byte, returned `new_asset=8a3bc672-4a0e-4542-80dc-0973d63fd5e2`,
  returned `new_inventory_item=77798038-e03a-4dd5-8704-031203269a63`, and
  confirmed that item via `FetchInventory2`.

### Concrete Next Step

Wire this path into the viewer inventory UI as a minimal "new text/notecard"
action, then build richer save/edit flows on top.

## Update 2026-05-10: Asset Viewer — Read-Only Notecard / Script / Texture Display

### What Changed

Full end-to-end plumbing for viewing object-inventory assets (notecards, LSL scripts,
textures) in the 3D viewer. Read-only, no upload/edit yet.

**Protocol layer (`src/vibestorm/udp/`)**

- `messages.py`: Added `TransferInfoMessage`, `parse_transfer_info`,
  `TransferPacketMessage`, `parse_transfer_packet`, and `encode_transfer_request`.
  These cover the `TransferInfo` and `TransferPacket` UDP messages used by the
  simulator's asset-delivery channel.
- `session.py`: Added `PendingAssetTransfer` dataclass; `fetched_assets: dict[UUID, bytes]`
  and `pending_asset_transfers: dict[UUID, PendingAssetTransfer]` on
  `LiveCircuitSession`. Added `build_transfer_request_packet()`,
  `_handle_transfer_info()`, and `_handle_transfer_packet()` methods.
  - Supports **TaskInventory (source_type=3)** transfers: when `task_id` and `item_id`
    are provided, the expanded `TransferRequest` params are used (allowing
    retrieval of copy-protected scripts/notecards from object inventory).
  - Uses `item_id` as a surrogate `asset_id` for completion tracking when the
    sim hides the real asset UUID (sending zeros).
- `world_client.py`: Resolves `owner_id` from `world_view` when performing a
  `TaskInventory` transfer.


**Bus layer (`src/vibestorm/bus/`)**

- `commands.py`: Added `RequestAssetData(asset_id, asset_type)` command.
- `events.py`: Added `AssetDataReady(region_handle, asset_id, asset_type, data)` event.

**World client (`src/vibestorm/udp/world_client.py`)**

- Registered handler for `RequestAssetData` → calls `build_transfer_request_packet`
  and queues the outbound packet.
- Translates `transfer.complete` session events into typed `AssetDataReady` bus events.

**HUD (`src/vibestorm/viewer3d/hud.py`)**

- `inspector_inventory` changed from `UITextBox` → `UISelectionList` so items are
  individually selectable.
- `_object_inventory_html()` (renamed semantically; returns `list[str]`) now renders
  each inventory item as `"Name [asset_type_or_inv_type]"`, with NUL-char stripping.
- New `on_view_asset: Callable[[UUID, int], None]` callback on `HUD.__init__`.
- New `inspector_view_asset_button` beside Load Inventory; enabled when an inventory
  item with a viewable asset is selected.
- `register_inventory_snapshot_for_view(snapshot)` — called when inventory arrives;
  builds `_inspector_item_asset_map` so the button knows which asset+type to request.
- `enable_view_for_item(item_key)` — called when a selection-list row is highlighted.
- `show_asset_data(asset_id, asset_type, data, item_name=…)` — decodes and displays:
  - asset_type 7 (notecard) / 10 (lsltext): UTF-8 text in `asset_viewer_text`.
  - asset_type 0 (texture): decoded via PIL → pygame Surface in `asset_viewer_image`.
  - Other types: size/type summary.
- New `asset_viewer_window` (`UIWindow`, resizable, hidden by default).
- `_asset_type_string_to_int()` module-level helper converts string → int.

**App (`src/vibestorm/viewer3d/app.py`)**

- `on_view_asset` callback wired to `client.bus.dispatch(RequestAssetData(…))`.
- Bus subscriptions added after HUD creation:
  - `AssetDataReady` → `hud.show_asset_data(…)`.
  - `ObjectInventorySnapshotReady` → `hud.register_inventory_snapshot_for_view(…)`.
- Session-event logging extended to include `"transfer."` prefix.

### What Is Now Known

- The Transfer protocol handshake (TransferRequest → TransferInfo → TransferPacket*)
  works identically to the Xfer handshake but uses a different packet set.
- Texture bytes coming through Transfer are raw J2K; PIL can decode them if installed.
- The `_ASSET_TYPE_MAP` in hud.py lists all known SL/OpenSim asset type strings and
  their integer equivalents.

### What Remains Unknown / TODO

- Real-world test against a live OpenSim instance (no live session done yet).
- Texture assets via the GetTexture capability (HTTP) are faster; Transfer is UDP only.
  A future pass should prefer GetTexture for texture type=0 when the cap is available.
- Download / save-to-disk is not wired; next feature track.
- Upload (create/edit notecard, script) is not wired; later feature track.

### What Was Verified

- **Protocol plumbing**:
    - `TransferRequest` (source_type=2) baseline successfully retrieves global assets.
    - Correction from OpenSim source: `TransferRequest` (source_type=3 / `SimInventoryItem`) uses 101-byte params, not 85 bytes:
      `AgentID, SessionID, OwnerID, TaskID, ItemID, AssetID, AssetType, IsPriority`.
      OpenSim reads `TaskID` at offset 48, `ItemID` at 64, and the requested
      asset UUID at 80 before fetching from the asset service.
    - Status=1 in `TransferPacket` is now correctly treated as 'Done' rather than an error.
    - Asset data up to 80KB+ successfully received and reassembled across 130+ packets.
- **UI Integration**:
    - HUD successfully captures and passes `task_id` and `item_id` to the session layer.
    - Automated test script (verified locally then removed) successfully completed the full login -> object search -> inventory load -> asset fetch loop.
- `python3 -m pytest` → **479 passed, 0 failed**.

### Concrete Next Step

Perform a final manual visual check in the 3D viewer: select a scripted object, load its inventory, and "View" a script or notecard. Then proceed to the next feature track: **Download / Save to Disk**.


### Blocker: Task Inventory Asset Silence

While the protocol plumbing for `TransferRequest` is implemented and verified for global assets (`source_type=2`), requests for protected object inventory assets (`source_type=3`) currently result in simulator silence in the manual viewer run.

- **Status**:
    - `TransferRequest` (source=3) now dispatches the OpenSim-compatible 101-byte parameter block: `AgentID(16), SessionID(16), OwnerID(16), TaskID(16), ItemID(16), AssetID(16), AssetType(4), IsPriority(1)`.
    - `OwnerID` is resolved from `ObjectPropertiesFamily` before the request.
    - If the object-inventory listing reports a zero asset UUID, Vibestorm no
      longer sends a doomed transfer request. The Object Inspector marks the row
      as `asset withheld`, opens an explanatory Asset Viewer message, and logs
      `object_inventory.asset_withheld`.
    - OpenSim source shows zero asset IDs are intentional when the simulator
      withholds task inventory asset UUIDs because object inventory edit rights
      or script/notecard permissions are insufficient.
- **Known Working Case**:
    - An automated test once successfully fetched ~80KB for a `source_type=3` request, but results are inconsistent.
- **Top Hypotheses**:
    1. **Permission Denial**: The simulator may be silently dropping the request if the `OwnerID` or `AgentID` don't have view permissions for the specific `item_id`.
    2. **Xfer/Transfer Conflict**: The simulator may be ignoring new `TransferRequest`s while an `Xfer` (used for the initial inventory listing) is still technically open or being cleaned up.
    3. **Identifier Mismatch**: Verify if `TaskID` must be the object's root UUID or if it needs to be the specific part UUID for multi-part objects.
    4. **Zeroed AssetID**: OpenSim may send all zeros for `AssetID` in some task inventory listings. Source_type=3 still needs the requested asset UUID at offset 80, so zero IDs are currently treated as server-withheld assets rather than downloadable assets.

---




## Update 2026-05-09: First Inventory Manager UI

The next viewer track has started with user inventory before object
inspection/object inventory.

- `viewer3d` View -> Inventory now opens an "Inventory Manager" window instead
  of a flat text dump.
- The window is still read-only and uses the existing login/prelude
  `InventorySnapshotReady` data from `FetchInventoryDescendents2` /
  `FetchInventory2`.
- The left pane is a selection list with loaded folders, child folder entries,
  items, and resolved Current Outfit links. Child folders whose contents are
  listed but not fetched are marked `(not loaded)` / `F*`.
- Follow-up polish changed the left pane to a more traditional tree-like row
  format: folder rows are left-indented with `▾` / `▸`, loaded/unloaded folder
  glyphs (`◼` / `◻`), ordinary item bullets (`•`), and link arrows (`↗`).
  This is still backed by `pygame_gui.UISelectionList`, not a true native tree
  widget.
- Folder opening is now wired for user inventory. Selecting an unloaded child
  folder and pressing Open, or double-clicking it, calls
  `FetchInventoryDescendents2` for that folder, merges the returned
  `InventoryFetchSnapshot` into the existing snapshot, and republishes
  `InventorySnapshotReady` through the existing session event bridge.
- The right pane shows details for the selected folder/item: IDs, parent/owner
  fields, type/inventory type, flags, description, link status, and load state.
- Added `inventory_snapshot_rows()` as a pure row-model helper with tests, plus
  HUD selection/details/open tests.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_hud_render_mode.py`
- `uv run --extra viewer pytest test/test_viewer3d_hud_render_mode.py -q`
- `uv run --extra viewer pytest test/test_inventory_caps_client.py test/test_viewer3d_hud_render_mode.py -q`

Known remaining inventory/tooling work:

- object inspector UI is now implemented with a read-only list of nearby objects and property joining.
- object inventory tree is protocol work after that: request/load inventory for selected nearby objects, then build editor/open/save/upload commands on top.

### Next Steps for Object Inventory

Goal:

- The View/Tools menu now has an "Object Inspector" window.
- The window provides a split-pane interface. The left pane shows nearby objects from `Scene.object_entities`, sorted by distance from `Scene.avatar_position` when available.
- The right pane shows grouped details joined from `SceneEntity` and `WorldView.objects` using `local_id_to_full_id`:
  - Identity: local ID, full UUID, display name
  - Transform: position, rotation, scale
  - Shape/render: pcode, kind, primitive shape, material, click action, default texture UUID, and per-face texture UUIDs
  - Object update/debug: variant, update flags, CRC, and data sizes
  - Properties: owner/group/permissions, name, description, sale fields from `ObjectPropertiesFamily`
- The bottom right pane contains an "Object Inventory" placeholder ("not requested yet").
- Tests were added in `test/test_viewer3d_object_inspector.py`.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py -q`

Object inventory notes:

- Object inventory loading is now wired read-only for the inspector.
- The inspector bottom pane has a "Load Inventory" button. It dispatches
  `RequestObjectInventory(local_id)`, which queues `RequestTaskInventory`.
- The simulator's `ReplyTaskInventory` supplies a task UUID, serial, and xfer
  filename. Vibestorm now sends `RequestXfer`, confirms `SendXferPacket`
  packets, assembles the xfer payload, parses common `inv_item` blocks, and
  publishes `ObjectInventorySnapshotReady`.
- Empty `ReplyTaskInventory` filenames are treated as successful empty object
  inventory loads. This avoids leaving the inspector stuck in "request sent"
  when an object simply has no contents.
- Viewer3D prints object-inventory debug events to stdout while running:
  `task_inventory.request`, `reply`, `xfer.request`, `xfer.packet`,
  `xfer.confirm`, `xfer.unknown`, and `ready`. Use these lines to distinguish
  a missing simulator xfer from an xfer ID/packet parsing problem.
- `Scene.object_inventory_snapshots[local_id]` stores loaded object inventory,
  and the inspector displays item name/type/UUID in the bottom pane.
- Lazy user-inventory folder loads also materialize a successful empty response
  as a loaded folder with zero descendants, so the tree can distinguish
  "empty" from "not loaded yet".
- The parser is intentionally partial and read-only. It does not yet implement
  item open, asset download, save, upload, move, delete, script running state,
  or permission editing.

Verification:

- `uv run ruff check --select F,I src/vibestorm/world/object_inventory.py src/vibestorm/udp/messages.py src/vibestorm/udp/session.py src/vibestorm/udp/world_client.py src/vibestorm/bus/commands.py src/vibestorm/bus/events.py src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/scene.py test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py -q`
- `uv run --extra viewer pytest`

## Update 2026-05-06: Terrain Heightmap + Surface Mesh

Viewer3D terrain work has moved past raw patch extraction.

- `src/vibestorm/world/terrain.py` now decodes standard 16x16 land
  `LayerData` patches all the way to height samples: libomv-compatible
  dequant table, copy-matrix reorder, two-pass IDCT, and final
  `mult/addval` height arithmetic.
- Important correction: the coefficient stream decoder now matches
  libopenmetaverse's real bit codes (`0` zero, `10` EOB, `110` positive,
  `111` negative). The earlier synthetic tests had encoded a different
  symmetric shape, so they were corrected at the same time.
- `RegionHeightmap` accumulates decoded land patches into a 256x256
  row-major sample array and tracks a `revision` for render-cache rebuilds.
- `viewer3d.Scene` consumes `LayerDataReceived` bus events, ignores non-land
  layers and other regions, and keeps the current `terrain_heightmap`.
- `PerspectiveRenderer` now builds a textured terrain heightfield mesh from
  the scene heightmap and draws it through the existing ground shader. It
  falls back to the flat region ground quad until terrain packets arrive.

Verification:

- `uv run pytest test/test_world_terrain.py test/test_viewer3d_scene.py -q`
- `uv run pytest test/test_viewer3d_perspective_gl.py -q`

Known remaining terrain gaps:

- Extended 32x32 patches are still rejected; only standard 16x16 land
  patches are decompressed.
- Wind/cloud layer data is surfaced but not rendered.
- No live OpenSim visual pass was run in this handoff; next concrete step is
  `./run.sh opensim` plus `./run.sh viewer3d`, then switch to 3D and confirm
  the ground surface is visibly elevated instead of flat.

Follow-up from the first live check:

- `viewer3d` now starts in 3D mode by default (`--render-mode 2d-map` is
  available for the old startup behavior).
- The viewer loop is capped at 20 FPS by default via `--max-fps 20`; pass
  `--max-fps 0` to disable the cap.
- The perspective renderer now draws terrain with a 1x1 fallback ground
  texture if terrain height data exists before the region map tile has loaded.
  This fixes the "no map tile means no terrain draw" path.
- A Diagnostics window is visible by default in 3D mode and available from
  Debug -> Diagnostics. It reports FPS, mode, region/map path, terrain
  dimensions/patch count/revision/min/max height, water level plus avatar
  under/above-water status, object/avatar/texture/chat counts.

Second follow-up from live debugging:

- The water plane now uses the parsed `RegionHandshake.WaterHeight` stored in
  `WorldView.region.water_height` instead of always using the default 20 m.
  The diagnostics window reports that same scene value.
- Basic 3D orbit inspection controls are wired:
  - right-drag rotates the orbit camera
  - mouse wheel changes orbit distance
  - Shift+right-drag pans the orbit target
  - Shift+PageUp/PageDown lifts/lowers the orbit target
  - Center/C now retargets orbit mode to the avatar/coarse self position

Third follow-up from live debugging:

- The water shader now applies subtle coordinate-based noise so the water
  plane is visually readable instead of a flat translucent sheet.
- Terrain heightfields now draw a bright green wire/grid overlay on top of the
  filled terrain mesh. This is intentionally texture-independent, so it should
  confirm whether `LayerData` has produced a mesh even when the map/terrain
  texture is missing or visually ambiguous.

Fourth follow-up for terrain diagnosis:

- `viewer3d` now accepts `--debug-terrain synthetic`. This seeds
  `Scene.terrain_heightmap` with a deterministic hill/valley/ripple surface
  and ignores live land `LayerData` while the override is active.
- Diagnostics now show terrain source (`live`/`synthetic`), min/max/mean,
  first patch keys, and the first four sample values. This should make it
  clear whether we are failing before GL (bad/flat decoded samples) or in GL
  (synthetic terrain also fails to draw).
- Follow-up after synthetic showed only grid lines: terrain fill is now a
  solid untextured green material whenever a heightmap exists. The textured
  ground path is left for the no-heightmap flat floor and future texture work.
- Follow-up after synthetic still looked flat: rendered terrain gained
  height-based color grading and a `--terrain-z-scale` option. It temporarily
  defaulted to `4.0` while geometry was suspect; later live validation moved
  the default back to real meter scale.
- Follow-up after synthetic still looked flat again: `Scene.apply_region_changed`
  was clearing the synthetic debug heightmap during the initial live
  `RegionChanged` event. Synthetic terrain now survives region changes, while
  normal live terrain is still cleared on region change.
- To diagnose live flat terrain, `RegionHeightmap.latest_layer_stats` now records
  the latest land LayerData packet's patch positions, ranges, DC offsets,
  prequant values, nonzero coefficient count, max absolute coefficient, and
  decoded per-packet height min/max/mean. Diagnostics shows these as `layer:`
  and `coeff:` lines.

Fifth follow-up for live flat terrain:

- Debug -> Sim Debug opens a "Sim Debug Heightmap" window showing the current
  `Scene.terrain_heightmap.samples` as a black/white normalized image. This is
  intentionally independent from the 3D mesh/material path; if live terrain is
  still flat in-world but the image has contrast, the bug is in mesh upload or
  render scaling. If the image is uniform gray, the decoded server heightmap is
  actually flat at the sample-array level.
- The heightmap window status line reports source (`live`/`synthetic`),
  dimensions, patch count, and min/max height for quick screenshots/logging.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_hud_render_mode.py`.

Sixth follow-up after Sim Debug showed uniform gray live terrain:

- The root cause was the terrain `BitPack` bit order. OpenMetaverse writes
  integer fields as little-endian byte chunks while retaining MSB-first
  ordering inside each chunk. Live OpenSim LayerData starts with `08 01 10 4c`
  for stride 264, patch size 16, land type 0x4c; the previous reader treated
  the whole stream as MSB-first.
- `BitPack` and `BitPackWriter` now match OpenMetaverse chunk order. Tests pin
  the live header prefix (`0801104c`) and coefficient prefix-code bytes
  (`10 -> 80`, `110 -> c0`, `111 -> e0`).
- Saved LayerData previews now decode to plausible headers such as stride 264,
  patch size 16, land type 0x4c, ranges 1/3, and valid patch coordinates.

Seventh follow-up after live terrain had plausible heights but wrong shape:

- The first `BitPack` correction still mishandled non-byte-aligned multi-byte
  values. OpenMetaverse continues a split input byte across output-byte
  boundaries; after `PackBits(2, 2)`, `PackBits(0x123, 10)` must produce
  `88 d0`. The Python reader/writer now pins and matches that behavior.
- `END_OF_PATCHES` is decimal `97` (`0x61`), not hex `0x97`; the old constant
  came from misreading the name/comment. Tests now pin the marker byte.
- Added an OpenSim-generated sloped-patch fixture using
  `OpenSimTerrainCompressor.CreatePatchFromTerrainData`; Python decode
  recovers `height = 20 + x * 0.05 + y * 0.02` within about 0.01 m. This
  verifies coefficient magnitudes, EOD, dequant, copy matrix, IDCT, and
  per-patch placement against the actual OpenSim compressor.

Eighth follow-up after live terrain shape looked correct:

- `--terrain-z-scale` now defaults to `1.0` again so rendered terrain uses
  real meter scale. The option remains available for debugging exaggerated
  relief, e.g. `--terrain-z-scale 4`.

Ninth follow-up for render-debug controls:

- View -> Render Settings now opens a small render settings window. It exposes
  checkbox-style buttons for Terrain Surface, Mesh Lines, Water, and Objects.
  These write through to `Scene.render_terrain`, `render_terrain_lines`,
  `render_water`, and `render_objects`.
- The same window has a Water opacity slider. `Scene.water_alpha` defaults to
  `0.72`, making water less transparent than the original debug plane while
  still leaving submerged terrain readable.
- The renderer honors those scene flags in `PerspectiveRenderer.render_gl`.
  Mesh lines can now be hidden without disabling terrain fill; water and
  object rendering can also be isolated while debugging.

Tenth follow-up for first-pass lighting:

- `SimulatorTimeSnapshot` now retains the UDP `SunDirection`, and
  `viewer3d.Scene` surfaces it as `Scene.sun_direction` alongside
  `sun_phase`.
- The 3D renderer applies ambient + directional lighting to object meshes.
  Primitive normals are currently approximated from local vertex position, so
  this is a visual depth cue rather than final face-accurate prim shading.
- Filled terrain now uses a fragment normal derived from the rendered height
  surface and shades against the same sun direction. Mesh lines remain
  unlit/debug-bright.
- Texturing has not started yet beyond the existing map-tile/fallback terrain
  texture path. The next concrete rendering step is proper terrain/prim texture
  interpretation, starting with full `TextureEntry` decode and asset lookup.

Eleventh follow-up for first-pass texturing:

- When a terrain heightmap exists and the region map tile has been cached, the
  3D renderer now drapes that map tile over the terrain mesh instead of using
  only the debug height-color fill. If no map tile is available, the existing
  untextured height-color fill remains the fallback.
- The live session now watches `WorldView.objects` for non-zero
  `default_texture_id` values, fetches one pending texture at a time via the
  existing `GetTexture` capability, decodes JPEG2000, and caches PNGs under
  `local/texture-cache/<uuid>.png`.
- `texture.cache.ok` session events are bridged to a typed
  `TextureAssetReady` bus event. `viewer3d.Scene` records those paths in
  `Scene.texture_paths`.
- `PerspectiveRenderer` groups primitive draws by shape and available default
  texture. Textured prims use a coarse generated UV projection in the shader;
  this is intentionally first-pass only and not a replacement for proper
  `TextureEntry` per-face UV/material decode.
- Verification: `uv run --extra viewer pytest test/test_world_client.py
  test/test_udp_session.py test/test_viewer3d_scene.py
  test/test_viewer3d_perspective_gl.py`.

Twelfth follow-up for object UV scaling:

- The first object texture shader projected every prim texture through local
  XY. That made side faces collapse to a single texture column/row and looked
  like the object was sampling one pixel.
- Object texture sampling now uses generated per-face projection in the shader:
  X-facing faces sample Y/Z, Y-facing faces sample X/Z, and Z-facing faces
  sample X/Y. UVs are clamped to the unit face instead of wrapped at exact
  edges.
- Added a matching pure `generated_texture_uv()` helper and tests so the
  intended projection stays pinned.
- This still is not full SL `TextureEntry` material fidelity. It fixes the
  gross scale/projection issue for default-textured prim draw groups; per-face
  image IDs, repeats, offsets, rotations, alpha, and glow still need real
  `TextureEntry` decode and mesh/material batching.

Thirteenth follow-up for per-face texture plumbing:

- Added `vibestorm.world.texture_entry` with a first-pass `TextureEntry`
  parser. It decodes the image UUID section: default texture UUID plus
  face-mask texture UUID overrides using the OpenMetaverse MSB-first 7-bit
  mask encoding.
- `ObjectUpdateEntry`, `WorldObject`, and `viewer3d.SceneEntity` now retain the
  parsed `TextureEntry` object while preserving the existing
  `default_texture_id` field for fallback rendering.
- Improved-terse texture-entry payloads also update the retained parsed
  texture entry when they carry at least a default UUID.
- The renderer is not yet using per-face overrides. The next concrete step is
  to add logical face IDs to cube mesh triangles and batch/draw cube faces by
  `texture_entry.texture_for_face(face_index)`, falling back to the default
  texture for shapes without face IDs.

Fourteenth follow-up for cube per-face texture rendering:

- Cube rendering now draws six logical face submeshes. Each face resolves its
  texture through `SceneEntity.texture_entry.texture_for_face(face_index)` and
  falls back to `default_texture_id`/tint if the override or cached asset is
  unavailable.
- The object texture fetch queue now includes face-override texture UUIDs from
  parsed `TextureEntry`, not only default texture IDs.
- Non-cube primitive shapes still use the default texture draw path. That keeps
  spheres/cylinders/tori/prisms stable until their SL face mapping is modeled.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_perspective_gl.py test/test_udp_session.py`.

## Update 2026-05-04: 3D Viewer Fork

3D viewer work has begun in a forked package rather than as an in-place
refactor of the 2D viewer.

- `src/vibestorm/viewer3d/` is a byte-for-byte copy of `src/vibestorm/viewer/`
  with intra-package imports retargeted and the window caption changed to
  "Vibestorm 3D Viewer". Behavior is identical to the 2D viewer today.
- `./run.sh viewer3d` runs the fork; `./run.sh viewer` is unchanged.
- The 2D `viewer/` package is now the stable reference. We don't intend to
  invest further in it; it stays for visual comparison and as a known-good
  baseline. Tests for `viewer/` still pass.
- The full plan, including renumbered implementation order with the fork as
  step 0, lives in `docs/viewer-3d-plan.md`.

Steps 1a, 1b-i, and 1b-ii are done.

- 1a: `viewer3d.scene` now exposes a renderer-agnostic `SceneEntity`
  (replacing `Marker`) with `kind`, full quaternion `rotation`,
  `default_texture_id`, `tint`, and a `shape` field. `Scene.sun_phase` is
  surfaced from `WorldView.latest_time`. `object_entities` /
  `avatar_entities` replace the old marker dicts.
- 1b-i: protocol fix. The `ObjectUpdate` parser had two self-cancelling
  off-by-one bugs (22-byte path/profile block, U16 ExtraParams length).
  Fixed: the block is decoded as 23 bytes via a new `PrimShapeData`
  dataclass, and ExtraParams uses U8 length per template. Side effect:
  `default_texture_id` is now the real UUID instead of being shifted
  left by one byte with a leading `0x00`. `docs/reverse-engineered-
  protocol.md` corrected; `test/fixtures/live/index.json` regenerated
  (now 43 captures vs 8).
- 1b-ii: `SceneEntity.shape` is now populated from real wire data via a
  new `classify_prim_shape(path_curve, profile_curve)` helper covering
  cube/sphere/cylinder/torus/prism/ring/tube. The OpenSim default sphere
  fixture classifies as `"sphere"`.

Test suite now 285 tests, all green. The 2D viewer reference under
`src/vibestorm/viewer/` is untouched.

Step 2 is done: `viewer3d/renderer.py` defines a `ViewerRenderer` protocol
plus a `TopDownRenderer` that wraps today's 2D draw. The app loop now
routes `update` / `render` / `clear_caches` through the renderer instead
of calling `render_scene` directly. Behavior unchanged. 289 tests, all
green.

Step 3 is done: "Render: 2D Map" and "Render: 3D" buttons in the View
menu, HUD tracks `render_mode`, status bar shows the active mode, and
selecting 3D posts a chat alert ("3D mode is not implemented yet").
296 tests, all green (7 HUD tests verified under `./run.sh test`).

Step 4 is done: `Camera3D` is a mode-aware camera with a Map mode that
reproduces today's pan/zoom math bit-for-bit, plus state fields for
Orbit/Eye/Free modes (yaw, pitch, distance, eye_position, target).
`Camera = Camera3D` alias preserves existing imports. The HUD render-
mode callback now also calls `camera.set_mode(...)`. 311 tests, all
green.

**The pre-3D refactor (steps 1a, 1b-i, 1b-ii, 2, 3, 4) is complete.**

Next planned step (`viewer-3d-plan.md` step 5): moderngl bootstrap. Add
the dependency behind a `viewer3d` extra in `pyproject.toml`, open a
hybrid `pygame.OPENGL | pygame.DOUBLEBUF` window, and draw a single
textured fullscreen quad (the cached region map tile) plus the existing
pygame_gui HUD on top. Goal: validate the GL+HUD compositing path
before any geometry lands.

## Summary

The bird's-eye viewer now has a runnable pygame v1. Session boots, fetches
its region map tile, caches it as PNG, surfaces inbound IM/Alerts in the
event stream, and the 2D viewer consumes `WorldView` + the cached map tile
directly.

## What Is Wired

Per session, automatically:
1. `RegionHandshake` → `RegionHandshakeReply` + `MapBlockRequest` for the
   current region's grid coords (`region_x // 256`, `region_y // 256`).
2. `MapBlockReply` is parsed; the entry matching our grid coords yields a
   `MapImageID` stashed as `session.region_map_image_id`.
3. The main loop polls for (GetTexture URL + image_id + not yet fetched) and
   runs `_fetch_and_cache_region_map`: HTTP GET → J2K decode → PNG written to
   `local/map-cache/<image_id>.png`. Path is exposed as
   `session.region_map_path` and surfaces in `SessionReport.region_map_path`.
4. Inbound `ChatFromSimulator` (existing), `ImprovedInstantMessage`,
   `AlertMessage`, and `AgentAlertMessage` all emit `chat.*` session events
   with the decoded text.
5. `LiveCircuitSession.build_chat_packet(text, *, chat_type=1, channel=0)`
   returns a ready-to-send packet (reliable + zerocoded) and emits a
   `chat.outbound` event.
6. `WorldClient` queues UI-built outbound packets; `run_live_session` drains
   that queue into the active UDP socket.
7. `WorldClient` publishes `InventorySnapshotReady` after the caps prelude
   fetches the login inventory/current-outfit snapshot.
8. `TeleportLocation` commands build reliable `TeleportLocationRequest`
   packets and queue them for the active circuit.
9. `src/vibestorm/viewer/` contains the pygame viewer:
   - `camera.py`: world/screen transform, zoom, pan, fit-region.
   - `scene.py`: render-state aggregation from typed bus events + `WorldView`,
     including self-position and inventory snapshot state.
   - `render.py`: map tile, grid, region border, object/avatar markers.
   - `hud.py`: top main-menu strip, bottom status bar, resizable chat,
     movement-help, teleport, options, and inventory windows.
   - `input.py`: movement keys, zoom wheel, right-drag pan, chat focus.
   - `app.py`: login + live session task + pygame loop.
10. `docs/viewer-help.md` is loaded into Help -> Movement Help.

## How to Verify

Run a live session against local OpenSim and check the report tail:

```
./run.sh opensim &
./run.sh session
```

Look for:
- `map[tile]=cached path=...` (success) — or `image_id_only` / `none events=...`
- A PNG appearing under `local/map-cache/<image_id>.png` matching the region's
  prerendered map.

Run the GUI:

```
./run.sh opensim &
./run.sh viewer
```

Expected v1 behavior:

- cached map tile as the region background once `MapBlockReply` + `GetTexture`
  complete
- colored markers for objects and avatars from `WorldView`
- WASD/arrows update movement control flags; mouse wheel zooms; right-drag pans
- Debug -> Center or `C` recenters on the avatar/coarse self position
- Enter focuses the chat window's input; submitting text sends `ChatFromViewer`
- the status bar shows avatar position, sim, parcel placeholder, map/object/avatar/chat counts
- Help -> Movement Help opens the 2D movement help file
- View -> Inventory shows the first read-only inventory snapshot from login/current-outfit fetches
- Tools -> Teleport sends a local `TeleportLocationRequest` to the current region handle
- UI scale is automatic from desktop size: 1920x1080 is 1x, 3840x2160 is 2x.
  Override with `./run.sh viewer --ui-scale N` if needed.

## What Remains for the Bird's-Eye Plan

- Full `TextureEntry` section-walking parser (default color first, then per-face).
- `ParcelOverlay` 4 KB bitfield decode → plot-edge polylines.
- Parcel status/name wiring after parcel metadata is decoded; the HUD currently says
  `Parcel: unknown`.
- Real inventory/asset management: folder browsing beyond the first snapshot, create/upload
  flows, asset permissions, and server-side store/update actions.
- Visual live pass against OpenSim to tune marker scale/colors, status text,
  main-menu contents, teleport behavior, inventory formatting, and chat-window persistence.

These are independent and can land in any order.

## Update 2026-06-22: Mesh Normals / UVs / Material Groups

`src/vibestorm/assets/sl_mesh.py` now decodes more than positions+indices per
submesh:

- `DecodedSLMesh.normals` — per-vertex normals. Decoded from the submesh
  `Normal` u16 array (domain fixed at -1..1); when absent, computed as smooth
  per-vertex normals from the triangle geometry.
- `DecodedSLMesh.uvs` — per-vertex `TexCoord0` (u16, domain from
  `TexCoord0Domain`, default 0..1); zero-filled when absent.
- `DecodedSLMesh.material_groups` — one `MeshMaterialGroup(face_index,
  index_start, index_count)` per submesh. SL submeshes map 1:1 to prim faces,
  so `face_index` is the `TextureEntry` slot for per-face material/texture
  assignment. Indices are rebased onto the combined vertex buffer.

These fields are additive; existing `.vertices`/`.indices`/`.submesh_count`
consumers are unchanged.

### Next (live, visual) step

The shared shape shader in `viewer3d/perspective.py` fakes normals via
`local_normal = normalize(in_pos)` (no `in_normal` attribute) and uploads the
mesh VBO as positions-only (`(vbo, "3f", "in_pos")`). To actually light meshes
correctly: add an optional `in_normal` attribute + a uniform flag to the shape
program, interleave `decoded.normals` into the mesh VBO, and bind per-face
texture groups using `material_groups`. This needs the GL viewer for visual
verification, so it was not bundled with the decoder change.

## Update 2026-06-22: Parcel / EventQueue / Animation / Sound Decode Pass

Written up 2026-08-14 from commits `5c1b75d..d7cb39d`; the session ended without
a handoff entry, so this reconstructs it. ~2250 lines added across 13 files.

### What Changed

**Parcel** (`src/vibestorm/world/parcel_overlay.py`, new)

- `decode_parcel_overlay` reassembles the N `ParcelOverlay` packets into a
  region-wide 4 m LandUnit grid: `ownership_at` / `ownership_at_meters`
  (PUBLIC/OTHER/GROUP/SELF/FOR_SALE/AUCTION) plus `border_segments`
  (west/south property lines in region meters). Cell ordering mirrors OpenSim
  `LandChannel.cs` / `LandManagementModule.cs` — row-major, y south→north
  outer, x west→east inner.
- `decode_parcel_bitmap` turns the `ParcelProperties` `Bitmap` field into a
  per-parcel membership mask over the same grid (`ParcelBitmap.contains` /
  `contains_meters` / `bounds_units` / `cell_count`). Bit order mirrors
  `LandObject.ConvertBytesToLandBitmap` — linear index `y*edge+x`, LSB-first
  per byte.
- `parse_parcel_properties` (`udp/messages.py`) decodes the `ParcelData` block
  through `GroupID`: ownership, AABB extent, Bitmap, area, prim counts, parcel
  flags, sale price, and the Name/Desc/MusicURL/MediaURL strings. Trailing
  single blocks past `GroupID` are **not** decoded.
- The two pair up: the per-parcel Bitmap says which cells are mine, the
  region-wide overlay grid says who owns each cell.

**EventQueueGet** (`src/vibestorm/event_queue/events.py`, new)

- `decode_event_queue_payload` turns a parsed EQG LLSD map into typed events:
  `EnableSimulator`, `EstablishAgentCommunication`, `TeleportFinish`,
  `CrossedRegion`, `ScriptRunningReply`, `ObjectPhysicsProperties`,
  `AgentGroupDataUpdate`, plus `UnknownEvent` for unrecognized names, and the
  ack id.
- `caps.llsd.parse_xml_value` gained binary-tag support (base64 default,
  base16/base85). OpenSim's LLSD encoder emits uint/ulong as big-endian binary
  blobs (region handles, sizes, flags, `GroupPowers` u64) and IPs as 4 binary
  bytes; those are coerced back to ints / dotted-quad IPs.
- `AgentGroupDataUpdate` merges the parallel `NewGroupData` array's
  `ListInProfile` flag into each membership by index, matching OpenSim's split
  encoding.
- Shapes verified against OpenSim `EventQueueGetHandlers.cs` and
  `LLSDxmlEncode.cs`.

**Animation / sound** (`src/vibestorm/udp/messages.py`)

- `parse_avatar_animation` — High #20 (wire `0x14`). Sender avatar id plus the
  running animation list (AnimID + sequence id), with the parallel
  `AnimationSourceList` ObjectID merged per index when present. Three Variable
  blocks, each with a 1-byte count.
- `parse_object_animation` — High #30 (`0x1E`). Same Sender/AnimationList shape
  minus the source/event lists.
- `parse_sound_trigger` — High #29. One-shot world sound: sound/owner/object/
  parent ids, region handle, position, gain.
- `parse_attached_sound` — Medium #13. Object-bound: sound/object/owner ids,
  gain, flags.
- `parse_attached_sound_gain_change` — Medium #14 (ObjectID + Gain).
- `parse_preload_sound` — Medium #15 (Variable DataBlock of ObjectID/OwnerID/
  SoundID entries).

**Wiring** (`udp/session.py`, `udp/world_client.py`, `bus/events.py`)

- `LiveCircuitSession.handle_incoming` dispatches all of the above instead of
  letting them fall through as unknown. `ParcelProperties` →
  `latest_parcel_properties` + `parcel.properties`; `ParcelOverlay` →
  `parcel_overlay_packets[seq]` + `parcel.overlay` (new `parse_parcel_overlay`
  body parser: SequenceID + Variable-2 Data); animations and sounds → their
  `avatar.*` / `object.*` / `sound.*` events. Decode failures record a
  `.decode_error` event rather than throwing.
- New typed bus events: `ParcelPropertiesReceived`, `ParcelOverlayReceived`,
  `AvatarAnimationReceived`, `ObjectAnimationReceived`, `SoundTriggered`,
  `AttachedSoundReceived`, `AttachedSoundGainChanged`, `PreloadSoundReceived`,
  each carrying the decoded dataclass.
- Session stores the latest decoded animation/sound message and fires
  `on_event` synchronously *after*, so the `WorldClient` bridge reads the
  just-set slot — the same "session is source of truth" pattern as
  `terrain.layer_data`.

### What Was Verified

Unit tests only — no live sim run. Session dispatch was exercised end-to-end
through `handle_incoming` with synthetic packets, and the bus path through
`session._record_event` → subscriber. Full suite passes (619 tests as of
2026-08-14). Nothing here has been seen against live OpenSim traffic.

### Current Boundary — three decoders are written but unreachable

This is the important part for whoever picks this up. Grep confirms:

1. **The whole typed EQG module is dead code outside tests.**
   `decode_event_queue_payload` has no caller in `src/`. The poll loop
   (`event_queue/client.py`, `poll_once`) still returns raw LLSD. Nothing
   consumes `EnableSimulator`/`TeleportFinish`/`ScriptRunningReply` yet.
2. **The parcel grid decoders are never called.** `session.parcel_overlay_packets`
   accumulates raw per-sequence bytes and publishes them, but no one calls
   `decode_parcel_overlay` to reassemble the grid, and no one calls
   `decode_parcel_bitmap` on `latest_parcel_properties.bitmap`.
3. **The HUD still says `Parcel: unknown`.** `viewer3d/scene.py:246` declares
   `parcel_name` and `:275` sets it to `None` — nothing ever assigns it, even
   though `ParcelProperties.name` is now decoded and on the bus.

So the decode work landed but the consumer side did not. That is the shortest
path to visible value.

### Concrete Next Step

Close boundary 3 first — it is small and makes the parcel work observable:
subscribe `viewer3d` to `ParcelPropertiesReceived`, set `scene.parcel_name`
from the decoded name, and confirm the HUD status bar shows a real parcel name
against local OpenSim (`./run.sh tester viewer3d`). Then reassemble the overlay
grid from `parcel_overlay_packets` and draw `border_segments` as plot edges —
that closes the long-standing bird's-eye "ParcelOverlay → plot-edge polylines"
item below.

But read the 2026-08-14 live-verification note below first: `ParcelProperties`
never arrives unsolicited, so step one is sending a request, not subscribing.

Wiring the typed EQG decoder into `poll_once` is independent and can land in
any order.

## Update 2026-08-14: First Live Verification Of The Parcel Decoders

First run of the 2026-06-22 work against live OpenSim (`Vibestorm Test`,
`127.0.0.1:9000`). Two 90–100s `./run.sh session --verbose` runs plus a
scripted bus-subscriber harness. No code changed.

### What Was Verified Live

- Session is healthy end to end: login → caps → UDP handshake →
  `AgentMovementComplete` → 90s of traffic → clean `LogoutRequest`/`LogoutReply`.
  293 messages, 9 seed caps, bake upload accepted (`uploaded:5 serial:5`),
  33 objects tracked with 32 carrying properties, map tile cached.
- `ParcelOverlay` **decodes correctly on real bytes.** The sim sent 4 packets
  (seq 0–3, 1024 B each) = 4096 cells = a 64×64 grid, exactly the predicted
  256 m ÷ 4 m LandUnit layout. `decode_parcel_overlay` reassembled it without
  error: `cells_per_edge=64`, ownership histogram `{other: 4096}` (the test
  region is one parcel owned by another account), and 128 border segments —
  64 west edges plus 64 south edges, i.e. exactly the region perimeter, which
  is what a single region-wide parcel should produce. First segments
  `(0,0,0,4)`, `(0,0,4,0)`, `(4,0,8,0)` confirm the west-edge/south-edge
  meter geometry.
- `AvatarAnimation` decodes live (`anims=1`, correct sender agent id).
- Session dispatch emits `parcel.overlay` and `avatar.animation` events with no
  `.decode_error` anywhere in either run.

### The Finding That Changes The Next Step

**`ParcelProperties` never arrives on its own — 0 received across both runs.**
No `ParcelPropertiesRequest` encoder existed anywhere in `src/`, so parcel data
was simply never asked for. The HUD `Parcel: unknown` problem was therefore not
a missing subscription.

> **Superseded on the same day — read the 2026-08-14 follow-up below.** The
> conclusion drawn here, that "the UDP path is the one to build against", is
> backwards. `ParcelProperties` is delivered over the *event queue* only. The
> request is still needed and still goes out over UDP; the reply does not come
> back that way.

### Still Unverified

`ObjectAnimation` and all four sound messages did not appear in live traffic —
a quiet test region with one avatar and no scripted sound emitters. They remain
synthetic-test-only. `decode_parcel_bitmap` is likewise unexercised on real
data, since it needs a `ParcelProperties` Bitmap to decode.

### Pre-existing, Not A Regression

The event-queue poll times out once at session start (`event queue poll timed
out after 5.0s`) and is not retried — no successful EQG poll occurs during a
normal session. That is the long-poll shape, but it means the EQG path is
effectively dormant, which is worth confirming when the typed decoder gets
wired into `poll_once`. One `GetTexture` 404 also appears for a texture the sim
does not hold.

*(That dormant queue turned out to be the actual root cause — see below.)*

## Update 2026-08-14: Parcel Identity, End To End

The HUD now shows a real parcel name. Three separate gaps were stacked behind
that one symptom, and each had to be closed before the next was visible.

### 1. Nothing ever asked for parcel data

Added `encode_parcel_properties_request` (`udp/messages.py`,
ParcelPropertiesRequest = Medium/11, Zerocoded) and autosend it after
`RegionHandshake`, beside `MapBlockRequest`, guarded by
`parcel_properties_request_sent`.

The bounds matter. `LandManagementModule.ClientOnParcelPropertiesRequest`
takes two paths: a box no wider than one 4 m LandUnit resolves a single
parcel, while anything larger is divided into LandUnits and walked, replying
once per distinct parcel found. So a region-sized box `(0, 0, 256, 256)`
enumerates every parcel. The bounds must stay inside the region — OpenSim
drops the request outright if `end > regionSize`, with no error to the client.
`SessionConfig.region_size_meters` (default 256) sizes the box; varregions
need it raised.

### 2. The event queue was dormant

This was the real root cause. `_run_caps_prelude` polled `EventQueueGet`
exactly once and then never again, so *every* EQG-only message was dropped on
the floor for the entire session.

Added `_run_event_queue_loop`: a background task started after the prelude,
cancelled at session teardown, that polls in a loop and acks each batch by id
(which is how the simulator knows it may drop delivered events). Long-poll
timeouts on a quiet queue are the normal idle shape and are now recorded as
`eventqueue.poll_timeout` rather than as errors; a 404 after logout is the
capability being torn down and is no longer reported at all. New knobs:
`SessionConfig.event_queue_polling` and `event_queue_timeout_seconds`.

### 3. `ParcelProperties` is an EVENT QUEUE message — the earlier note was backwards

`LLClientView.SendLandProperties` builds an EQG event via `eq.StartEvent(
"ParcelProperties", ...)` and returns early if no event queue exists. There is
**no `ParcelPropertiesPacket` send path anywhere in `LLClientView.cs`.**

Consequences worth internalising:

- `udp.messages.parse_parcel_properties`, written 2026-06-22, **never fires
  against OpenSim.** It is not wrong, just unreachable on this server. Keep it
  for SL/other-server compatibility, but do not expect it to run.
- The UDP `ParcelProperties` template entry is marked `UDPDeprecated`, and that
  turns out to be literally true on OpenSim.
- The request goes out over UDP; the reply comes back over HTTP. Neither
  transport tells the whole story on its own — which is exactly why the
  earlier single-transport reasoning went wrong in both directions.

Added `EVENT_PARCEL_PROPERTIES` / `ParcelPropertiesEvent` to
`event_queue/events.py`, decoding into the **same** `ParcelPropertiesMessage`
the UDP parser produces, so `ParcelPropertiesReceived` and every downstream
consumer are unchanged regardless of transport.

### 4. Consumer side

`LiveCircuitSession.handle_event_queue_batch` folds a decoded batch into
session state (parcel replies also land in `parcel_properties_by_local_id`,
since a region-wide request draws one per parcel). `viewer3d`'s `Scene` gained
`apply_parcel_properties`, wired in `_wire_scene`, preferring the parcel whose
Bitmap actually covers the avatar and falling back to the first reply while the
avatar position is unknown.

### One Live Bug Found And Fixed

`_as_uuid` raised on `''`. OpenSim writes an unset UUID as an empty `<uuid/>`,
which parses to the empty string rather than the all-zero form — hit
immediately on `GroupID` for an ungrouped parcel, and it killed the decode of
both parcel events. Blank now yields the null UUID; genuinely malformed values
still raise.

### What Was Verified Live

- `parcel.properties local_id=1 name='Your Parcel' area=65536
  owner=6571e388-…` — two replies, one from OpenSim's own login-time land info
  and one from our request.
- `decode_parcel_bitmap` on the live 512-byte Bitmap: 4096 cells, edge 64,
  bounds `(0,0,63,63)`, `contains_meters(128,128)=True`. Consistent with the
  overlay grid's 128 perimeter segments and with `area=65536` — a single
  region-wide parcel, three independent decoders agreeing.
- A headless `Scene` driven through the real `_wire_scene` renders
  `Parcel: Your Parcel`.
- 630 tests pass; no new lint findings.

## Update 2026-08-14: Parcel Borders (2D)

`Scene` now accumulates the sequenced `ParcelOverlay` packets and calls
`decode_parcel_overlay` after each piece, keeping `parcel_overlay` and
`parcel_borders` once the set is whole. Retrying per-packet rather than waiting
for an expected count means a late or reordered packet still completes the grid.
State clears on region change alongside the map tile.

The 2D top-down path draws the segments (`_draw_parcel_borders` in
`viewer3d/render.py`, between the region border and the entity markers), gated
on `Scene.render_parcel_borders`. That closes the bird's-eye
"`ParcelOverlay` → plot-edge polylines" item that had been open since the
original plan.

Verified live: 4 packets → grid decoded → **128 border segments** in a `Scene`
driven through the real `_wire_scene`, which for this single region-wide parcel
is exactly the region perimeter. 634 tests pass.

### 3D Borders Too — And A Note On "Needs Visual Verification"

Landed in the same pass. `perspective.py` gained a line VAO for
`scene.parcel_borders` reusing the terrain-line program, with endpoints lifted
onto the heightmap (or the flat ground plane before terrain arrives) plus a
0.35 m offset so lines follow the land instead of z-fighting it. The VAO
rebuilds only when the segments or terrain revision change.

**Worth knowing for every future "this needs the GL viewer" item in this repo:
`moderngl.create_standalone_context()` works on this machine** (NVIDIA GT 1030,
GL 3.3). So GL work can be verified headlessly by rendering into an offscreen
framebuffer and reading pixels back — no display, no manual look-and-see. The
new tests in `test_viewer3d_perspective_gl.py` render the region perimeter with
and without borders, assert pixels changed, and assert the brightest changed
pixel is green-dominant. That is a real check that geometry rasterized, not
just that a VAO was allocated.

This applies directly to the still-open mesh-normals item below, which was
deferred for exactly this reason.

### Mesh Normals Landed Too

With headless GL available, the deferred mesh-normals item was closed in the
same pass. Every VAO bound to the shape program now supplies interleaved
`"3f 3f"` position+normal. Primitive shapes and sculpt meshes bake the old
`normalize(in_pos)` approximation into their buffer (so their lighting is
byte-identical to before), while decoded mesh assets pass `decoded.normals`
through.

One test-design trap worth recording: the first version of the verification
compared a mesh **with** a `Normal` array against one **without**, and they
shaded identically — because `decode_sl_mesh_asset` *computes* normals from the
triangles when the asset omits them. The meaningful contrast is authored-vs-
computed, so the test now authors normals pointing +X on a triangle lying in
the XY plane (which the decoder would otherwise compute as +Z) and asserts the
frames differ.

### Per-Face Materials And The Event Queue, Consumed

Both remaining "decoded but dropped" items from the June pass are closed.

**`material_groups`.** Mesh index buffers now split along the decoded material
groups, and each slice draws with that prim face's `TextureEntry` override —
the treatment cube faces already had. Per-face buffers share the parent VBO;
only IBOs and VAOs are per face, and they rebind when the instance buffer
grows. Single-group meshes keep the one-draw-call path. The GL test paints a
two-submesh mesh red on face 0 and blue on face 1 and asserts both appear;
**both the structural and the pixel assertions were checked to fail when the
feature is stubbed out**, so neither passes vacuously. That check is cheap and
worth repeating for any future pixel-level test — a render test that cannot
fail is worse than no test.

**The event queue.** Typed EQG events now publish as `EventQueueEventReceived`,
one carrier rather than one bus event per message, because the queue's
vocabulary is open-ended — `UnknownEvent` publishes too, so a consumer can
handle something this client does not decode yet. `viewer3d` reports the two
that mean something to a person: `TeleportFinish` and `ScriptRunningReply`.

### Concrete Next Step

**Live-verify object sync**, the oldest open track (implemented 2026-05-25,
never confirmed against a running sim). Everything it needed now exists:
`ScriptRunningReply` arrives over the live queue and surfaces in viewer chat,
so the server-side "script compiled OK" confirmation is finally observable.
Select a scripted object in `./run.sh tester viewer3d`, Save Text, edit the
`.lsl`, upload, and watch the chat line.

### Mesh UVs (2026-08-14)

Also closed. Mesh vertex buffers carry `in_mesh_uv` and a `u_use_mesh_uv`
uniform selects authored coordinates per draw; primitives and sculpts keep the
generated fallback.

This forced a decoder change worth knowing about. `DecodedSLMesh.uvs` is
zero-filled when a submesh omits `TexCoord0`, so an authored `(0, 0)` was
indistinguishable from a missing array — and defaulting to the authored path
would sample an untextured mesh at a single texel, visibly *worse* than the
fallback. `DecodedSLMesh.has_authored_uvs` now records the difference, true
only when every submesh carried its own array.

That is the same trap as the normals work: **this decoder fills in defaults for
absent data, so "the field is populated" never means "the asset supplied it."**
Check for a dedicated flag before treating decoded geometry data as authored.

A GL-testing note: the first version of the test pinned UVs to exactly 1.0 and
the triangle rendered magenta rather than blue — at the texture edge,
wrap-around linear filtering blends the last texel with the first. Sample away
from the seam.

## Update 2026-08-14: ExtraParams Beyond Sculpt/Mesh

`vibestorm/world/extra_params.py` decodes the remaining prim feature blocks:
flexi (`0x10`), light (`0x20`), projector (`0x40`) and reflection probe
(`0x90`). Layouts from OpenSim `PrimitiveBaseShape`.

Two quantisations are easy to get wrong and are covered by dedicated tests:

- **Flexi softness is a 2-bit level split across the TOP bit of two different
  bytes**, whose low 7 bits carry tension and drag. Read the bytes naively and
  you get both a wrong softness and a wrong tension.
- **Light intensity rides in the colour's alpha channel**, so `LightParams.color`
  is RGB only. Treating alpha as opacity loses the intensity entirely.

Truncated blocks return `None` rather than raising — a malformed block should
cost one prim feature, not the whole object update — and `in_use=False` blocks
are skipped, since that is how a sim *clears* a feature.

Wired to a consumer in the same change, per the lesson from the June backlog:
`SceneEntity.extra_params` carries the decoded blocks and the Object Inspector
renders one row per block a prim actually has.

### What Was Verified Live

Two prims in the test region carry flexi blocks and decode to a plausible
configuration (softness 2, tension 1.0, drag 2.0, gravity 0.3, wind 0). The
region contains no light, projector or reflection-probe prims, so **those three
paths remain synthetic-test-only** — rez one of each to confirm them.

Render materials (`0x80`) and mesh flags (`0x70`) followed. Render materials
is the only variable-length block — a count byte then `(face_index, UUID)`
pairs — and OpenSim rejects the whole list when the declared count does not fit
the data rather than reading a partial one. This mirrors that: a half-applied
material set would be worse than none. Mesh flags returns `None` only for a
short block, so an explicit `0` stays distinguishable from absent, and the
aggregate filters on `is not None` rather than truthiness for the same reason.

## Update 2026-08-14: 32x32 LandExtended Terrain Patches

Varregions send terrain as 32x32 patches, which `decompress_patch` rejected
outright — the dequantize table, zig-zag copy matrix, cosine table and IDCT
were all hard-coded to 16. The algorithms were already generic in libomv; only
this port had baked in the edge. They are size-parameterized now, with tables
built once per size and cached. `DEQUANTIZE_TABLE16` / `COPY_MATRIX16` /
`COSINE_TABLE16` / `idct_patch16` remain as the 16-sized bindings, so nothing
that used them had to change.

`RegionHeightmap` also grows to fit. It was fixed at 256x256 and raised on any
patch landing outside — but a varregion is larger and its size is not known
until patches start arriving. Growth re-lays existing rows at the new stride,
capped at 2048 m so a malformed patch coordinate cannot allocate unbounded
memory.

**Unverified against a real varregion.** The test region is a standard 256 m
sim, so the 32x32 path is exercised only by synthetic bitstreams built with
`BitPackWriter`. The 16x16 path was live re-checked (14 LayerData messages, no
errors) to confirm the generalization did not regress it. Standing up a
varregion would confirm the rest.

## Update 2026-08-14: Object-Sync Dry Run (Read-Only)

The object script/notecard sync path has been implemented since 2026-05-25 and
never confirmed against a running sim, because confirming it means uploading
into in-world objects. A dry run verifies everything up to that point without
writing anything, and all of it works:

1. **`RequestTaskInventory` + xfer assembly** — 22 objects queried, every one
   returned a snapshot with a real `task_id`. Two carry a script:
   `'New Script'`, `asset_type='lsltext'`, with concrete `item_id`s
   (`8b2c2787-…` on task `43c98748-…`, `eb8743e4-…` on task `1ae29d6b-…`).
2. **Asset-type mapping** — task inventory reports `asset_type` as a *template
   string* (`'lsltext'`), not the integer the matcher and upload caps expect.
   `_asset_type_string_to_int` bridges it, mapping `'lsltext'` → 10. Anything
   consuming task inventory outside the HUD has to do the same conversion.
3. **Update capabilities** — `UpdateScriptTaskInventory`,
   `UpdateNotecardTaskInventory` and `UpdateScriptTask` all resolve against
   local OpenSim, so the upload half has somewhere to POST.
4. **The file matcher** — `_match_files_to_task_selections` pairs
   `New Script.lsl` with the live inventory row, reports `Unrelated.lsl` as
   skipped, and correctly ignores a non-uploadable `notes.png` rather than
   listing it as unmatched.

So the untested surface is now narrow: the two-step CAP POST itself
(`_request_uploader_sync` → upload bytes) and whether the sim recompiles the
script afterwards. Everything that feeds it is confirmed against live data.

Whoever runs the real verify: object local_id `234346577` or `234346578` each
hold one script, and their `task_id`s are above.

### Remaining

Nothing enumerated is now blocked on decoding. What is left is verification
against world content this test region does not contain:

- rez a light / projector / reflection-probe prim to confirm those three
  `ExtraParams` decoders live (only flexi has been seen)
- stand up a varregion to confirm 32x32 terrain live
- an object with a multi-submesh mesh and per-face materials would exercise the
  `material_groups` path against real assets rather than synthetic ones

## 2026-08-14 — SL prim face numbering, and the shape block compressed updates dropped

Two findings, the second much larger than the first and found only because the
first prompted a live census.

### TextureEntry faces did not match SL's face numbering

Per-face texturing existed only for cubes, and it split `CUBE_INDICES` by the
order `meshes.cube_mesh` happens to author faces in (-Z, +Z, +Y, -Y, +X, -X).
SL numbers box faces 0=+X, 1=+Y, 2=-X, 3=-Y, 4=top, 5=bottom. Every per-face
texture on a box therefore landed on the wrong side — a silent failure, since
the prim still rendered.

`meshes.shape_face_indices()` now returns the SL face → triangle index map for
the multi-face built-in prims, derived from SL's rule of walking the profile's
side segments first and appending the caps last (top before bottom). The
renderer's `_render_cube_faces` generalised into `_render_prim_faces` over
`_prim_face_meshes`, so cylinders (0=side, 1=top, 2=bottom) and prisms get
per-face textures too. Single-face prims — sphere, torus, sculpt — now read the
face 0 override rather than `TextureEntry`'s default; `texture_for_face` falls
back to the default anyway, so that only ever adds information.

The prism side-quad order is **derived, not observed**. The caps are confident;
which of the three side quads is SL's face 0 has never been checked against a
textured in-world prism, and a wrong guess rotates the three side textures
among themselves. `prism_face_indices` says so in its docstring.

Verified by offscreen GL readback aiming the camera at each face in turn, and
mutation-checked both ways (cube map reverted to slot order, cylinder map
rotated) to confirm the tests discriminate. One trap worth remembering: a
camera placed *directly* above its target has a view direction parallel to the
`(0, 0, 1)` up vector, which degenerates the view matrix and renders solid
black. The cap tests use `(1.5, 0, ±6)`.

### 30 of 32 live prims had no shape data at all

The census run to check whether the region contained any non-cube prims with
face overrides answered a different question instead: 30 of 32 prims reported
`shape=None`, so nearly every prim in the region was rendering as the fallback
cube regardless of its actual profile.

Cause: `decode_compressed_object_data` walked past the 23-byte path/profile
block with a bare `pos += 23` and never parsed it. `ObjectUpdateCompressed`
carries the bulk of object traffic in a populated region, so "compressed" meant
"shapeless".

The subtlety that makes this worth a separate parser: **the compressed block
does not use the message template's field order.** `ObjectUpdate` puts
ProfileCurve second, right after PathCurve; `LLClientView.CreateCompressedUpdateBlockZC`
writes the entire path group first and the profile group last. Reusing
`_parse_prim_shape_block` here raises nothing — it silently reports a profile
curve read out of the middle of `PathBegin`. Hence
`_parse_compressed_prim_shape_block`, plus a test whose fixture is authored so
the two readings disagree.

Live before: 30 unclassified, 2 cubes. Live after: 22 cubes, 5 spheres, 3 tori,
1 tube, 1 unclassified. The spheres and tori were always there.

### Still unobserved

The test region contains **zero** prims carrying per-face `TextureEntry`
overrides, so the face-map work is unit- and pixel-verified but not live-
verified. A prim with different textures on two faces would close that — and a
textured prism would settle the side-quad order.

## 2026-08-14 — hover text, wire to pixels

Prim floating text was recorded as a byte count and nothing else, in both
update paths; `ObjectUpdateCompressed` did not even do that, it stepped over
the field. Both paths now decode the string, and it carries through
`WorldObject` -> `SceneEntity` to an Object Inspector row *and* to a
camera-facing billboard in the 3D view.

**The colour is the trap.** OpenSim writes `argb ^ 0xff000000`
(`LLUDPZeroEncoder.AddColorArgb`), so fully opaque text goes out with alpha
byte `0`. Reading the byte as-is makes every ordinary hover text look
completely transparent — the feature would appear to work right up until
nothing rendered. `_decode_text_color` inverts it and tests pin both ends.

The billboard is scaled by eye distance so the text keeps a constant apparent
size, the way SL does it. Two consequences worth knowing:

- Moving the test camera closer does **not** make the label bigger. The GL
  tests need their own 256x256 framebuffer; at the file's shared 64x64 target
  a label is about 2 px tall and every glyph pixel is partial coverage, so the
  colour test can never pass.
- `depth_mask` lives on the bound framebuffer in moderngl 5, not on the
  context. Depth *test* stays on (text behind a wall is hidden); depth
  *writes* are off (overlapping labels blend instead of punching holes).

Live end-to-end: logged in, built a `Scene` from the live `WorldView`, aimed a
camera at local_id `234346578` and rendered offscreen. The magenta
`'hover text'` label appears above its prim — 96 pixels matching
rgba(255, 0, 255, 255), which is exactly the reading an uninverted alpha
decode would have reported as invisible. The same frame shows spheres and a
cylinder rendering as spheres and a cylinder, which is the compressed shape
fix visible in the same shot.

## 2026-08-14 — avatar names were in the data all along

`ObjectUpdate` NameValue pairs have been parsed into `WorldObject.name_values`
since early on, and nothing ever read them. Avatars never receive an
`ObjectPropertiesFamily`, so those pairs are the *only* place an avatar's name
arrives — which meant every avatar was anonymous in the inspector and
unlabelled in the 3D view while the data sat one attribute away.

A live census confirmed the shape before building on it:
`{'FirstName': 'Vibestorm', 'LastName': 'Tester', 'Title': ''}`.

`scene.avatar_display_name()` joins first and last, drops a `Resident` last
name (SL's placeholder for a single-name account, which viewers hide), and
puts a non-empty group `Title` on the line above. The empty-string `Title`
OpenSim sends for an untitled avatar must not become a blank first row on the
tag — that one has a test.

The hover-text billboard generalised into `_render_labels`, which walks both
sources: prim hover text brings its own colour, avatar tags get
`AVATAR_NAME_COLOR`. `render_hover_text` and `render_avatar_names` stay
independently switchable, and a test asserts turning one off does not silence
the other.

Live: rendered the region offscreen — "Vibestorm Tester" draws above the
avatar, the magenta hover-text prim draws beside it, and the spheres visible
in the same frame are the compressed-shape fix.

## 2026-08-14 — the sweep for content-gated code, and what it found

Chasing "is there any prim in the region with a per-face texture?" turned up
something better: **the Object Inspector crashed on exactly that prim.**
`TextureEntry.face_texture_ids` is a tuple of `(face, uuid)` pairs and the
inspector called `.items()` on it. Selecting any prim with a face override
would have raised `AttributeError` and taken the detail panel down. It never
fired because no prim in the region has one — the same absence that leaves the
new SL face maps live-unverified.

That is a *class* of bug, not one bug: branches that only execute when world
content the sim lacks comes into view. `test/test_rare_content_paths.py` now
covers the combination — one synthetic prim carrying per-face overrides,
flexi, light, projector, reflection probe, GLTF render materials, mesh flags,
hover text and a media URL, walked from the compressed wire blob through
`WorldView`, `Scene`, the inspector, and a real GL render.

Building that fixture pinned down a layout fact worth keeping: **the
compressed block's ExtraParams header is 6 bytes** (type u16, size u32) with
no in-use byte. The 7-byte in-use form is the `ObjectUpdate` /
`ObjectExtraParams` variant. `parse_shape_extra_params` tries both, so it
tolerates either, but `decode_compressed_object_data`'s own cursor walk
assumes 6 — the fixture assumed 7 and silently misaligned everything after it,
dropping the shape block.

Two decoding gaps closed alongside:

- **Attached sound.** Both paths stepped over the 25-byte sound group. Note
  they do not share a layout: the compressed block writes UUID/gain/flags/
  radius contiguously, while the full `ObjectUpdate` tail puts OwnerID
  *between* the sound UUID and its gain. A null sound UUID reports as `None`,
  since that is how a sim clears a looping sound. No prim in the region has
  one, so this is synthetic-only for now.
- **Permission masks.** The inspector printed five masks as raw hex. Bit
  values come from OpenSim's `PermissionMask` (`Framework/Util.cs`), not from
  memory: Transfer `1<<13`, Modify `1<<14`, Copy `1<<15`, Export `1<<16`, Move
  `1<<19`, and `All` deliberately excludes Export. Folded permissions (low
  nibble) are a *different* encoding of the same rights and stay separate —
  folding them in would claim a copy right the object does not have.
  Unrecognised bits survive as `unknown_bits`. Live across 32 objects x 5
  masks: every bit resolved, none unknown.

**Deliberately not done:** decoding `update_flags` into named prim flags.
`PrimFlags` lives in libomv, not in `opensim-source/`, and everything else
this session came from reading `LLClientView.cs` or `PrimitiveBaseShape.cs`
directly. Reconstructing a 32-bit flag table from memory would have produced
something plausible and unverifiable. It stays open until a libomv source is
available.

Also confirmed live and no longer a gap: **prim names**. 32 of 33 objects
carry an `ObjectPropertiesFamily` name; the one that does not is the avatar,
which now resolves from NameValues.

## 2026-08-14 — `./run.sh census`

The question "does this region contain a prim with X?" drives almost every
verification decision in this project: a decoder is only live-verified if the
sim produces content that reaches it. It got answered by a throwaway script
five separate times in one session, with results that could not be compared
between runs. It is now a command.

    ./run.sh census [--duration 30]

Reports object/avatar counts, the shape histogram, named vs unnamed, each
tracked feature with example `local_id`s, and every permission mask grouped by
what it grants. The load-bearing line is **`census absent=`** — a feature with
no example is named explicitly rather than left as a silent zero, because a
silent zero reads as "fine" and is how a decoder stays unverified for months.

Shape classification deliberately follows the *renderer's* precedence: a
sculpt/mesh hint beats the path/profile curves. So the histogram reports what
would actually be drawn, not what the curves alone imply. That is why the
census says 8 spheres where a curves-only script says 5 spheres and 3 tori —
those three are sculpts, and the renderer draws them via the sculpt path.

Current reading of the test region:

    census objects=32 avatars=1
    census shape[cube]=22 sphere=8 tube=1 unclassified=1
    census feature[hover text]=1
    census feature[flexi]=2
    census feature[sculpt or mesh]=3
    census absent=media url, attached sound, per-face texture, light,
                  projector, reflection probe, render materials, mesh flags
    census perms_unknown=none

That `absent=` list is the standing answer to "what content would let us
verify the rest", and it should be re-run rather than reasoned about after
anyone changes the region.

### The census immediately earned itself

The first report had one prim in an `unclassified` bucket. "Unclassified" on
its own is not actionable, so the census now prints the curves behind it — and
it said `census unclassified[path=0x80 profile=0x01]=1`, which named the bug
outright.

`classify_prim_shape` only knew path curves 0x10 (straight) and 0x20/0x30 (the
two circular extrusions). OpenSim's `Extrusion` enum
(`PrimitiveBaseShape.cs`) is Straight=0x10, Curve1=0x20, Curve2=0x30,
**Flexible=0x80**. Flexible is a *path mode*, not a shape of its own: a flexi
prim is a straight extrusion that bends at runtime, and the flexi ExtraParams
block carries the bending. So every flexi prim was falling through to the
unclassified fallback and rendering as a default cube regardless of its
cross-section — harmless for a flexi box, wrong for a flexi cylinder.

0x80 now joins the linear branch, with a test guarding the obvious wrong fix
of sending it to the circular branch instead. After it, the region reports no
unclassified prims and 23 cubes rather than 22.

The general lesson: a diagnostic that reports a *category* of ignorance
("unclassified", "unknown", "absent") should always carry the raw value that
produced it. The bucket says something is wrong; only the value says what.

### And then the `tube=1` bucket

The same report showed one tube. `_SHAPE_ALIASES` mapped tube to the *cube*
mesh and ring to the *torus*, so a square-section tube drew as a box and a
triangle-section ring as a round donut.

Both are swept cross-sections around a circular path, differing from the torus
only in the shape of that cross-section, so `meshes._swept_ring_mesh` is now
the shared body behind all three. One detail worth not re-discovering: the
tube's 4-gon profile is **phased 45 degrees**. A bare 4-side sweep puts profile
vertices at 0/90/180/270 and produces a diamond section, which reads as a lumpy
torus rather than a tube; a test pins the square section so the phase cannot
regress silently.

Tube and ring are single-face prims in SL, so they joined sphere, torus and
sculpts in reading the face 0 `TextureEntry` override rather than the entry
default. `_SHAPE_ALIASES` is now down to one entry: `mesh` still stands in as a
sphere until authored mesh assets are fetched and decoded.

Live-verified by rendering the region offscreen aimed at local_id 234346573 —
it draws as a hollow square-section ring.

## 2026-08-14 — the census hiding a zero in its own report

Reading the census output rather than just running it turned up two more
things.

**"sculpt or mesh=3" answered the wrong question.** Sculpts and authored
meshes ride the same ExtraParams block but fetch through different
capabilities — GetTexture for a sculpt map, GetMesh for a mesh asset. A count
that merges them does not say which pipeline a region exercises. The census now
names the kind (`sculpt:sphere`, `sculpt:torus`, `sculpt:plane`,
`sculpt:cylinder`, `mesh`) and prints the asset id per prim.

**And merging them hid a zero.** Three sculpts and no meshes produced a
non-zero total, so the GetMesh pipeline's complete absence of live coverage
never reached the `absent=` line — the exact silent zero the report exists to
prevent, inside the report itself. They are separate tracked features now, and
`absent=` correctly ends with `mesh asset`.

That immediately explained a session log. All three sculpt prims share asset
`be293869-d0d9-0a69-5989-ad27f1946fd4`, and a verbose session shows:

    event=texture.fetch.error id=be293869-d0d9-0a69-5989-ad27f1946fd4
        error=GetTexture ... failed: HTTP 404

So the sculpt render path cannot be verified here because **the sculpt map is
missing from the sim's asset store**, not because of anything client-side. And
`mesh.get_mesh_url_ready mesh fetch deferred until mesh object seen` is GetMesh
correctly idling — there is no mesh object in the region to fetch for.

Both are content problems, now visible rather than inferred. Re-uploading that
sculpt map would light up the sculpt path; a rezzed mesh object would light up
GetMesh and, with per-face materials, the `material_groups` path too.

## 2026-08-14 — the sound/animation events finally have a consumer

Six bus events (`AvatarAnimationReceived`, `ObjectAnimationReceived`,
`SoundTriggered`, `AttachedSoundReceived`, `AttachedSoundGainChanged`,
`PreloadSoundReceived`) were published from the session and subscribed by
nothing. The Scene now tracks them and the Object Inspector shows what an
object is doing *now* next to how it was *built* — the `ObjectUpdate` sound
block and the live AttachedSound can legitimately disagree, since a script can
swap either at runtime.

The modelling decision worth keeping: these are **current state, not a log**. A
new `AvatarAnimation` or `AttachedSound` replaces what was there, because that
is exactly how a sim stops an animation or swaps a sound. A trailing log would
show a stopped animation forever. Two consequences, both tested:

- a null sound id **clears** the entry rather than storing a zero UUID —
  otherwise "silent" and "playing asset 0" are indistinguishable
- an `AttachedSoundGainChange` for an object with no known sound is **ignored**,
  since inventing an entry from it would claim a sound id never seen

`SoundTrigger` is the exception. One-shot sounds have no lasting state, so they
go to a bounded tail (`SOUND_TRIGGER_HISTORY`) rather than a dict that grows
without limit in a busy region.

State is keyed by full UUID, which is how these messages address objects; a
local id only exists inside one region session.

Nothing in the test region emits any of them, so unit tests are the only
coverage until a sound emitter or animated object is rezzed — and `./run.sh
census` reports `attached sound` in its `absent=` list, so that stays visible.

## 2026-08-14 — every primitive had the wrong normals

Noticed by looking at a live render rather than a test: the avatar drew as a
smooth vertical plank instead of a figure. Its scale was correct (0.45 x 0.6 x
1.9 m, a real SL avatar bounding box), so the problem was shading.

`_interleave_vertex_attributes` falls back to `normalize(position)` when a mesh
supplies no normals — and **no built-in primitive supplied any**. That fallback
is exact for a sphere centred on the origin and wrong for everything else:

- a torus/tube/ring vertex on the inner wall of the hole got a normal pointing
  outward from the *world origin*, so the hole lit up backwards
- cylinder cap vertices shared a normal with the round side
- box corners got radial diagonals instead of flat face normals
- and the avatar placeholder's seven boxes sit *away* from the origin, so every
  part shaded into one gradient — the plank

Fixes, all in `meshes.py` behind `shape_normals()`:

- boxes and the prism are emitted **flat shaded**: a vertex per face corner
  rather than shared corners, because a shared corner can carry only one normal
- the cylinder splits its cap rings from its side rings for the same reason
- swept surfaces get analytic normals pointing away from the **centre of the
  tube** (the nearest point on the ring circle), not away from the origin

Index emission order is unchanged throughout, so the SL face maps still slice
correctly. A pleasant side effect: the flat cube normals now *independently*
confirm the face mapping derived earlier from OpenSim's profile-then-caps rule —
face 0 comes out (1,0,0), face 1 (0,1,0), face 2 (-1,0,0), face 3 (0,-1,0),
face 4 (0,0,1), face 5 (0,0,-1).

### The test lesson

Mesh-level tests proved `shape_normals()` was right. They did **not** prove the
renderer used it: deleting the pass-through left every one of them green. Two
GL tests now cover that, one per upload site — `_prim_face_meshes` (a cube face)
and `_shape_meshes` (the avatar's torso face) — each asserting the face shades
uniformly rather than as a gradient. Both were mutation-checked.

Worth remembering that the first mutation check *appeared* to pass because it
patched the wrong upload site. There are **three**, and each needed its own
test:

| site | shapes | normals from |
| --- | --- | --- |
| `_prim_face_meshes` | cube, cylinder, prism | `meshes.shape_normals()` |
| `_shape_meshes` (built-ins) | sphere, torus, tube, ring, avatar | `meshes.shape_normals()` |
| `_shape_meshes` (sculpt) | decoded sculpt maps | `smooth_vertex_normals()` |

(A fourth, `_mesh_face_meshes` for authored SL mesh assets, was already passing
`decoded.normals` correctly.)

Sculpts kept the position fallback a commit longer than the rest. A sculpt map
carries only positions, and only the *sphere* sculpt type is a surface centred
on the origin — torus, plane and cylinder sculpts were all lit wrongly. The
mesh decoder already computed smooth per-vertex normals for a submesh with no
`Normal` array, so that is now extracted as
`sl_mesh.smooth_vertex_normals(positions, indices)` and shared, rather than
having two implementations drift apart.

One detail there that looks like an oversight and is not: the triangle cross
product is accumulated **unnormalized**, so each face contributes in proportion
to its area. A sliver should not sway a shared vertex as much as a large
neighbouring face. There is a test pinning that.

## 2026-08-14 — TextureAnim, and a bug the "does not disturb" test caught

Texture animation was another byte count and nothing else. It is how a prim
scrolls, rotates, scales or flipbook-animates its texture — most of what makes
a region look alive.

Sourced, not reconstructed: `SceneObjectPart.AddTextureAnimation` writes the 16
bytes (Flags u8, Face s8, SizeX/SizeY u8, Start/Length/Rate f32) and the mode
bits are the LSL constants `ANIM_ON`..`SCALE` in `LSL_Constants.cs`.

Three details worth not rediscovering:

- **Face is signed.** `-1` means every face; reading it unsigned turns "all
  faces" into face 255.
- **"Off" is an empty block**, not 16 bytes with a cleared flag — absent and
  off are the same wire state.
- Under `ROTATE`/`SCALE` the SizeX/SizeY grid is unused, so `describe()`
  reports angles rather than printing a misleading `0x0` grid.

### The bug

Adding this broke the TextureEntry read, and the test that caught it was the
one asserting the new field "does not disturb" the old one — worth writing
every time a field is appended to a cursor-walked blob.

`decode_compressed_object_data` back-computed the TextureEntry start as
`pos - texture_entry_size`. That was correct only while nothing was parsed
after it. Appending the TextureAnim advance moved `pos` further on, so the
TextureEntry was then read ten bytes early and every prim's default texture
became garbage. The start offset is captured at read time now.

The compressed flag bit for TextureAnim (`0x0040`) is **inferred** from the gap
between `HAS_PARENT` (0x0020) and `HAS_ANG_VEL` (0x0080), not sourced. It is a
safe thing to be wrong about: OpenSim writes TextureAnim last, so no later
field depends on that cursor. Flagged here in case someone later finds the real
value.

`texture animation` joins the census `absent=` list — the test region has none.

## 2026-08-14 — SimStats: two enums, and why the live check mattered twice

Every session receives 41 region-health numbers — frame rate, physics time,
prim and script counts — decoded correctly into `(stat_id, value)` pairs and
then discarded: `SimStatSnapshot` kept only `len(message.stats)`. The snapshot
now stores named stats, and `format_world_status` prints a `world[sim_health]`
line.

Names come from `src/vibestorm/world/sim_stats.py`, transcribed from
`opensim-source/OpenSim/Framework/SimStats.cs`.

### Two enums, only one of which is on the wire

`SimStats.cs` defines **both** `StatsIndex` and `StatsID`, and they are easy to
confuse:

- `StatsIndex` numbers the slots of the sim's internal `float[]`.
- `StatsID` is the wire id. `LLClientView.SendSimStats` writes
  `SimStats.StatsIndexID[i]`, an array mapping slot → wire id.

The first version of the table was keyed on `StatsIndex`. The two enums are
**identical for ids 0-3** and diverge from 4 onward, so the mistake survives a
casual read: `StatsIndex` 4 is `Agents`, wire id 4 is `FrameMS`. The result is
not a crash or a missing value — it is a full status line of plausible numbers
under the wrong labels. `test_table_is_not_keyed_on_the_internal_array_index`
asserts the divergence point directly rather than trusting the 0-3 overlap.

Two further details worth keeping: the extension stats sit at **1000+**, not
packed just past the viewer range, and `UnAckedBytes` (24) is divided by 1024
by the writer — it is the only rescaled stat, hence the name `unacked kb`.

### The live run caught a second, quieter bug

After the table was fixed the status line *still* read
`sim fps=0 physics fps=0 time dilation=0 agents=0 …`. Every label correct,
every value zero — which reads exactly like an idle region rather than a bug.

`summarize_sim_stats` was re-naming stats that had already been named. It used
`getattr(entry, "stat_value", 0.0)`, and `NamedSimStat` carries `.value`, not
`.stat_value` — so the default silently supplied 0.0 for all 41. The defaults
are gone; the namer now takes raw entries only and raises on anything else, and
a test pins that feeding named stats back through it raises rather than
returning zeros.

Both bugs produced output that looked entirely reasonable. Neither would have
been caught by the unit tests alone — only by comparing against a running sim.
The confirmed live read: time dilation 1.0, sim fps 55.1, agents 1, total prims
32 (matching the census object count), active scripts 2, frame ms 18.16, ids
arriving out of numeric order exactly as `StatsIndexID` predicts. All 41 ids
resolved — no `world[sim_stats_unknown]` line.

## 2026-08-14 — the sourcing boundary, stated once

Three separate features have now been declined for the same reason, so it is
worth naming the pattern instead of rediscovering it:

**OpenSim's own enums live in `OpenSim/Framework/` and are sourceable. The
bitfields the viewer sees are libomv's (`OpenMetaverse.*`), and libomv is not
in `opensim-source/`.** OpenSim's call sites use those names freely, so a
grep finds `RegionFlags.AllowDamage` or `ChatSourceType.Agent` and it *looks*
sourced — but only the name is there, never the numeric value.

Declined on these grounds so far: `PrimFlags` (object `update_flags`), the
particle system block layout, and `ChatSourceType` / `ChatAudibleLevel`.

**Partially recanted for region flags** — see the parcel/region flags entry
below. `LSL_Constants.cs` turned out to define a sourced *subset* of both the
region and parcel flag words, which is enough to name those bits and report the
rest as unknown. Before declining anything on this basis, check LSL_Constants:
it exposes whatever a script can query, which is a surprising amount.

`RegionFlags` deserves a specific warning: `OpenSim/Framework/RegionFlags.cs`
**does** exist and defines an enum with that exact name — but it is the *grid
service's* region-record flags (DefaultRegion, FallbackRegion, Hyperlink), not
the region flags in `RegionHandshake`. Same trap as StatsIndex/StatsID: a real
file, the right name, the wrong enum.

## 2026-08-14 — chat had a type byte nobody read

`ChatFromSimulator` carries a chat type that reached the CLI as `type=1` and
the viewer not at all. `src/vibestorm/world/chat_types.py` names it from
`OpenSim/Framework/ChatTypeEnum.cs`.

The byte does three unrelated jobs, and conflating them was an actual bug:
types 4 and 5 are **start/stop typing**, which arrive as ChatFromSimulator
packets with an empty message. `Scene.apply_chat_local` appended every event
unconditionally, so each one became a blank row in the chat log. They now feed
a `typing_senders` indicator instead, cleared by stop-typing *or* by the sender
actually saying something (a sim does not reliably send stop-typing first).
Whisper and shout are shown as qualifiers; an ordinary say is left unmarked.

Type 3 is a dead second encoding of Say. It is deliberately left unnamed, so
that a sim sending it would show up as `unknown type 3` rather than being
folded silently into "say".

Live-verified by sending whisper/say/shout from a headless session and reading
the sim's echo back: types 0/1/2, `audible=1`, correct names. The typing path
is **not** live-verified — this client never sends typing notifications and the
test region has no second avatar to produce them.

`sourcetype` and `audible` stay raw ints; see the sourcing-boundary note above.

## 2026-08-14 — physics properties: decoded, now consumed, still unverifiable

`ObjectPhysicsProperties` was fully decoded by `event_queue/events.py` into
`ObjectPhysicsPropertiesEvent` — and nothing anywhere consumed it. Same shape
as the SimStats gap: correct decode, no destination.

`src/vibestorm/world/physics_shape.py` names the shape type from OpenSim's
`PhysShapeType` (`Framework/ExtraPhysicsData.cs`: prim 0, none 1, convex 2,
invalid 255). `Scene.object_physics` records it per prim and the inspector
shows it. Two deliberate choices:

- Material values equal to OpenSim's defaults (density 1000, friction 0.6,
  restitution 0.5, gravity 1.0 — `SceneObjectPart` field initialisers, checked
  against source) are **not** printed. Every prim carries these numbers;
  echoing them back implies someone chose them.
- `shape=none` also prints "no collision shape" on its own row. It is the one
  value with a visible in-world consequence: the prim is walked through.
- Keyed by **local id**, unlike the sound and animation rows beside it, which
  key by full UUID. `ObjectPhysicsProperties` addresses prims by local id. A
  test pins this, since getting it wrong would attach another prim's physics.

### Why this cannot be live-verified right now

`SceneGraph` calls `SendPartPhysicsProprieties` only from `UpdateExtraPhysics`
and `PrimMaterial` — that is, **only as an echo of an edit the viewer itself
just made**. The sim never sends it unprompted. A 25-second passive session
confirms this from the other side: the event queue delivered **no events at
all**, of any kind.

So reaching this code live requires modifying a prim in the region, which is
the same consent gate as the object-sync verify. Unit-tested and mutation-
checked, live-unverified, and it will stay that way until someone authorises
an in-world edit.

## 2026-08-14 — material and click action were integers on every prim

Both bytes arrive in every `ObjectUpdate` on every object, and both reached
the inspector raw: "Material: 3", "Click Action: 0". `world/prim_attributes.py`
names them from `LSL_Constants.cs` (`PRIM_MATERIAL_*`, `CLICK_ACTION_*`) — the
same source as the texture-animation modes.

The name is shown *with* the number (`metal (1)`), not instead of it. This is a
protocol client; a reader comparing the inspector against a packet dump needs
the byte.

`CLICK_ACTION_NONE` and `CLICK_ACTION_TOUCH` are **both 0**. Zero is named
"touch", because touch is what an unconfigured prim does — "none" would suggest
clicking does nothing.

The census now counts both, which is what makes the live check meaningful.

### What the live run actually proved, and what it did not

    census material[wood]=32
    census click_action[touch]=32

Every prim resolved, no unknown values — but every prim is also the *default*
(`SceneObjectPart.m_material` initialises to Wood; touch is the default click
action). So the tables are live-verified for exactly one value each. stone,
metal, glass, flesh, plastic, rubber, light, and sit/buy/pay/open/play/zoom are
unexercised, and will stay so until the region contains a prim that uses them.
That is a content gap, added to the standing list.

### A test caught a regression I introduced

Replacing the panel's defensive `getattr(w, "material", ...)` with direct
attribute access broke the TextureAnim inspector test, whose `_World` stub is
deliberately partial. The panel's contract is that it walks whatever it is
handed — every field in it uses `getattr` for that reason. Restored.

## 2026-08-14 — parcel and region flags, and a correction to the note above

**The sourcing-boundary note earlier in this file overstated the case for
region flags.** It said the viewer-facing `RegionFlags` bitfield is libomv's
and therefore unsourceable. That is true of the *complete* enum, but OpenSim's
`LSL_Constants.cs` defines nine `REGION_FLAG_*` and sixteen `PARCEL_FLAG_*`
constants with real numeric values — `llGetRegionFlags` and `llGetParcelFlags`
return exactly those words. A partial, sourced table is worth having.

`src/vibestorm/world/land_flags.py` decodes both, using the same contract as
`world/permissions.py`: name what is sourced, and report everything else
through `unknown_bits` rather than dropping it. `parcel_flags` reaches the
scene and the HUD diagnostics panel; `region_flags` is now carried on
`RegionInfo` (it previously existed only inside a debug detail string) and
printed by `format_world_status`.

A test asserts the two tables are **not** interchangeable: `0x40` is "allow
create objects" for a parcel and "block terraform" for a region, so using the
wrong table gives a wrong answer that still looks like a real flag name.

### Live results — both halves worth reading

    world[region_flags]=0x14108026 allow direct teleport, unknown 0x14008026

    parcel flags=0x2800204b allow fly, allow scripts, allow landmark,
                            allow create objects, allow all object entry,
                            unknown 0x20002000

Parcel decoding is genuinely exercised: five of the sixteen names appear on a
real reply. Region decoding resolves exactly **one** of nine — OpenSim's
`GetRegionFlags` sets a good many bits (AllowLandmark, AllowSetHome,
ExternallyVisible, AllowVoice, and others) whose numeric values LSL never
exposes, so they land in `unknown_bits`. That large unknown mask is the correct
output, not a defect, and it is exactly what the earlier note was right to be
cautious about — the fix is to report the gap, not to guess the bits.

## 2026-08-14 — the state byte, and a swap that hides in plain sight

Every prim carries a `state` byte the client kept raw. It means two different
things: for a tree or grass prim it is the species, and for an **attachment**
it is the attachment point — **nibble-swapped**. `LLClientView` does it twice,
in the full update and the terse one:

    int st = 0xff & (int)part.ParentGroup.AttachmentPoint;
    state = (byte)((st >> 4) | (st << 4));

This is the nastiest failure mode seen this session, worse than the
StatsIndex/StatsID mix-up, because the wrong answer is *in range*: attachment
point 1 (chest) arrives as `0x10` = 16, and 16 is itself a valid point (right
eye). Skipping the swap does not corrupt anything or raise — it silently
reports a different body part. A test asserts both halves: that `0x10` decodes
to chest, and that the raw 16 would have read as "right eye".

`world/attachments.py` decodes it, names all 55 points from `ATTACH_*` in
`LSL_Constants.cs`, and flags the eight HUD slots (31-38) separately since
those are screen-space and visible only to the wearer.

Crucially, the state byte alone cannot tell you a prim *is* an attachment — a
tree has a non-zero state too. OpenSim marks an attachment's root with the
`AttachItemID` NameValue, which the client already decoded, so that is the
test. `describe_attachment` returns None for anything else.

Points 29/30 are worth knowing about: `ATTACH_RPEC`/`ATTACH_LPEC` and
`ATTACH_LEFT_PEC`/`ATTACH_RIGHT_PEC` share those two values with the sides
**swapped** — an upstream SL bug (SVC-580) kept for compatibility. Named by
the newer, correctly-sided constants.

Live: `census absent=… attachment`. The test region has no attachments — the
tester avatar wears nothing — so this is unit-tested only. Rezzing anything
onto the avatar would exercise it, and the census now asks the question every
run.

## 2026-08-14 — sound flags, and the near-miss on sourcing them

The inspector showed `flags 0x00`. `world/sound_flags.py` names the byte:
LOOP, SYNC_MASTER, SYNC_SLAVE, SYNC_PENDING, QUEUE, STOP.

**The obvious source was the wrong one.** After the LSL_Constants win with
parcel flags, the natural next move was to reach for `SOUND_*` in
`LSL_Constants.cs` — PLAY 0, LOOP 1, TRIGGER 2, SYNC 4. Those are
`llLinkPlaySound` *parameters*, not the wire byte. The real enum is OpenSim's
own `SoundFlags` in `CoreModules/World/Sound/SoundModule.cs`, and the two
disagree on every value above 1: wire 2 is SYNC_MASTER, not TRIGGER; wire 4 is
SYNC_SLAVE, not SYNC. A test asserts exactly that, because the LSL-sourced
version would have produced confident, wrong, plausible names.

So the lesson from the parcel-flags entry needs a qualifier: LSL_Constants is
worth checking, but it describes *the scripting API*, which is not always the
wire. Confirm against the code that writes the bytes.

One behavioural consequence: `AttachedSoundState.is_silent` now also checks the
STOP bit. A stop message still names a sound, so "sound_id set" was being read
as "playing" when the sim had just told it to go quiet.

Live: unverified. No prim in the test region has a sound, and `census absent=`
has listed `attached sound` all session.

## 2026-08-14 — region-scoped state that outlived its region

Found by asking a maintenance question rather than a protocol one: *which of
the Scene's state is region-scoped, and who clears it?*

`Scene.apply_region_changed` already cleared entities, textures, parcel name,
overlay and map tile — with an explicit comment about not showing a stale tile
from the old region. But every **per-object side dict** survived the change:

    object_physics        attached_sounds      object_animations
    avatar_animations     recent_sound_triggers  typing_senders
    parcel_flags          sim_health

The entity dicts look after themselves — `refresh_from_world_view` rebuilds
them every frame from the WorldView, which is per-circuit and therefore
naturally region-scoped. These do not: they accumulate from bus events and
nothing pruned them.

`object_physics` is the one that is actually *wrong* rather than merely stale.
It is keyed by **local_id**, and local ids are assigned per region session — so
object 42 in the new region would silently inherit object 42's physics from the
region just left. The UUID-keyed dicts are less dangerous (a UUID is global) but
still wrong: an object left behind keeps a phantom looping sound forever.

`typing_senders` and `parcel_flags` were mine, added earlier this session;
`parcel_name` was already being cleared right beside `parcel_flags`, which made
the omission easy to see once the question was asked.

Each cleared field is mutation-checked individually, so the test cannot pass on
a partial fix.

**Worth repeating as a habit:** every time state is added to a long-lived
object, ask what its scope is and who resets it. Four of these eight fields
were added this session without that question being asked.

## 2026-08-14 — a GL texture leak in the label cache

The same "what is this scoped to, and who frees it?" question, asked of the
renderer's caches this time. `PerspectiveRenderer._hover_text_textures` is
keyed by **the text itself**, and had no eviction — only a teardown release.

So the cache is bounded by *distinct labels seen over the session*, not by the
prim count. A static "For Sale" sign is one texture forever, which is why this
never showed up in testing. But hover text is what scripts use for clocks,
visitor counters, vendor prices and status boards: a prim rewriting its text
once a second mints one GL texture per second and frees none until the
renderer is torn down.

Now an LRU capped at `HOVER_TEXT_CACHE_MAX = 64`, releasing on eviction.

The test uses a stub context rather than real GL, so the eviction policy is
covered without needing a GL machine. Three mutations, three distinct
failures:

- no eviction at all → 4 failures
- evicts but never calls `release()` → 2 failures
- no LRU touch on a cache hit → 1 failure

The middle one matters most: dropping the reference without `release()` looks
completely correct from Python — the dict shrinks, memory usage in the process
looks fine — while the texture stays allocated on the GPU. Only an explicit
assertion that evicted textures were *released* catches it.

## 2026-08-14 — the object texture cache, and two tests that lied

Same sweep, next cache. `_object_textures` is keyed by texture UUID and does
correctly release on re-upload when a path changes. It is also deliberately
**not** cleared on a region change — texture files persist on disk, so
revisiting a region reuses what is already uploaded — and that is exactly why
it needs a bound: it is the one GL cache nothing clears mid-session. Capped
LRU at 256, which is 256 real textures, not glyph bitmaps.

The interesting part was the tests, both of which passed while testing
nothing:

**1. The test called the evictor directly.** `_upload()` poked
`_object_textures` and then called `_evict_object_textures()` itself. Deleting
the evictor's call site from the real upload path still passed. This is the
same mistake as the normals work earlier in the session (patching one of three
upload sites and believing the green run) — a test that reaches past the code
path it is meant to cover. Fixed by going through `_upload_object_texture`
with a real PNG in a temp dir.

**2. The LRU test asserted the wrong thing.** It checked that a frequently
reused texture was still *present* after churn. Without an LRU touch, that
texture is evicted and then immediately re-uploaded on its next request — so
it is present at the end either way. Presence was never the property worth
asserting; the **upload count** was. It now asserts exactly `churn + 1`
uploads.

Both are the same underlying error: asserting a state that the bug also
produces. Worth checking for whenever a mutation "passes".

Final mutation matrix, all four failing distinctly:

| mutation | result |
|---|---|
| no eviction on upload | 2 failures |
| evict without `release()` | 1 failure |
| paths dict not evicted in lockstep | 1 failure |
| no LRU touch on cache hit | 1 failure |

## 2026-08-14 — the LRU caps were the wrong fix; reference pruning replaces them

**This supersedes the two cache entries above.** The leaks they describe were
real, but the fix — a least-recently-used count cap — was wrong, and would have
been much worse than the problem.

Both `_upload_object_texture` and `_hover_text_texture` are called **inside the
per-frame draw loop**, once per visible textured face group and once per label.
Neither the number of visible textures nor the number of visible labels is
capped anywhere. So the moment a region holds more than the cap, an LRU evicts
textures that are *still on screen*, and they are re-decoded from PNG and
re-uploaded to GL **every frame, forever**. Trading a slow memory leak for a
permanent per-frame stall is a bad trade, and 256 visible textures is an
ordinary region, not an extreme one.

All three caches now prune by **reference** instead:

- `_prune_object_textures(scene)` — keeps whatever is in `scene.texture_paths`
- `_prune_label_textures(active_texts)` — keeps the labels drawn this frame
- `_prune_mesh_assets(scene)` — keeps whatever is in `scene.mesh_paths`

Nothing in use can be released, by construction: the live set *is* what the
draw loop is able to ask for. The first two run once per frame; the freeing
happens naturally when `apply_region_changed` clears those scene dicts.

`_prune_mesh_assets` is new ground — I had claimed in the previous entry that
the mesh caches "don't warrant the same treatment" because they are keyed by
shape key. That was wrong: a mesh asset's shape key embeds its asset UUID, so
they accumulate per distinct mesh exactly like textures do, and a decoded
mesh's vertex and index buffers are bigger than a texture. It only takes care
of asset-derived keys — the built-in prim meshes share `_shape_meshes` and
releasing one would break every prim of that shape, which a test now pins.

Five mutations, five distinct failures: no release on texture prune, paths dict
not pruned in lockstep, no release on label prune, mesh face buffers not freed,
and pruning against an empty live set.

**The lesson worth keeping:** when a cache is filled from inside a render loop,
a capacity bound is not a safety measure — it is a thrash generator. Bound
those caches by what is live, never by how many.

## 2026-08-14 — testing a cache across frames, and why one-frame tests can't

The pruning above is a **cross-frame** mechanism: frame N uploads, frame N+1
decides what is still live. Every existing GL test in
`test_viewer3d_perspective_gl.py` renders exactly one frame and tears the
renderer down afterwards, so none of them could reach the failure mode at all.
Added a `_multi_frame` helper that drives several frames through one renderer.

Two things fell out, both instructive.

**The teardown ran before the caller saw the renderer.** The helper returned
the renderer from inside a `try` whose `finally` called `clear_caches()` — so
the cache assertions saw an emptied cache and failed with `0 != 1`. Python
runs the `finally` before the return completes. The pixel assertions, which
are the ones that matter, had passed all along. Now the cache size is read
before teardown and returned as a value.

**Then the real lesson: asserting the render output cannot detect thrash.**
Mutating `_prune_label_textures` to release *everything* every frame — the
worst possible prune — left all three new GL tests passing. Pruning runs
before the draw loop, and the draw loop re-rasterises whatever is missing, so
the frame still comes out pixel-correct. It just re-uploads a texture per label
per frame forever, which is exactly the pathology this work exists to prevent.

The fix is to assert **identity**, not presence or appearance: for unchanged
text the cached texture object must be the *same object* across frames. With
that, the prune-everything mutation fails.

This is the third time this session that a mutation passing revealed a test
asserting a state the bug also produces — see the object-texture entry above.
When a mutation passes, suspect the assertion before concluding the code is
fine.

## 2026-08-14 — constructing the large region the test sim doesn't have

The cache rework left one thing unobserved: the failure an LRU cap causes
needs *more distinct visible textures than the cap*, and the test region has
32 prims sharing a handful of textures. That is a content gap — but unlike a
sound emitter or an attachment, it is one a test can construct.

`LargeRegionTextureGLTests` builds a scene with **300 distinct textures**, each
its own PNG on disk and its own UUID, on 300 spread-out prims, and renders two
frames through one renderer on real GL. It asserts identity across frames:
every texture object after frame 2 must be the *same object* as after frame 1.
A second test clears `texture_paths` and `object_entities` the way
`apply_region_changed` does, and asserts everything is released.

Reinstating the reverted LRU-cap design fails **three** tests. Removing texture
pruning entirely fails one. So this test would have caught the bad fix before
it was committed — twice.

Distinct files as well as distinct ids, deliberately: a wrong implementation
that keyed only on path would look correct if 300 ids shared one file.

The general point: "we have no content that exercises this" is sometimes a real
blocker (a sound emitter must exist in-world to send AttachedSound) and
sometimes just an unasked question. Scale, breadth, and volume are usually
constructible; only genuinely external behaviour is not.

## 2026-08-14 — the last two orphaned bus events get a consumer

`EnableSimulator` and `CrossedRegion` were decoded, published on the bus, and
consumed by **nothing** — the last such pair. The scene's own docstring claimed
they "reach consumers through the bus", which described an intention rather
than reality; that line is corrected.

They deserve opposite treatment, which is why one handler would have been
wrong:

- **`EnableSimulator`** fires once per neighbouring region and is re-announced,
  so it is recorded as state (`Scene.neighbour_regions`, handle -> "ip:port")
  and shown as a count in the diagnostics panel. Announcing each one would put
  eight alerts on screen on arriving in a region with eight neighbours.
- **`CrossedRegion`** is rare and means the avatar is now somewhere else, so it
  posts an alert line, like `TeleportFinish` does.

A real crossing needs a neighbour region and the test sim is standalone, so
this is not live-verified — but the *events* are constructible, so the scene's
handling of them was never actually blocked on that. Only the transport
behaviour is (`world_client`'s documented "EnableSimulator → child circuit,
CrossedRegion → promote child" remains unimplemented and genuinely does need a
neighbour to build against).

`neighbour_regions` is cleared in `apply_region_changed`, decided when the
field was added rather than found later — the habit from the region-scope fix
earlier today.

### A fourth way a mutation can lie

The "neighbours not cleared" mutation passed, and this time neither the code
nor the assertion was at fault: the mutation script's string replace **did not
match**, so it silently changed nothing and tested the unmodified code. Re-run
by line index with an `assert` on the target line, it fails correctly.

Ad-hoc mutation scripts must assert the pattern was found. A `.replace()` that
misses is indistinguishable from a test that holds.

## 2026-08-14 — a gap that had already been closed

`projectstate.md` listed "deeper object update families such as
`ObjectUpdateCached` and `KillObject`" as an open gap. `ObjectUpdateCached` is
not a gap and has not been one for a while: `session.py` already requests a
full update for every cached entry via `RequestMultipleObjects`, chunked at
255 ids.

Measured against the live sim rather than assumed:

- `ObjectUpdateCached` arrives **twice**, at ~5.7 s and ~6.3 s — at region
  entry, not on a repeating timer.
- 13 `ObjectUpdate` events yield **33 tracked objects**, so the cached-request
  path is what populates most of the region. It demonstrably works.

I had gone looking for a specific bug — the handler re-requests unconditionally
rather than only on genuine cache misses, so a sim that re-announced cached
objects periodically would make us re-request things we already hold. That is
a real inefficiency in principle and a non-issue in practice at two messages
per session. Not worth code; worth the measurement that says so.

`KillObject` does remove objects from the `WorldView`. It has no live exercise
because nothing in the test region is ever deleted.

Also removed the stray empty `tests/` directory. The suite is `test/`; the
plural one was an empty leftover and had been sitting there unexplained.

**Stale gap entries are their own hazard.** This one would have had someone
implement a feature that already existed. Worth re-reading the gap list
occasionally and checking the entries still describe reality.

## 2026-08-14 — local ids are never reused, so don't "fix" the kill-side leak

Chasing the within-region version of this morning's cross-region bug: a killed
object leaves entries behind in the scene's `local_id`-keyed dicts
(`object_physics`, `object_inventory_snapshots`), and if the sim reused that
local id, a newly rezzed prim would inherit a dead one's physics.

It does not. `SceneBase.AllocateLocalId` is:

    return (uint)Interlocked.Increment(ref m_lastAllocatedLocalId);

A monotonic counter with no free list — within a region session's lifetime, a
local id is never handed out twice. So those leftovers are a **bounded leak,
not a correctness bug**, and the leak is bounded by objects derezzed during
one session.

Deliberately no code. Pruning them would have to run somewhere, and the
obvious place — pruning per frame against the WorldView — risks dropping state
for an object that is momentarily absent, and `ObjectPhysicsProperties` is
never re-sent unprompted. A real risk in exchange for no real gain. Recorded
here so the next person weighing it can skip the investigation.

What the look *did* find was a coverage gap: `apply_kill_object` removes
`terse_objects` entries, and no test asserted it. That matters for a
terse-only object, where the terse record is the *only* record — leaving it
behind keeps a dead prim moving in the viewer. Now covered both ways, and the
mutation (dropping the `terse_objects.pop`) fails.

## 2026-08-14 — auditing the gap list, which had drifted into a changelog

Prompted by finding one stale entry: if the list said `ObjectUpdateCached` was
open when it had been done for a while, what else was wrong? Two more were:

- **"semantic decoding of terse object payloads beyond the first inferred
  `local_id`"** — stale. `parse_improved_terse_object_update` decodes state,
  is_avatar, the avatar-only collision plane, position, velocity,
  acceleration, rotation, angular velocity and the TextureEntry. Nothing is
  left inferred.
- **"(`EnableSimulator`, `CrossedRegion`) remain the only bus events with no
  consumer"** — contradicted by a *newer bullet in the same list* saying they
  now have one. The list had grown two entries that disagreed.

The underlying problem is structural rather than any single wrong line.
"Current Gaps" had become a changelog: most bullets describe finished work,
written in the same voice and tense as the open items. There is no way to tell
"this is missing" from "this was built" by reading a bullet.

Fixed by putting the genuinely open work at the top, grouped by **what would
unblock it** — region content, consent, sources, or nobody-has-written-it-yet —
and labelling everything below as landed work. The history is kept; it just no
longer masquerades as a to-do list.

That grouping is also the honest summary of where this stands: almost
everything still open needs something from outside the codebase, and the only
two purely-unimplemented items are region-crossing transport and the inventory
write surface.

**A stale gap entry costs as much as a bug and leaves no trace.** Two of them
sent me on investigations this session that ended in no code. Worth re-reading
the list against reality now and then, which is what this was.

## 2026-08-14 — inventory asset types, and live data that proves the distinction

`./run.sh inventory-walk` now names item types and reports which *gap-closing*
types the account lacks — the account-side counterpart of the census
`absent=` line.

Values are the `INVENTORY_*` LSL constants, and the care went into deciding
**which field they name**. The wire carries both `type` (asset type) and
`inv_type` (inventory type). These constants are the asset numbering, pinned
by `llGetInventoryType` returning `item.Type`. libomv's `InventoryType` table
is not in `opensim-source/`, so `inv_type` is deliberately left unnamed.

The live walk settles it beyond the source reading:

    type=5   inv_type=18   'Default Shirt'
    type=13  inv_type=18   'Default Eyes'
    type=24  inv_type=18   (Current Outfit links)

`Default Shirt` is asset type 5 (clothing) but inventory type 18 (wearable).
Naming `inv_type` with this table would have been wrong on nearly every item
in a real account, and the two enums diverge worst where it is least visible —
an animation is asset 20 / inventory 19, a gesture 21 / 20.

Type 24 (link) and type 2 (calling card) appear live and are correctly
reported as `unknown type N`: real asset types that LSL does not expose. Not
guessing them is the point.

### What the account actually holds

    inventory type[body part]=12  type[unknown type 24]=6  type[clothing]=4
    inventory absent=object, sound, animation, gesture

So the standing region-content gaps **cannot be closed from this account's
existing inventory** — there is nothing to rez or play. That is a concrete
answer to a question that had been open all session.

### The upload smoke test creates a mistyped item — diagnosed from source

The notecard left by `upload-empty-text-smoke` reads back as
`type=0 inv_type=0`. Asset type 0 is *texture*; a notecard should be 7.

I first filed this as needing another upload to investigate. It did not —
OpenSim's source answers it outright. `BunchOfCaps.UploadCompleteHandler`
opens with:

    sbyte assType = 0;
    sbyte inType = 0;

and then assigns them only inside `if (inventoryType == ...)` branches. The
complete set of branches is **sound, snapshot, animation, animset, wearable,
object**. There is no `notecard` branch, and no `else`. An unrecognised type
falls through and the item is created with both fields still 0 — while the
upload reports success and `FetchInventory2` confirms the asset matches, which
is exactly why the smoke test has always passed.

Notecards and scripts are not uploaded through this capability at all: a
viewer creates them with `CreateInventoryItem` and fills them in through
`UpdateNotecardAgentInventory` / `UpdateScriptAgent`.

`NEW_FILE_INVENTORY_TYPES` now records the supported set, with a test that
re-parses the branch list out of `BunchOfCaps.cs` so it cannot drift. The
smoke command prints the stored types and a warning rather than reporting a
clean success. Still no fix to the upload path itself — the correct flow is a
different pair of capabilities and building it means writing to the user's
inventory, which is theirs to authorise.

**The generalisable bit:** "I would have to perform a side effect to find out"
was wrong. The server's own source said what it does with the request. Reading
beats poking, and it needed no permission.

## 2026-08-14 — "verified" was hiding two very different claims

The upload smoke test turned out to verify something weaker than it appeared
to, which prompted the same audit on `spec/message-coverage.md` that the gap
list got: does each claim mean what a reader would take it to mean?

The status scale said `verified` meant "covered by fixtures, **tests, or** live
session evidence". Those are not the same strength of claim, and this session
produced three decoders that passed their unit tests and were wrong on live
data — SimStats keyed to the wrong one of two enums, the inventory walk raising
on the real payload shape, and a GL cache policy that was correct per-frame and
pathological across frames. A green suite is evidence about the code; only a
live run is evidence about the protocol.

`tested` and `verified` are now separate statuses, and where a row covers
several sub-decoders the status reflects the **weakest** one.

Three rows changed as a result:

- **`ObjectExtraParams`** claimed `verified` for seven sub-decoders. Two —
  sculpt and flexi — are live-confirmed. Light, projector, reflection probe,
  render materials and mesh flags have never seen live data and are all on the
  census `absent=` list. Now `tested`, with the split spelled out.
- **`LayerData`** was `verified` on the strength of the 16x16 path; the 32x32
  LandExtended half has never run against a sim. Said so.
- **`ChatFromSimulator` / `ChatFromViewer`** were *understated* — marked
  `handled` and "needs an in-world speaker to observe", which stopped being
  true this session when I sent whisper/say/shout and read the sim's echo.
  Both are now `verified`, with the typing notifications still `tested`.

`SimStats` had **no row at all**, despite arriving in every session since the
project started. Added.

Note that the drift ran in both directions: one row overclaimed, one
underclaimed, one message was missing. A ledger nobody re-derives goes stale
in whichever direction the last edit happened to leave it.

## Notes For The Next Agent

- All viewer-data protocol primitives live in `src/vibestorm/udp/messages.py`
  (encoders/parsers) and `src/vibestorm/caps/get_texture_client.py`.
- `src/vibestorm/assets/j2k.py` is the Pillow-backed decoder. Pillow is in
  the optional `viewer` extra (`uv sync --extra viewer`).
- The viewer dependency is `pygame-ce` rather than classic `pygame`; current
  `pygame_gui` imports APIs that classic `pygame` 2.6.1 does not expose.
- `local/map-cache/` is gitignored by the existing `local/` rule.
