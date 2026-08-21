"""Native LVM thin-pool status parsing and OBSI capacity policy."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import IntEnum


class PolicyLevel(IntEnum):
    OK = 0
    WARN = 1
    CRITICAL = 2
    DENY = 3
    FAILED = 4


@dataclass(frozen=True)
class ThinPoolStatus:
    vg_name: str
    pool_name: str
    data_percent: float
    metadata_percent: float
    size_bytes: int
    when_full: str
    lv_attr: str
    level: PolicyLevel
    reason: str

    @property
    def used_percent(self) -> float:
        return max(self.data_percent, self.metadata_percent)

    @property
    def can_start(self) -> bool:
        return self.level < PolicyLevel.DENY

    @property
    def used_bytes(self) -> int:
        return int(self.size_bytes * self.data_percent / 100)

    @property
    def free_bytes(self) -> int:
        return max(0, self.size_bytes - self.used_bytes)

    def as_json(self) -> dict:
        result = asdict(self)
        result["level"] = self.level.name.lower()
        result["used_percent"] = self.used_percent
        result["can_start"] = self.can_start
        result["used_bytes"] = self.used_bytes
        result["free_bytes"] = self.free_bytes
        return result


def evaluate_policy(
    data_percent: float,
    metadata_percent: float,
    *,
    warn_at: float = 85.0,
    critical_at: float = 92.0,
    deny_at: float = 96.0,
) -> tuple[PolicyLevel, str]:
    if not 0 <= data_percent <= 100 or not 0 <= metadata_percent <= 100:
        return PolicyLevel.FAILED, "invalid thin-pool usage values"
    if not 0 < warn_at < critical_at < deny_at <= 100:
        raise ValueError("thresholds must satisfy 0 < warn < critical < deny <= 100")

    highest = max(data_percent, metadata_percent)
    dimension = "metadata" if metadata_percent >= data_percent else "data"
    if highest >= deny_at:
        return PolicyLevel.DENY, f"{dimension} usage reached the allocation deny threshold"
    if highest >= critical_at:
        return PolicyLevel.CRITICAL, f"{dimension} usage is critical"
    if highest >= warn_at:
        return PolicyLevel.WARN, f"{dimension} usage is high"
    return PolicyLevel.OK, "thin pool is healthy"


def _number(value: object, field: str) -> float:
    try:
        return float(str(value).strip().lstrip("<>"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid LVM field {field}") from exc


def parse_pool_report(
    payload: str,
    *,
    expected_vg: str,
    expected_pool: str,
    warn_at: float = 85.0,
    critical_at: float = 92.0,
    deny_at: float = 96.0,
) -> ThinPoolStatus:
    try:
        reports = json.loads(payload)["report"]
        rows = [row for report in reports for row in report.get("lv", [])]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("invalid LVM JSON report") from exc

    matches = [
        row
        for row in rows
        if str(row.get("vg_name", "")).strip() == expected_vg
        and str(row.get("lv_name", "")).strip() == expected_pool
    ]
    if len(matches) != 1:
        raise ValueError("thin pool was not found exactly once")
    row = matches[0]
    data = _number(row.get("data_percent"), "data_percent")
    metadata = _number(row.get("metadata_percent"), "metadata_percent")
    size = int(_number(row.get("lv_size"), "lv_size"))
    level, reason = evaluate_policy(
        data,
        metadata,
        warn_at=warn_at,
        critical_at=critical_at,
        deny_at=deny_at,
    )
    when_full = str(row.get("whenfull", "")).strip() or "unknown"
    lv_attr = str(row.get("lv_attr", "")).strip()
    if len(lv_attr) < 9:
        level, reason = PolicyLevel.FAILED, "thin-pool health attributes are unavailable"
    elif lv_attr[4] in "cCX" or lv_attr[8] in "DMPX":
        level, reason = PolicyLevel.FAILED, f"thin-pool health flag is {lv_attr}"
    elif when_full != "error" and level == PolicyLevel.OK:
        level, reason = PolicyLevel.WARN, "thin-pool when-full policy is not error"
    return ThinPoolStatus(
        vg_name=expected_vg,
        pool_name=expected_pool,
        data_percent=data,
        metadata_percent=metadata,
        size_bytes=size,
        when_full=when_full,
        lv_attr=lv_attr,
        level=level,
        reason=reason,
    )


def parse_map_allocated(payload: str) -> int:
    try:
        entries = json.loads(payload)
        if not isinstance(entries, list):
            raise TypeError
        allocated = sum(
            int(entry["length"])
            for entry in entries
            if entry.get("data") is True and entry.get("zero") is not True
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid qemu-img map") from exc
    if allocated < 0:
        raise ValueError("qemu-img allocation cannot be negative")
    return allocated
