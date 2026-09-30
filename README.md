# jev-screen

jev-screen is a local web application for title and abstract screening in systematic reviews. It ranks the retrieved records with Jev (TypeSafe AI, through the Vercel AI Gateway). You screen them in that order and stop when a statistical stopping criterion (Callaghan and Müller-Hansen, 2020) indicates that you have found at least 95% of the relevant records with 95% confidence.

The workflow is the one the accompanying paper recommends (citation to be added on publication). In that paper, ranking with Jev and stopping with the statistical criterion reached 95% recall in every review evaluated. Reviewers read 87.6% of the records on average in the 23 held-out SYNERGY reviews and 86.1% in the 28 CLEF 2019 reviews. Most of the saving comes in large reviews. In small reviews, the criterion needs almost every record before it can stop.

## Install and run

```bash
pipx install jev-screen          # or: pip install jev-screen
jev-screen serve                 # opens http://127.0.0.1:8765 in your browser
```

The server listens only on 127.0.0.1. It needs the session token in the printed address, so other users of a shared computer cannot open your projects. Each project is one SQLite file in `~/jev-screen-projects` (set `--dir` or `JEV_SCREEN_PROJECTS` to change it).

You need a Vercel AI Gateway API key with paid credits; Jev is not available on the free tier. Paste the key into the Score page, or set `AI_GATEWAY_API_KEY` before starting. The key stays in memory for the run and is never written to the project. Scoring costs about US$0.02 per 1,000 records at the list price at the time of the paper ($0.042 per million input tokens, about 455 tokens per record).

## Workflow

1. **New project.** Enter the review title, research question and eligibility criteria from your protocol, written before screening.
2. **Import.** Upload RIS, CSV, PubMed format (.nbib) or PubMed XML files, or paste PMIDs. jev-screen removes duplicates by DOI, PMID, or title and year. Records without an abstract, or whose title and abstract do not appear to be in English, go to a manual queue and are not sent to Jev. You can move a flagged record back before the ranking is frozen.
3. **Score.** jev-screen sends the records in seeded random order, ten per request, with the paper's prompt. The request bodies are byte-identical to those used in the paper. Requests are paced and retried with the server's `retry-after`. Scoring can be cancelled and resumed.
4. **Freeze the ranking.** The order is fixed: highest probability first, with ties broken by the project seed. Records that Jev did not score, including refusals, move to the manual queue.
5. **Screen.** You see one record at a time, in ranked order, and decide include, maybe or exclude (keys I, M, E, U to undo). The Jev probability is hidden by default. After each decision, the stopping panel shows the criterion's p value. "Maybe" counts as relevant, which is the conservative choice.
6. **Stop.** The stop button becomes available once p < 0.05. Screen the manual queue in full.
7. **Report.** The report tab gives PRISMA 2020 counts and a draft methods paragraph. You can export decisions (CSV), included and maybe records (RIS), and a reproducibility archive. The archive (ZIP) holds the project database, every request and response, the scores, the decisions and SHA-256 sums.

The command line offers the same steps: `jev-screen new | import | score | rank | status | export`.

## What to keep in mind

- **Do not use a probability threshold as a stopping rule.** The paper did not support the label-free threshold (τ = 0.07) as a standalone rule, and jev-screen does not offer one.
- **Model version.** Jev does not report a model version. jev-screen archives every request and response. The Score page offers a drift check that re-sends up to five archived requests unchanged and compares the probabilities with the stored ones.
- **Data.** The criteria and the titles and abstracts go to TypeSafe AI through Vercel, on servers in the United States. The provider does not offer zero data retention. Send only published or otherwise cleared records. Do not send unpublished, confidential or personal data.
- **Language.** Jev was evaluated on English records. The language flag is a heuristic based on the declared language, the script and the proportion of common English words. Check the manual queue before you freeze the ranking.
- **Order of input.** Do not sort exports by relevance. jev-screen randomises the order in which it sends records, but you should still export complete, unsorted result sets.
- **Reporting.** In the PRISMA flow diagram, report records not screened after stopping as excluded by the stopping rule, and name the rule. Describe the ranking method, the model alias, the dates, the stopping criterion and its parameters. The methods paragraph drafts this for you.

## Validation

- `validation/reproduce_paper.py` recomputes the stopping criterion at the 3,148 evaluation points of the paper's cross-check against buscarpy 0.0.2. The largest absolute difference was 3.4e-11. One decision differs from buscarpy, at a point where p equals 0.05 up to floating-point rounding; the paper's own implementation differs at the same point. Applied to the stored Jev probabilities, the port reproduces the paper's combined workflow exactly: 87.6% read with 23/23 reliable reviews (SYNERGY held out) and 86.1% with 28/28 (CLEF 2019). See `validation/reproduce_paper.log`.
- `tests/` runs without network access. The tests use the 70 records of SYNERGY review Theobald_2021 (CC0; titles and abstracts from OpenAlex, CC0), the Jev probabilities the paper stored for them, the paper's stopping points, and buscarpy values. The tests check the following:
  - The request bodies equal those of the paper's scorer.
  - The retry and authentication paths behave as intended.
  - The whole project flow runs from import to archive.
  - The web server refuses requests without the session token.
- `pytest -m network` also parses the same PubMed records exported as XML and as MEDLINE text.
- A live comparison of new Jev scores with the paper's stored scores (`validation/live_theobald.py`) has not been run yet. The gateway account used for development returned HTTP 403 for Jev on the free tier.

## Free trial proxy

`proxy/` contains an optional Vercel function that relays a limited number of requests on an operator's key, with users identified by GitHub. It is not deployed. See `proxy/README.md` for the conditions that must be met first.

## License

Apache License 2.0. The stopping criterion follows Callaghan MW, Müller-Hansen F. Statistical stopping criteria for automated screening in systematic reviews. Syst Rev. 2020;9:273.
