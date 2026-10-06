"""MCP server for ReviewFast: lets an MCP client (Claude Desktop, Claude Code, other assistants) run the screening workflow on
local project files. Start it with `reviewfast-mcp` (stdio transport).

The workflow is the one the accompanying paper evaluated: score the records with a zero-shot LLM classifier (Jev, ten records
per request, the paper's prompt), freeze the ranking, let a person screen in ranked order, and stop when the statistical
criterion of Callaghan and Mueller-Hansen indicates at least 95% recall with 95% confidence. Screening decisions must come from
the human reviewer; the stopping criterion is only valid for human decisions made in ranked order.
"""
import json
import os
import threading
from pathlib import Path

try:                                    # mcp 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

from . import __version__, records_io, report, scoring, stopping
from .jev_client import GATEWAY_URL, JevClient, estimate_cost
from .project import Project, ProjectError

DATA_NOTICE = ('The criteria and the titles and abstracts are sent to TypeSafe AI through the Vercel AI Gateway (United States). '
               'The provider does not offer zero data retention. Send only published or otherwise cleared records; do not send '
               'unpublished, confidential or personal data.')

INSTRUCTIONS = f"""ReviewFast {__version__}: title and abstract screening for systematic reviews.
Workflow: create_project -> import_records -> estimate_scoring_cost -> score_records -> freeze_ranking ->
repeat (next_record -> the human reviewer decides -> record_decision) -> stop_screening when stopping_met is true ->
screen the manual queue (next_record with queue='manual') -> export_results.
Rules:
- record_decision must carry the human reviewer's own decision. Do not decide inclusion on the reviewer's behalf; the stopping
  criterion assumes human decisions made in ranked order.
- Do not show the classifier probability to the reviewer unless they ask (it can anchor decisions).
- Before score_records, show the reviewer this data notice and get their agreement: {DATA_NOTICE}
- Do not use a probability threshold as a stopping rule; the paper did not support it.
Scoring needs a Vercel AI Gateway API key with paid credits in the environment variable AI_GATEWAY_API_KEY."""

server = MCPServer(name='reviewfast', instructions=INSTRUCTIONS)


def _open(project):
    path = Path(project).expanduser()
    if not path.exists():
        raise ProjectError(f'No project file at {path}. Create one with create_project.')
    p = Project(path)
    if not p.meta('created'):
        p.close()
        raise ProjectError(f'{path} is not a ReviewFast project.')
    return p


def _record_view(r, show_score):
    if r is None:
        return None
    out = {k: r.get(k) for k in ('rid', 'rank', 'title', 'abstract', 'authors', 'year', 'journal', 'doi', 'pmid', 'flag')}
    if show_score:
        out['probability'] = r.get('p')
    return {k: v for k, v in out.items() if v is not None}


@server.tool()
def check_stopping(labels: list[int], total_records: int, recall_target: float = 0.95, confidence: float = 0.95) -> dict:
    """Statistical stopping criterion (Callaghan and Mueller-Hansen 2020) for any ranked screen.

    labels: relevance (1 = include or maybe, 0 = exclude) of the records screened so far, in ranked order.
    total_records: number of records in the ranked set. Returns the p value and whether screening can stop."""
    if total_records < len(labels):
        raise ValueError('total_records is smaller than the number of screened records.')
    st = stopping.status([int(bool(x)) for x in labels], int(total_records), recall_target, confidence)
    return st.to_dict()


@server.tool()
def create_project(path: str, title: str, research_question: str, eligibility_criteria: str) -> dict:
    """Creates a project file (SQLite). Use the eligibility criteria from the review protocol, written before screening."""
    p = Project.create(Path(path).expanduser(), title, research_question, eligibility_criteria)
    try:
        return {'project': str(Path(path).expanduser()), 'seed': p.meta('seed'), 'app_version': __version__}
    finally:
        p.close()


@server.tool()
def import_records(project: str, files: list[str]) -> dict:
    """Imports RIS, CSV, PubMed (.nbib) or PubMed XML files. Removes duplicates and sends records without an abstract or
    apparently not in English to the manual queue."""
    p = _open(project); out = {}
    try:
        for f in files:
            fmt, recs = records_io.parse_file(f, Path(f).expanduser().read_bytes())
            out[f] = {'format': fmt, **p.import_records(recs, f'{fmt}:{Path(f).name}')}
        out['counts'] = p.counts()
        return out
    finally:
        p.close()


@server.tool()
def project_status(project: str) -> dict:
    """Counts, scoring summary and stopping status of a project."""
    p = _open(project)
    try:
        return {'counts': p.counts(), 'scoring': p.scoring_summary(), 'stop': p.stop_status(), 'ranked': p.is_ranked()}
    finally:
        p.close()


@server.tool()
def estimate_scoring_cost(project: str) -> dict:
    """Records still to score and the estimated cost at the list price used in the paper (about US$0.02 per 1,000 records)."""
    p = _open(project)
    try:
        n = len(p.to_score())
        return {'records_to_score': n, 'estimated_cost_usd': round(estimate_cost(n), 4), 'data_notice': DATA_NOTICE}
    finally:
        p.close()


@server.tool()
def score_records(project: str, reviewer_accepted_data_notice: bool) -> dict:
    """Scores all unscored records with Jev (ten per request, the paper's prompt). Requires AI_GATEWAY_API_KEY and the
    reviewer's agreement to the data notice. Can take several minutes for large reviews; rerun to resume."""
    if not reviewer_accepted_data_notice:
        return {'scored': False, 'reason': 'The reviewer has not accepted the data notice.', 'data_notice': DATA_NOTICE}
    key = os.environ.get('AI_GATEWAY_API_KEY')
    if not key:
        return {'scored': False, 'reason': 'Set AI_GATEWAY_API_KEY (Vercel AI Gateway key with paid credits) in the MCP server environment.'}
    p = _open(project); client = JevClient(key, endpoint=GATEWAY_URL)
    try:
        return {'scored': True, **scoring.run(p, client, cancel=threading.Event())}
    finally:
        client.close(); p.close()


@server.tool()
def freeze_ranking(project: str) -> dict:
    """Fixes the screening order (highest probability first; ties broken by the project seed). Records without a score move to
    the manual queue. Records and criteria cannot change afterwards."""
    p = _open(project)
    try:
        p.freeze_ranking()
        return p.counts()
    finally:
        p.close()


@server.tool()
def next_record(project: str, queue: str = 'ranked', show_probability: bool = False) -> dict:
    """The next record to screen. queue='ranked' (in ranked order) or 'manual'. The probability is hidden unless requested."""
    if queue not in ('ranked', 'manual'):
        raise ValueError("queue must be 'ranked' or 'manual'.")
    p = _open(project)
    try:
        r = p.next_record(queue)
        return {'record': _record_view(r, show_probability), 'stop': p.stop_status()}
    finally:
        p.close()


@server.tool()
def record_decision(project: str, record_id: int, decision: str) -> dict:
    """Saves the human reviewer's decision ('include', 'maybe' or 'exclude') for the record returned by next_record.
    Returns the updated stopping status; 'maybe' counts as relevant."""
    p = _open(project)
    try:
        st = p.decide(int(record_id), decision)
        return {'stop': st, 'stopping_met': st['met']}
    finally:
        p.close()


@server.tool()
def undo_last_decision(project: str) -> dict:
    """Removes the most recent decision."""
    p = _open(project)
    try:
        return {'undone': p.undo(), 'stop': p.stop_status()}
    finally:
        p.close()


@server.tool()
def stop_screening(project: str) -> dict:
    """Stops screening of the ranked set. Only allowed once the stopping criterion is met. The manual queue must still be
    screened in full."""
    p = _open(project)
    try:
        return p.stop()
    finally:
        p.close()


@server.tool()
def export_results(project: str, out_dir: str) -> dict:
    """Writes decisions.csv, included.ris, methods.txt (draft methods paragraph and PRISMA counts) and archive.zip."""
    p = _open(project); out = Path(out_dir).expanduser()
    try:
        out.mkdir(parents=True, exist_ok=True)
        (out / 'decisions.csv').write_text(records_io.to_csv(report.decisions_rows(p), report.DECISION_COLUMNS))
        (out / 'included.ris').write_text(report.included_ris(p))
        (out / 'methods.txt').write_text(report.methods_text(p))
        (out / 'archive.zip').write_bytes(report.archive(p))
        return {'written': [str(out / n) for n in ('decisions.csv', 'included.ris', 'methods.txt', 'archive.zip')]}
    finally:
        p.close()


def main():
    server.run()


if __name__ == '__main__':
    main()
