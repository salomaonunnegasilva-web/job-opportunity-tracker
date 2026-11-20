"""Write local reports and preserve the owner's labels between exports."""

import csv
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def atomic_text(path: Path, *, encoding="utf-8"):
    """Replace the destination only after the complete output is on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = tempfile.NamedTemporaryFile(
        mode="w", encoding=encoding, newline="", dir=path.parent, delete=False
    )
    temporary = Path(stream.name)
    try:
        with stream:
            yield stream
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


LABEL_FIELDS = (
    "board", "job_id", "employer", "title", "location", "url", "status",
    "baseline_rank", "baseline_score", "eligibility", "reasons", "would_apply",
)


def spreadsheet_text(value):
    """Keep source-provided text from being interpreted as an Excel formula."""
    value = str(value or "")
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_labels(path: Path, active_jobs, all_jobs) -> int:
    fields = list(LABEL_FIELDS)
    existing = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            if not {"board", "job_id", "would_apply"}.issubset(headers):
                raise ValueError(f"{path} needs board, job_id and would_apply columns")
            if len(set(headers)) != len(headers):
                raise ValueError(f"Duplicate column names in {path}")
            fields.extend(name for name in headers if name not in fields)
            for row in reader:
                key = row["board"], row["job_id"]
                if not all(key) or key in existing or None in row or None in row.values():
                    raise ValueError(f"Check row {reader.line_num} in {path} before re-exporting")
                existing[key] = row

    rows = []
    for rank, job in enumerate(active_jobs, start=1):
        key = job["board"], str(job["job_id"])
        row = existing.pop(key, {})
        row.update({name: spreadsheet_text(job[name]) for name in
                    ("board", "job_id", "employer", "title", "location", "url", "eligibility")})
        row.update(status="open", baseline_rank=rank, baseline_score=job["score"],
                   reasons=" | ".join(job["reasons"]))
        rows.append(row)

    known_closed = {(j["board"], str(j["job_id"])) for j in all_jobs if not j["is_open"]}
    for key, row in existing.items():
        row.update(status="closed" if key in known_closed else "untracked",
                   baseline_rank="", baseline_score="")
        rows.append(row)

    with atomic_text(path, encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
