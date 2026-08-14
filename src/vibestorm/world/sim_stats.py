"""Names for the ``SimStats`` values a region reports every few seconds.

``SimStats`` arrives in every live session and carries 40-odd numbers about
region health — frame rate, physics time, script load, prim counts. The client
decoded the ``(stat_id, value)`` pairs correctly but then kept only their
*count*, so the whole picture was thrown away one step before it was useful.

Ids are OpenSim's ``StatsID`` enum (``OpenSim/Framework/SimStats.cs``), not
reconstructed. Note that file defines *two* enums, and only one of them belongs
here: ``StatsIndex`` numbers the slots of the sim's internal float array, while
``StatsID`` is what actually travels on the wire — ``LLClientView.SendSimStats``
writes ``StatsIndexID[i]``, which maps slot to wire id. The two agree for ids
0-3 and diverge from 4 onward, so keying off the wrong one produces labels that
are individually plausible and uniformly wrong (frame time reported as an agent
count, and so on).

Extension stats live at 1000+, deliberately far from the viewer-defined range.
They are named too: an unexplained id is worse than a long table.
"""

from __future__ import annotations

from dataclasses import dataclass

#: OpenSim's ``SimExtraCountStart``. Ids below this are viewer-defined; ids at
#: or above it are OpenSim's own extras, which a stock viewer never shows.
SIM_EXTRA_COUNT_START = 1000

SIM_STAT_NAMES: dict[int, str] = {
    0: "time dilation",
    1: "sim fps",
    2: "physics fps",
    3: "agent updates/s",
    4: "frame ms",
    5: "net ms",
    6: "other ms",
    7: "physics ms",
    8: "agent ms",
    9: "image ms",
    10: "script ms",
    11: "total prims",
    12: "active prims",
    13: "agents",
    14: "child agents",
    15: "active scripts",
    16: "script lines/s",
    17: "packets in/s",
    18: "packets out/s",
    19: "pending downloads",
    20: "pending uploads",
    21: "virtual size kb",
    22: "resident size kb",
    23: "pending local uploads",
    # Sent divided by 1024 by SendSimStats, so the unit is KB despite the name.
    24: "unacked kb",
    25: "physics pinned tasks",
    26: "physics lod tasks",
    27: "physics step ms",
    28: "physics shape ms",
    29: "physics other ms",
    30: "physics memory",
    31: "script events/s",
    32: "sim spare ms",
    33: "sim sleep ms",
    34: "io pump ms",
    35: "scripts run %",
    36: "region idle",
    37: "region idle possible",
    38: "ai step ms",
    39: "skipped silhouette/s",
    40: "skipped chars/s",
    # OpenSim extensions, placed far from the viewer-defined ids.
    1000: "internal script lines/s",
    1001: "frame dilation",
    1002: "users logging in",
    1003: "total geometric prims",
    1004: "total mesh prims",
    1005: "script engine threads",
    1006: "npcs",
}

#: The handful worth putting on one status line. Ordered for reading, not by id.
KEY_SIM_STAT_IDS: tuple[int, ...] = (1, 2, 0, 13, 11, 15, 4)


def sim_stat_name(stat_id: int) -> str:
    """Human name for a stat id, or a marked-unknown label.

    An unrecognised id keeps its number rather than being dropped: a sim
    reporting something this table does not know about is worth seeing.
    """
    name = SIM_STAT_NAMES.get(stat_id)
    return name if name is not None else f"unknown stat {stat_id}"


def is_viewer_stat(stat_id: int) -> bool:
    """True for ids a stock viewer understands, false for OpenSim extras."""
    return stat_id < SIM_EXTRA_COUNT_START


@dataclass(slots=True, frozen=True)
class NamedSimStat:
    stat_id: int
    name: str
    value: float

    @property
    def is_known(self) -> bool:
        return self.stat_id in SIM_STAT_NAMES

    def describe(self) -> str:
        # Integral counts read badly as "6.0 prims"; rates and times need the
        # fraction. Split on whether the value is actually integral.
        if float(self.value).is_integer():
            return f"{self.name}={int(self.value)}"
        return f"{self.name}={self.value:.2f}"


def name_sim_stats(entries: object) -> tuple[NamedSimStat, ...]:
    """Attach names to decoded ``SimStatEntry`` values.

    Reads ``stat_id``/``stat_value`` without a fallback default on purpose:
    passing an already-named entry here used to yield a full set of correctly
    labelled zeros, which reads as "the region is idle" rather than as a bug.
    An AttributeError is the better outcome.
    """
    return tuple(
        NamedSimStat(
            stat_id=int(entry.stat_id),
            name=sim_stat_name(int(entry.stat_id)),
            value=float(entry.stat_value),
        )
        for entry in entries or ()
    )


def summarize_sim_stats(
    stats: object,
    *,
    stat_ids: tuple[int, ...] = KEY_SIM_STAT_IDS,
) -> str:
    """One-line summary of the most telling stats, in ``stat_ids`` order.

    Takes already-named stats (``SimStatSnapshot.stats``), not raw entries.
    """
    by_id = {stat.stat_id: stat for stat in stats or ()}
    parts = [by_id[stat_id].describe() for stat_id in stat_ids if stat_id in by_id]
    return " ".join(parts)


__all__ = [
    "KEY_SIM_STAT_IDS",
    "SIM_EXTRA_COUNT_START",
    "SIM_STAT_NAMES",
    "NamedSimStat",
    "is_viewer_stat",
    "name_sim_stats",
    "sim_stat_name",
    "summarize_sim_stats",
]
