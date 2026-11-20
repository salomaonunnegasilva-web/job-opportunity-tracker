import unittest

from tracker.presentation import ranked_jobs, render_digest


def job(job_id, title, location, *, active=True, url="https://example.org/jobs/1"):
    return {"job_id": job_id, "board": "sample", "employer": "Sample",
            "title": title, "location": location, "is_open": active, "url": url}


class RankingTests(unittest.TestCase):
    def test_sort_reasons_location_and_closed_postings(self):
        jobs = [
            job("2", "Data Engineer", "Remote (USA)"),
            job("1", "Data Engineer", "Remote — Brazil"),
            job("3", "Travel Agent", "LATAM"),
            job("4", "AI Engineer", "Remote", active=False),
        ]
        results = ranked_jobs({"jobs": jobs})
        self.assertEqual([row["job_id"] for row in results], ["1", "2", "3"])
        self.assertIn("Brazil listed +3", results[0]["reasons"])
        self.assertEqual(results[0]["score"], 8)
        self.assertEqual(results[1]["eligibility"], "Other listed location")
        self.assertEqual(results[2]["score"], 0)
        self.assertNotIn("score", jobs[0])

    def test_remote_is_unspecified_and_regexes_use_word_boundaries(self):
        jobs = [job("1", "Sales Agent · Fragment Developer · NoSQL", "Remote"),
                job("2", "Agentic ETL Developer, Python / SQL", "Latin America"),
                job("3", "AI Engineer", "Remote worldwide except Brazil")]
        results = {row["job_id"]: row for row in ranked_jobs({"jobs": jobs})}
        self.assertEqual(results["1"]["score"], 0)
        self.assertEqual(results["1"]["eligibility"], "Location unspecified")
        self.assertEqual(results["2"]["eligibility"], "LATAM listed")
        self.assertIn("ETL / ELT +4", results["2"]["reasons"])
        self.assertEqual(results["3"]["eligibility"], "Brazil excluded")


class DigestTests(unittest.TestCase):
    def test_escape_untrusted_text_and_restrict_schemes(self):
        unsafe = job("1", '<img src=x onerror="alert(1)">', "<script>oops</script>",
                     url="javascript:alert(1)")
        report = render_digest({"jobs": [unsafe], "sources": [], "events": []})
        self.assertIn("&lt;img", report)
        self.assertNotIn("javascript:", report)
        self.assertNotIn("<script>", report)
        self.assertIn("Open postings (1)", report)

    def test_failures_and_changes_are_visible_after_a_known_snapshot(self):
        source = {"board": "sample", "employer": "Sample", "last_success": "2026-11-19T12:00:00Z",
                  "last_attempt": "2026-11-20T12:00:00Z", "error": "503 <failed>"}
        event = {"board": "sample", "job_id": "1", "title": "Data Engineer", "kind": "reopened",
                 "at": "2026-11-19T12:00:00Z"}
        run = {"board": "sample", "employer": "Sample", "at": source["last_attempt"],
               "job_count": None, "error": source["error"]}
        report = render_digest({"jobs": [], "sources": [source], "events": [event], "runs": [run]})
        for text in ("Stale", "503 &lt;failed&gt;", "Nov 19, 2026", "Nov 20, 2026",
                     "Reopened", "Recent source runs", "Failed"):
            with self.subTest(expected=text):
                self.assertIn(text, report)


if __name__ == "__main__":
    unittest.main()
