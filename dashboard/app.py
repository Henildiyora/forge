"""Streamlit dashboard that tails real swarm run event streams.

Node status lights update from `.swarm/runs/<run_id>.jsonl` written by the
graph as it executes — no sleep()-driven fake progress.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

# Allow `streamlit run dashboard/app.py` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swarm.config import REPO_ROOT, get_settings  # noqa: E402
from swarm.progress import (  # noqa: E402
    NODE_SEQUENCE,
    iter_runs,
    node_statuses,
    read_events,
)

st.set_page_config(page_title="DevOps Swarm", layout="wide")
st.title("DevOps Swarm")
st.caption(
    "Live pipeline view of the Monitoring → Code Analysis → Ops → dry-run graph. "
    "Status comes from the run event stream, not a timer."
)

settings = get_settings()
runs_dir = settings.runs_dir
runs_dir.mkdir(parents=True, exist_ok=True)

_RUN_ID_RE = re.compile(r"run_id[=:]?\s*([0-9a-f]{8,})", re.IGNORECASE)


def _extract_run_id(text: str) -> str | None:
    match = _RUN_ID_RE.search(text or "")
    return match.group(1) if match else None


def _newest_run_id() -> str | None:
    files = list(iter_runs(runs_dir))
    return files[0].stem if files else None


# --------------------------------------------------------------------------
# Sidebar: trigger a run / benchmark (button-gated — never auto-run)
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Run")
    scenario = st.selectbox(
        "Scenario",
        ["payment_timeout", "feature_flag_blowup", "retries_zeroed"],
    )
    offline = st.checkbox("Offline metrics (fixture replay)", value=True)

    if st.button("Run swarm scenario", type="primary"):
        cmd = [
            sys.executable,
            "-m",
            "swarm",
            "run",
            "--scenario",
            scenario,
            "--skip-llm",
            "--max-repair-attempts",
            "0",
        ]
        if offline:
            # Insert after --scenario <name>
            cmd[6:6] = ["--offline"]

        with st.spinner("Running…"):
            completed = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
        output = completed.stdout[-4000:] or completed.stderr[-4000:]
        with st.expander("CLI output", expanded=False):
            st.code(output)
        if completed.returncode != 0:
            st.error(f"Exit code {completed.returncode}")
        else:
            st.success("Finished")
            run_id = _extract_run_id(completed.stdout) or _newest_run_id()
            if run_id:
                st.session_state["last_run_id"] = run_id
            st.rerun()

    if st.button("Run full benchmark (offline)"):
        with st.spinner("Benchmarking all scenarios…"):
            completed = subprocess.run(
                [sys.executable, "-m", "benchmark.benchmark", "--offline"],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
        with st.expander("Benchmark CLI output", expanded=False):
            st.code(completed.stdout[-5000:] or completed.stderr[-5000:])
        if completed.returncode != 0:
            st.error(f"Exit code {completed.returncode}")
        else:
            st.success("Benchmark finished")
            run_id = _newest_run_id()
            if run_id:
                st.session_state["last_run_id"] = run_id
            st.rerun()

    st.divider()
    auto = st.checkbox("Auto-refresh (2s)", value=False)

# --------------------------------------------------------------------------
# Pick a run
# --------------------------------------------------------------------------

run_files = list(iter_runs(runs_dir))
if not run_files:
    st.info("No runs yet. Use the sidebar to start one.")
    if auto:
        time.sleep(2)
        st.rerun()
    st.stop()

labels = [p.stem for p in run_files]
preferred = st.session_state.get("last_run_id")
default_index = labels.index(preferred) if preferred in labels else 0
choice = st.selectbox("Run", labels, index=default_index)
path = runs_dir / f"{choice}.jsonl"
events = read_events(path)
statuses = node_statuses(events)

# --------------------------------------------------------------------------
# Pipeline view
# --------------------------------------------------------------------------

st.subheader("Pipeline")
cols = st.columns(len(NODE_SEQUENCE))
color = {
    "pending": "#9aa0a6",
    "running": "#f9ab00",
    "done": "#34a853",
    "failed": "#ea4335",
}
for col, node in zip(cols, NODE_SEQUENCE, strict=True):
    status = statuses.get(node, "pending")
    col.markdown(
        f"""
        <div style="border:2px solid {color[status]}; border-radius:12px;
                    padding:16px; text-align:center; min-height:110px;">
          <div style="font-size:0.8rem; opacity:0.7;">node</div>
          <div style="font-weight:700;">{node}</div>
          <div style="margin-top:8px; color:{color[status]}; font-weight:600;">
            {status.upper()}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# Terminal disposition from events
terminal = next(
    (
        e
        for e in reversed(events)
        if e.get("event") == "node_finished"
        and e.get("node") in {"no_incident", "ready_to_apply", "needs_human_review"}
    ),
    None,
)
if terminal:
    st.markdown(f"**Disposition:** `{terminal.get('node')}`")

# --------------------------------------------------------------------------
# Agent outputs
# --------------------------------------------------------------------------

st.subheader("Agent outputs")


def _payload_for(node: str):
    for event in reversed(events):
        if event.get("event") == "node_finished" and event.get("node") == node:
            return event.get("payload")
    return None


c1, c2 = st.columns(2)
with c1:
    st.markdown("#### IncidentSignal")
    st.json(_payload_for("monitoring_agent") or {})
    st.markdown("#### ProposedFix")
    st.json(_payload_for("ops_agent") or {})
with c2:
    st.markdown("#### CommitCandidate[]")
    st.json(_payload_for("code_analysis_agent") or {})
    st.markdown("#### DryRunResult")
    st.json(_payload_for("dry_run_validate") or {})

# --------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------

with st.expander("Raw event stream", expanded=False):
    st.code("\n".join(json.dumps(e) for e in events[-80:]))

# --------------------------------------------------------------------------
# Benchmark chart
# --------------------------------------------------------------------------

st.subheader("Benchmark comparison")
results_path = REPO_ROOT / "benchmark" / "benchmark_results.json"
if results_path.exists():
    data = json.loads(results_path.read_text(encoding="utf-8"))
    summary = data.get("summary", {})
    st.markdown(
        f"Average reduction: **{summary.get('avg_reduction_pct', '—')}%** "
        f"(manual {summary.get('avg_manual_seconds', '—')}s → "
        f"swarm {summary.get('avg_swarm_seconds', '—')}s)"
    )
    rows = data.get("scenarios", [])
    if rows:
        try:
            import pandas as pd

            frame = pd.DataFrame(
                {
                    "scenario": [r["scenario_id"] for r in rows],
                    "manual_s": [r["manual_baseline_seconds"] for r in rows],
                    "swarm_s": [r["swarm_seconds"] for r in rows],
                }
            ).set_index("scenario")
            st.bar_chart(frame)
        except Exception:  # noqa: BLE001
            st.table(rows)
    st.caption(data.get("disclaimer", ""))
else:
    st.info("No benchmark_results.json yet. Click “Run full benchmark” in the sidebar.")

# Auto-refresh only re-reads events — never launches a new swarm.
if auto:
    time.sleep(2)
    st.rerun()
