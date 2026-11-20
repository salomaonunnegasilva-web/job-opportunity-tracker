import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tracker.__main__ import main
from tracker.output import atomic_text


def listing(job_id, title):
    return {"id": job_id, "title": title, "absolute_url": f"https://example.org/{job_id}",
            "location": {"name": "Remote, Brazil"}}


class StarterTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.db = root / "data" / "jobs.sqlite3"
        self.config = root / "boards.json"
        self.config.write_text(json.dumps({"demo": "Example"}), encoding="utf-8")
        self.digest = root / "site" / "index.html"
        self.labels = root / "data" / "labels.csv"

    def invoke(self, command, *args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main([command, "--db", str(self.db), *map(str, args)])

    def refresh(self, rows):
        with patch("tracker.core.fetch_board", return_value={"jobs": rows, "meta": {"total": len(rows)}}):
            return self.invoke("refresh", "--boards", self.config, "--output", self.digest)

    def export(self):
        return self.invoke("export-labels", "--output", self.labels)

    def test_labels_survive_reordering_closure_and_excel_edits(self):
        initial = [listing(1, "Data Engineer"), listing(2, "=Formula")]
        self.assertEqual(self.refresh(initial), 0)
        self.assertIn("Data Engineer", self.digest.read_text(encoding="utf-8"))
        self.assertEqual(self.export(), 0)
        with self.labels.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            columns = [*reader.fieldnames, "my_notes"]
        edited = next(row for row in rows if row["job_id"] == "2")
        self.assertEqual(edited["title"], "'=Formula")
        edited.update(would_apply="no", my_notes="São Paulo")
        with self.labels.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        self.assertEqual(self.refresh([initial[0], listing(3, "ML Engineer")]), 0)
        self.assertEqual(self.export(), 0)
        with self.labels.open(encoding="utf-8-sig", newline="") as stream:
            final = {row["job_id"]: row for row in csv.DictReader(stream)}
        self.assertEqual((final["2"]["would_apply"], final["2"]["my_notes"],
                          final["2"]["status"]), ("no", "São Paulo", "closed"))
        self.assertEqual(final["3"]["would_apply"], "")

    def test_failure_keeps_local_digest_and_prior_labels(self):
        self.assertEqual(self.refresh([listing(1, "Data Engineer")]), 0)
        with patch("tracker.core.fetch_board", side_effect=TimeoutError("test outage")):
            self.assertEqual(self.invoke("refresh", "--boards", self.config, "--output", self.digest), 1)
        digest = self.digest.read_text(encoding="utf-8")
        self.assertIn("Data Engineer", digest)
        self.assertIn("test outage", digest)
        self.labels.write_text("a different spreadsheet\n", encoding="utf-8")
        self.assertEqual(self.export(), 2)
        self.assertEqual(self.labels.read_text(encoding="utf-8"), "a different spreadsheet\n")

    def test_unfinished_write_preserves_previous_output(self):
        self.digest.parent.mkdir()
        self.digest.write_text("previous", encoding="utf-8")
        with self.assertRaises(ValueError):
            with atomic_text(self.digest) as stream:
                stream.write("incomplete")
                raise ValueError("simulated interruption")
        self.assertEqual(self.digest.read_text(encoding="utf-8"), "previous")
        self.assertEqual(list(self.digest.parent.iterdir()), [self.digest])


if __name__ == "__main__":
    unittest.main()
