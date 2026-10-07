# ReviewFast

ReviewFast is a free tool for title and abstract screening in systematic reviews. A decision model (Jev, TypeSafe AI, through the Vercel AI Gateway), a model that answers a yes-or-no question about each record with a probability instead of generating text, ranks the retrieved records against your eligibility criteria. You screen them in that order and stop when a statistical stopping criterion (Callaghan and Müller-Hansen, 2020) indicates that you have found at least 95% of the relevant records with 95% confidence.

- **Web version:** https://www.jolab.ai/reviewfast/ runs entirely in your browser. It includes a demo on a public review that needs no key.
- **Python package:** a local web app and a command line (`reviewfast`).
- **MCP server:** `reviewfast-mcp` lets an AI assistant run the steps while you make the screening decisions.

The workflow is the one the accompanying evaluation of a decision model for screening examined (manuscript in preparation; citation to be added). In that study, ranking with Jev followed by the statistical stopping criterion, a post hoc analysis, reached 95% recall in all 51 reviews evaluated (23 held-out SYNERGY reviews and 28 CLEF 2019 Cochrane reviews). Reviewers read 87.6% and 86.1% of the records on average. Most of the saving comes in large reviews; in small reviews the criterion needs almost every record before it can stop. The full workflow was evaluated with one decision model, Jev.

## Install and run (Python)

```bash
pip install "git+https://github.com/taehojo/reviewfast"
reviewfast serve                 # opens http://127.0.0.1:8765 in your browser
```

The server listens only on 127.0.0.1 and needs the session token in the printed address, so other users of a shared computer cannot open your projects. Each project is one SQLite file in `~/reviewfast-projects` (set `--dir` or `REVIEWFAST_PROJECTS` to change it).

You need a Vercel AI Gateway API key with paid credits; Jev is not available on the free tier. Paste the key into the Score page, or set `AI_GATEWAY_API_KEY` before starting. The key stays in memory for the run and is never written to the project. Scoring costs about US$0.02 per 1,000 records at the list price at the time of the evaluation ($0.042 per million input tokens, about 455 tokens per record).

The command line offers the same steps: `reviewfast new | import | score | rank | status | export`.

## MCP server

```bash
pip install "reviewfast[mcp] @ git+https://github.com/taehojo/reviewfast"
```

Add the server to your MCP client, for example Claude Desktop or Claude Code:

```json
{
  "mcpServers": {
    "reviewfast": {
      "command": "reviewfast-mcp",
      "env": { "AI_GATEWAY_API_KEY": "your key" }
    }
  }
}
```

Tools: `create_project`, `import_records`, `estimate_scoring_cost`, `score_records`, `freeze_ranking`, `next_record`, `record_decision`, `undo_last_decision`, `stop_screening`, `draw_audit_sample`, `export_results`, `project_status`, and `check_stopping`, which applies the criterion to any ranked screen without a project. The server instructions tell the assistant that inclusion decisions must come from the human reviewer, that the probability stays hidden unless asked, and that the data notice must be accepted before scoring.

## Workflow

1. **New project.** Enter the review title, research question and eligibility criteria from your protocol, written before screening.
2. **Import.** Upload RIS, CSV, PubMed format (.nbib) or PubMed XML files (the Python version also accepts pasted PMIDs). ReviewFast removes duplicates by DOI, PMID, or title and year. Records without an abstract, or whose title and abstract do not appear to be in English, go to a manual queue and are not sent to Jev.
3. **Score.** ReviewFast sends the records ten per request with the same prompt and request format as the evaluation. The evaluation batched records in data-file order; ReviewFast uses a seeded random order, which the evaluation did not test. Requests are paced and retried with the server's `retry-after`. Scoring can be cancelled and resumed.
4. **Freeze the ranking.** The order is fixed: highest probability first, with ties broken by the project seed. Records that the model did not score, including refusals, move to the manual queue.
5. **Screen.** You see one record at a time, in ranked order, and decide include, maybe or exclude (keys I, M, E, U to undo). The probability is hidden by default. After each decision, the stopping panel shows the criterion's p value. "Maybe" counts as relevant, which is the conservative choice.
6. **Stop.** The stop button becomes available once p < 0.05. Screen the manual queue in full.
7. **Check a random sample (optional, recommended).** Draw a random sample of the ranked records you did not screen and screen it in full. Decisions on the sample do not change the stopping statistics; relevant records found there are reported in the methods paragraph, as a warning to continue screening. Available in the web version, the MCP server and the Python library (`Project.draw_audit`); the local app does not show it yet.
8. **Report.** PRISMA 2020 counts, a draft methods paragraph, decisions (CSV), included and maybe records (RIS), and a project archive with every request, response, score and decision.

## What to keep in mind

- **Do not use a probability threshold as a stopping rule.** The evaluation did not support the label-free threshold (τ = 0.07) as a stand-alone rule, and ReviewFast does not offer one.
- **Model version.** Jev does not report a model version. ReviewFast archives every request and response, and offers a drift check that re-sends up to five archived requests unchanged and compares the probabilities.
- **Data.** The criteria and the titles and abstracts go to TypeSafe AI through Vercel, on servers in the United States. The provider does not offer zero data retention. Send only published or otherwise cleared records. The web version sends nothing to jolab.ai: the page is static, and its content security policy allows network requests only to the Vercel AI Gateway.
- **Language.** Jev was evaluated on English records. Check the manual queue before you freeze the ranking.
- **Order of input.** Do not sort exports by relevance; export complete, unsorted result sets.
- **Reporting.** In the PRISMA flow diagram, report records not screened after stopping as excluded by the stopping rule, and name the rule.

## Repository

| Path | Contents |
|---|---|
| `reviewfast/` | Python package: decision-model client, project file, stopping criterion, reports, local web app, CLI, MCP server |
| `site/` | Web version served at jolab.ai/reviewfast (plain HTML, CSS and JavaScript, no build step); `site/demo/` holds the demo review |
| `tests/` | Python tests (no network) |
| `site/test/` | JavaScript core tests (`node site/test/core.test.js`) and a browser test (Playwright, developer check) |
| `validation/` | Reproduction of the evaluation's stopping results from the stored probabilities |
| `proxy/` | Optional free-trial proxy (not deployed) |

## Validation

- `validation/reproduce_paper.py` reproduces the stopping results of the accompanying paper from the public per-record data in `validation/data/` (record identifiers, labels, the stored Jev probabilities in the analysis order, the ASReview screening orders and the stopping cross-check sequences; no titles or abstracts). It recomputes the criterion at the 3,148 evaluation points of the cross-check against buscarpy 0.0.2 (largest absolute difference 3.4e-11) and reproduces, for the 23 held-out SYNERGY+ reviews and the 28 CLEF 2019 reviews with a relevant record: label-free ranking with statistical stopping (87.6% read, 23/23 reliable; 86.1%, 28/28), the ASReview ranking with statistical stopping (90.1%, 23/23; 86.9%, 28/28) and the label-free threshold of 0.07 (58.9%, 23/23; 39.1%, 25/28). Run `python validation/reproduce_paper.py` (about 15 minutes).
- `tests/` runs without network access on the 70 records of SYNERGY review Theobald_2021 (CC0; titles and abstracts from OpenAlex, CC0) and the Jev probabilities stored by the evaluation. The tests cover the request bodies, retries and authentication, the whole project flow, the web server's session token and the MCP tools.
- `site/test/core.test.js` checks that the web version builds the same request bodies as the Python package and that its stopping p values match the Python package and buscarpy 0.0.2 (largest difference about 1e-13).
- The demo uses review Attai_2022 (SYNERGY held-out split, CC0) with the Jev probabilities stored by the evaluation.
- A live comparison of new Jev scores with the stored scores (`validation/live_theobald.py`) has not been run yet. The gateway account used for development returned HTTP 403 for Jev on the free tier.

## License

Apache License 2.0. The stopping criterion follows Callaghan MW, Müller-Hansen F. Statistical stopping criteria for automated screening in systematic reviews. Syst Rev. 2020;9:273.
