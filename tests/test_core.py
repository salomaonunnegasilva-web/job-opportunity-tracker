import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from tracker.core import read_state, refresh


def job(job_id, title="Data Engineer", location="Brazil"):
    return {"id": job_id, "title": title, "location": {"name": location},
            "absolute_url": f"https://example.org/jobs/{job_id}"}


def feed(*jobs):
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state" / "jobs.sqlite"
        self.boards = {"one": "Employer One"}

    def refresh_one(self, payload, at):
        return refresh(self.path, self.boards, fetch=lambda _: payload, now=at)

    def test_lifecycle_and_idempotency(self):
        self.refresh_one(feed(job(1), job(2)), "day1")
        self.refresh_one(feed(job(1), job(2)), "day2")
        updated = job(2, title="Senior Data Engineer")
        self.refresh_one(feed(updated, job(3)), "day3")
        self.refresh_one(feed(updated, job(3), job(1)), "day4")
        self.refresh_one(feed(updated, job(3), job(1)), "day5")
        state = read_state(self.path)
        expected = [("1", "baseline"), ("2", "baseline"), ("2", "changed"),
                    ("3", "new"), ("1", "closed"), ("1", "reopened")]
        self.assertEqual([(e["job_id"], e["kind"]) for e in reversed(state["events"])], expected)
        self.assertEqual(len(state["runs"]), 5)
        self.assertTrue(all(j["is_open"] for j in state["jobs"]))
        self.assertEqual(state["sources"][0]["last_success"], "day5")

    def test_stale_source_is_preserved_while_other_source_updates(self):
        boards = {"one": "One", "two": "Two"}
        refresh(self.path, boards, fetch=lambda _: feed(job(1)), now="day1")

        def flaky_source(board):
            if board == "one":
                raise TimeoutError("request timeout")
            return feed(job(1), job(2))

        result = refresh(self.path, boards, fetch=flaky_source, now="day2")
        self.assertEqual(result, {"one": "request timeout", "two": None})
        state = read_state(self.path)
        self.assertEqual(len(state["jobs"]), 3)
        self.assertTrue(all(j["is_open"] for j in state["jobs"]))
        one, two = state["sources"]
        self.assertEqual((one["last_attempt"], one["last_success"], one["error"]),
                         ("day2", "day1", "request timeout"))
        self.assertEqual((two["last_success"], two["error"]), ("day2", None))
        self.refresh_one(feed(job(1)), "day3")
        self.assertIsNone(read_state(self.path)["sources"][0]["error"])

    def test_upstream_edit_and_legacy_database_upgrade(self):
        self.refresh_one(feed(job(1)), "day1")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("ALTER TABLE jobs DROP COLUMN updated_at")

        self.refresh_one(feed({**job(1), "updated_at": "2026-11-01T12:00:00Z"}), "day2")
        self.refresh_one(feed({**job(1), "updated_at": "2026-11-01T09:00:00-03:00"}), "day3")
        self.refresh_one(feed(job(1)), "day4")
        updated = {**job(1), "updated_at": "2026-11-02T12:00:00Z"}
        self.refresh_one(feed(updated), "day5")
        self.refresh_one(feed(updated), "day6")
        state = read_state(self.path)
        self.assertEqual(state["jobs"][0]["updated_at"], "2026-11-02T12:00:00+00:00")
        self.assertEqual([(e["kind"], e["at"]) for e in state["events"]],
                         [("changed", "day5"), ("baseline", "day1")])

    def test_invalid_snapshots_do_not_create_or_close_jobs(self):
        self.refresh_one(feed(job(1)), "day1")
        for payload in [
            {},
            {"jobs": [], "meta": {"total": 1}},
            {"jobs": [], "meta": {"total": False}},
            feed(job(2), job(2)),
            feed(job(2), {"id": 3}),
            feed({**job(7), "absolute_url": "javascript:alert(1)"}),
            feed({**job(7), "updated_at": "yesterday"}),
        ]:
            with self.subTest(payload=payload):
                self.assertTrue(self.refresh_one(payload, "day2")["one"])
                state = read_state(self.path)
                self.assertEqual(len(state["events"]), 1)
                self.assertEqual([(j["job_id"], j["is_open"]) for j in state["jobs"]], [("1", 1)])
                self.assertEqual(state["sources"][0]["last_success"], "day1")

    def test_valid_zero_and_recovery_from_first_failure(self):
        refresh(self.path, self.boards, fetch=Mock(side_effect=OSError("offline")), now="day0")
        self.refresh_one(feed(), "day1")
        self.refresh_one(feed(job(1)), "day2")
        self.refresh_one(feed(), "day3")
        state = read_state(self.path)
        self.assertEqual([e["kind"] for e in state["events"]], ["closed", "new"])
        self.assertFalse(state["jobs"][0]["is_open"])
        self.assertEqual([r["job_count"] for r in state["runs"]], [0, 1, 0, None])


if __name__ == "__main__":
    unittest.main()
