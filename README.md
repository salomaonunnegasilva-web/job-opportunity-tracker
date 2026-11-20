# Job Opportunity Tracker

A daily, ranked digest of AI and data roles for someone searching from Brazil. The first two boards are Nimble Gravity and Turing. Python collects published job metadata from Greenhouse, SQLite records changes, and one HTML file shows the ranking and source health. The keyword scorer is the baseline for your first model.

## Run on Windows

Open PowerShell in this project folder. The starter uses Python 3.11 or newer and the standard library.

```powershell
py -3 -m tracker refresh
Start-Process .\site\index.html
py -3 -m tracker export-labels
```

The digest opens locally in your browser. A refresh needs internet access. Viewing a saved digest, exporting labels, and running the tests work offline. On macOS or Linux use `python3` in place of `py -3`.

| File | Purpose |
| --- | --- |
| `boards.json` | Maps each Greenhouse board token to an employer. Add a token and refresh to collect another board. |
| `data/jobs.sqlite3` | Stores job postings, source status, per-board refresh attempts, and listing changes. |
| `site/index.html` | The standalone digest, including scores, reasons, recent changes, and the last successful refresh of each board. |
| `data/labels.csv` | Your private labels. Re-exporting preserves decisions, custom columns, and previously exported roles that subsequently closed. |

The first successful snapshot establishes a baseline. A later complete snapshot can mark a posting new, changed, reopened, or closed. The stable key is the board token plus the Greenhouse posting ID. A closure means the posting disappeared from that board. Requests that fail validation preserve the previous snapshot. A complete response explicitly reporting zero jobs is valid. [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html#list-jobs)

## Your first model exercise

1. Open `data/labels.csv` in Excel. If everything appears in one column, use **Data → From Text/CSV** and choose the comma delimiter. Label 20 roles in `would_apply` with `yes` or `no`. Include some roles lower in the list and leave undecided rows blank. Save as comma-delimited CSV UTF-8 and close it before re-exporting.
2. Look at the top five in the digest. Write down one false positive and one role the rules ranked too low, if you find them. The title rules and location weights are in `tracker/presentation.py`. A generic “Remote” location leaves eligibility unspecified.
3. As more listings arrive, label a separate batch for evaluation. Train your model on the earlier labeled roles. Rank the same held-out jobs with both systems and count how many of the top five you marked `yes`. Keep each posting and its later revisions on the same side of the split. Use the title and listed location as model inputs and keep your decision and notes out of the features.

## Test and recover

```powershell
py -3 -m unittest discover -s tests -v
```

The tests cover repeat imports, changed and reopened postings, partial responses, a failed board alongside a healthy board, and label preservation. To see the recovery behavior in isolation, run the outage test:

```powershell
py -3 -m unittest discover -s tests -p test_core.py -k stale_source -v
```

If a live run fails, open the source status in the digest to see the failing board and the last successful fetch. Check the board token and network connection, then re-run the same refresh. A healthy board already processed can be fetched again safely. The digest timestamps are UTC.

| Exit code | Meaning |
| --- | --- |
| 0 | The command succeeded. |
| 1 | A source failed. The digest was written with the information available. |
| 2 | The command could not finish, for example because of a bad configuration, a locked output file, or a database error. |

## Activate daily deployment

1. When ready, publish **this project folder** as the root of a public GitHub repository. Keep `data/` and `site/` ignored. The tracking workflow creates its own hosted history from its first run.
2. Enable GitHub Actions and choose **Settings → Pages → Build and deployment → Source → GitHub Actions**. Repository policies must allow the workflow to write to the `tracker-state` branch and deploy Pages.
3. In **Actions → Daily job tracker**, choose **Run workflow** on the default branch. Open the Pages deployment link after it succeeds. The workflow also runs daily at **09:17 São Paulo time** (12:17 UTC).

Each run tests the code, restores the previous SQLite history from the `tracker-state` branch, collects both boards, saves the updated history, and publishes the digest. Only the HTML goes to Pages. The state branch contains public job metadata and run history. Git ignores your label CSV, and the workflow publishes only the digest. A failed source makes the workflow report a failure even when it successfully publishes the digest showing stale data. Use **Run workflow** for a fresh retry. [GitHub Pages workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
