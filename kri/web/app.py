"""FastAPI app for KRI (Blueprint Sec. 21 / SPEC.md web).

Endpoints:
  * ``POST /api/series``            -- submit an mbox (raw text) OR a lore URL /
                                       message-id; returns the parsed PatchSeries.
  * ``GET  /api/series/{sid}``      -- return a previously-parsed series (in-memory).
  * ``GET  /api/series/{sid}/reviews`` -- correlated reviews per patch, with
                                       provenance links.
  * ``POST /api/review``            -- submit a patch series for full simulation
                                       review (Sprint-3).
  * ``GET  /api/benchmark``         -- run the benchmark against cached fixtures
                                       (Sprint-3).
  * ``GET  /``                      -- tiny HTML form to submit and view a series.

Domain-agnostic: no subsystem identifiers. Configuration (cache dir, kernel path)
comes from environment variables so the app never hardcodes a mailing list or
domain. Network I/O happens only inside the Lore Manager.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from kri.common.models import PatchSeries
from kri.lore_manager import LoreConfig, LoreManagerImpl
from kri.patch_manager import PatchManagerImpl


class SubmitRequest(BaseModel):
    """Submit either raw ``mbox`` text or a ``lore_ref`` (URL or message-id)."""

    mbox: str | None = None
    lore_ref: str | None = None


class ReviewRequest(BaseModel):
    """Request body for /api/review: submit a patch for simulation review."""

    mbox: str | None = None
    lore_ref: str | None = None
    domain: str | None = None  # DKP domain name, e.g. resolved from file patterns


def _default_cache_dir() -> Path:
    env = os.environ.get("KRI_LORE_CACHE")
    if env:
        return Path(env)
    # data/lore_cache next to the repo root (see SPEC.md caching note).
    return Path(__file__).resolve().parents[3] / "data" / "lore_cache"


def _default_maintainers() -> Path | None:
    env = os.environ.get("KRI_KERNEL_PATH")
    if env:
        p = Path(env) / "MAINTAINERS"
        return p if p.exists() else None
    return None


def create_app(
    lore_manager: LoreManagerImpl | None = None,
    patch_manager: PatchManagerImpl | None = None,
) -> FastAPI:
    """Application factory. Managers may be injected for testing/offline use."""
    app = FastAPI(title="KRI", version="0.1.0",
                  description="Kernel Review Intelligence — Sprint-1 ingest API")

    lore = lore_manager or LoreManagerImpl(LoreConfig(
        cache_dir=_default_cache_dir(),
        inbox=os.environ.get("KRI_LORE_INBOX", "all"),
        maintainers_path=_default_maintainers(),
    ))
    patches = patch_manager or PatchManagerImpl(lore_manager=lore)

    store: OrderedDict[str, PatchSeries] = OrderedDict()
    _STORE_MAX = 100
    app.state.store = store
    app.state.lore = lore
    app.state.patches = patches

    from kri.knowledge_manager.manager import KnowledgeManagerImpl

    # Shared across requests so the learning-iteration counter (/api/learn)
    # actually advances instead of resetting to 1 on every call.
    learning_km = KnowledgeManagerImpl()
    app.state.knowledge_manager = learning_km

    def _store_series(series: PatchSeries) -> None:
        store[series.series_id] = series
        store.move_to_end(series.series_id)
        while len(store) > _STORE_MAX:
            store.popitem(last=False)

    @app.post("/api/series", response_model=PatchSeries)
    def submit_series(req: SubmitRequest) -> PatchSeries:
        if req.mbox:
            series = patches.parse(req.mbox)
        elif req.lore_ref:
            try:
                thread = lore.fetch(req.lore_ref)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=502, detail=f"lore fetch failed: {exc}") from exc
            series = patches.parse(thread)
        else:
            raise HTTPException(status_code=400, detail="provide 'mbox' or 'lore_ref'")
        _store_series(series)
        return series

    @app.get("/api/series/{series_id}", response_model=PatchSeries)
    def get_series(series_id: str) -> PatchSeries:
        series = store.get(series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="series not found")
        return series

    @app.get("/api/series/{series_id}/reviews")
    def get_reviews(series_id: str) -> dict[str, Any]:
        series = store.get(series_id)
        if series is None:
            raise HTTPException(status_code=404, detail="series not found")
        correlated = patches.correlate_reviews(series)
        return {
            "series_id": series_id,
            "reviews": {
                pid: [
                    {
                        "comment_id": c.comment_id,
                        "author": c.author,
                        "is_maintainer": c.is_maintainer,
                        "severity": c.severity.value,
                        "category": c.category,
                        "message": c.message,
                        "source_url": c.provenance.source_url,
                    }
                    for c in comments
                ]
                for pid, comments in correlated.items()
            },
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    # --- Sprint-3 endpoints: review simulation + benchmark ---

    @app.post("/api/review")
    def review_series(req: ReviewRequest) -> dict[str, Any]:
        """Submit a patch series for full simulation review.

        Parses the series, runs the full pipeline (review -> evidence ->
        confidence -> report), and returns the Review Explainability Report."""
        from kri.knowledge_manager.manager import KnowledgeManagerImpl
        from kri.simulation.engine import SimulationEngineImpl

        # Parse the series.
        if req.mbox:
            series = patches.parse(req.mbox)
        elif req.lore_ref:
            try:
                thread = lore.fetch(req.lore_ref)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=502, detail=f"lore fetch failed: {exc}"
                ) from exc
            series = patches.parse(thread)
        else:
            raise HTTPException(status_code=400, detail="provide 'mbox' or 'lore_ref'")

        _store_series(series)

        # Build the simulation engine with DKP.
        km = KnowledgeManagerImpl()
        dkp = None
        if req.domain:
            try:
                dkp = km.load_dkp(req.domain)
            except Exception:  # noqa: BLE001
                pass  # graceful degradation

        sim = SimulationEngineImpl(km, dkp=dkp)
        report = sim.simulate(series)
        return report

    @app.post("/api/review/intelligent")
    def intelligent_review(req: ReviewRequest) -> dict[str, Any]:
        """Submit a patch series for LLM-powered intelligent review.

        Returns structured inline comments, patch summary, lore-style email
        reply, and optionally rule-based decisions alongside."""
        from kri.llm.client import LLMClient, LLMConfig, LLMOfflineError
        from kri.llm.reviewer import IntelligentReviewEngine
        from kri.knowledge_manager.manager import KnowledgeManagerImpl

        # Parse the series.
        if req.mbox:
            series = patches.parse(req.mbox)
        elif req.lore_ref:
            try:
                thread = lore.fetch(req.lore_ref)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=502, detail=f"lore fetch failed: {exc}"
                ) from exc
            series = patches.parse(thread)
        else:
            raise HTTPException(status_code=400, detail="provide 'mbox' or 'lore_ref'")

        _store_series(series)

        # Load DKP for domain context.
        dkp = None
        if req.domain:
            try:
                km = KnowledgeManagerImpl()
                dkp = km.load_dkp(req.domain)
            except Exception:  # noqa: BLE001
                pass

        # Run LLM review.
        try:
            llm_config = LLMConfig()
            client = LLMClient(llm_config)
            engine = IntelligentReviewEngine(client=client, dkp=dkp)
            report = engine.review(series)
            return report.model_dump()
        except LLMOfflineError as e:
            raise HTTPException(
                status_code=503,
                detail=f"LLM service unavailable: {e}. Set ANTHROPIC_AUTH_TOKEN.",
            ) from e

    @app.get("/api/benchmark")
    def run_benchmark() -> dict[str, Any]:
        """Run the benchmark against cached fixtures; return agreement metrics."""

        from kri.benchmark.runner import BenchmarkRunner
        from kri.knowledge_manager.manager import KnowledgeManagerImpl
        from kri.simulation.engine import SimulationEngineImpl

        cache_dir = _default_cache_dir()
        fixture_results: list[dict[str, Any]] = []
        runner = BenchmarkRunner()

        # Discover cached .mbox.gz fixtures.
        if not cache_dir.exists():
            return {"error": "lore cache directory not found", "fixtures": []}

        fixtures = sorted(cache_dir.glob("*.mbox.gz"))
        if not fixtures:
            return {"error": "no cached fixtures found", "fixtures": []}

        # Domain is resolved from environment config, not hardcoded.
        dkp_domain = os.environ.get("KRI_DKP_DOMAIN", "")

        for fixture_path in fixtures:
            try:
                thread = lore.load_cached(fixture_path)
                series = patches.parse(thread)
                ground_truth = lore.extract_reviews(thread)

                # Run simulation.
                km = KnowledgeManagerImpl()
                dkp = None
                if dkp_domain:
                    try:
                        dkp = km.load_dkp(dkp_domain)
                    except Exception:  # noqa: BLE001
                        pass

                sim = SimulationEngineImpl(km, dkp=dkp)
                sim.simulate(series)

                # Reconstruct decisions for benchmark comparison.
                from kri.confidence_engine.engine import ConfidenceEngineImpl
                from kri.evidence_engine.engine import EvidenceEngineImpl
                from kri.process_rules.manager import (
                    ProcessEtiquettePlugin,
                    ProcessRulesManagerImpl,
                )
                from kri.review_engine.engine import ReviewEngineImpl

                ev_engine = EvidenceEngineImpl(km)
                conf_engine = ConfidenceEngineImpl()
                re_engine = ReviewEngineImpl(ev_engine, conf_engine)
                extra_plugins = [ProcessEtiquettePlugin(ProcessRulesManagerImpl())]

                decisions = re_engine.review(series, dkp, extra_plugins=extra_plugins)

                metrics = runner.compare(decisions, ground_truth, series)
                fixture_results.append({
                    "fixture": fixture_path.name,
                    "series_id": series.series_id,
                    "metrics": metrics.to_dict(),
                })
            except Exception as exc:  # noqa: BLE001
                fixture_results.append({
                    "fixture": fixture_path.name,
                    "error": str(exc),
                })

        return {"fixtures": fixture_results}

    @app.get("/api/learn")
    def run_learning() -> dict[str, Any]:
        """Ingest cached fixtures through the Learning Engine's extract stage.

        Read-only/observational: surfaces candidate patterns with their honest
        support levels (Constitution: no hallucinated knowledge) rather than
        auto-promoting them into a DKP's rule set."""
        from kri.learning.extraction import HistoricalPatternExtractor

        cache_dir = _default_cache_dir()
        if not cache_dir.exists():
            return {"error": "lore cache directory not found", "patterns": []}

        fixtures = sorted(cache_dir.glob("*.mbox.gz"))
        if not fixtures:
            return {"error": "no cached fixtures found", "patterns": []}

        threads = []
        for fixture_path in fixtures:
            try:
                threads.append(lore.load_cached(fixture_path))
            except Exception:  # noqa: BLE001
                continue

        extractor = HistoricalPatternExtractor(lore, patches)
        candidates = extractor.extract_patterns(threads)

        learning_iteration = learning_km.bump_learning_iteration()

        return {
            "fixtures_ingested": len(threads),
            "patterns": [c.as_pattern() for c in candidates],
            "learning_iteration": learning_iteration,
        }

    return app


_INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>KRI — Kernel Review Intelligence</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}
h1{color:#2c3e50}
.input-group{margin:1rem 0}
label{font-weight:600;display:block;margin-bottom:0.3rem}
input,textarea{width:100%;padding:0.5rem;border:1px solid #ccc;border-radius:4px;font-family:monospace}
textarea{height:10rem}
.btn-group{margin:1rem 0;display:flex;gap:1rem}
button{padding:0.6rem 1.5rem;border:none;border-radius:4px;cursor:pointer;font-size:1rem;font-weight:600}
.btn-parse{background:#3498db;color:#fff}
.btn-parse:hover{background:#2980b9}
.btn-review{background:#27ae60;color:#fff}
.btn-review:hover{background:#219a52}
.btn-intel{background:#8e44ad;color:#fff}
.btn-intel:hover{background:#732d91}
.btn-parse:disabled,.btn-review:disabled,.btn-intel:disabled{background:#bdc3c7;cursor:wait}
#status{margin:0.5rem 0;font-style:italic;color:#7f8c8d}
.result-section{margin-top:1.5rem}
.decision{border:1px solid #ddd;border-radius:6px;padding:1rem;margin:0.8rem 0;background:#f9f9f9}
.decision h3{margin:0 0 0.5rem 0}
.publishable{border-left:4px solid #27ae60}
.unpublishable{border-left:4px solid #e74c3c}
.confidence-bar{height:8px;border-radius:4px;background:#ecf0f1;margin:0.3rem 0}
.confidence-fill{height:100%;border-radius:4px;transition:width 0.3s}
.factor{font-size:0.85rem;color:#555;margin:0.2rem 0}
.evidence{font-size:0.85rem;color:#2c3e50;margin:0.2rem 0 0.2rem 1rem}
pre{background:#2c3e50;color:#ecf0f1;padding:1rem;border-radius:6px;overflow:auto;max-height:400px;font-size:0.85rem}
.disclaimer{background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:0.8rem;margin:1rem 0;font-size:0.9rem}
</style></head><body>
<h1>KRI — Kernel Review Intelligence</h1>
<p>Submit a lore message-id, URL, or paste raw mbox text for AI-simulated maintainer review.</p>

<div class="input-group">
<label for="ref">Lore reference (message-id or full URL):</label>
<input id="ref" placeholder="20260714145250.2473461-1-user@example.com  or  https://lore.kernel.org/...">
</div>

<div class="input-group">
<label for="domain">Domain (DKP):</label>
<input id="domain" value="" placeholder="e.g. subsystem name (auto-detected if blank)">
</div>

<div class="input-group">
<label for="mbox">Or paste raw mbox:</label>
<textarea id="mbox"></textarea>
</div>

<div class="btn-group">
<button class="btn-parse" onclick="submitSeries()">Parse Series</button>
<button class="btn-review" onclick="submitReview()">Run Review</button>
<button class="btn-intel" onclick="submitIntelligent()">Intelligent Review (AI)</button>
</div>
<div id="status"></div>

<div id="results"></div>
<pre id="raw" style="display:none"></pre>

<script>
function getRef(){
  let ref=document.getElementById('ref').value.trim();
  // Extract message-id from full lore URL
  if(ref.includes('lore.kernel.org')){
    const m=ref.match(/lore\\.kernel\\.org\\/[^/]+\\/([^/]+)/);
    if(m) ref=m[1];
  }
  return ref;
}
function setStatus(msg){document.getElementById('status').textContent=msg}
function setButtons(disabled){
  document.querySelectorAll('button').forEach(b=>b.disabled=disabled);
}

async function submitSeries(){
  const ref=getRef();
  const mbox=document.getElementById('mbox').value;
  if(!ref&&!mbox){setStatus('Please provide a lore ref or mbox.');return}
  const body=ref?{lore_ref:ref}:{mbox:mbox};
  setStatus('Parsing series...');setButtons(true);
  try{
    const r=await fetch('/api/series',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok){setStatus('Error: '+(data.detail||r.statusText));return}
    setStatus('Parsed successfully.');
    document.getElementById('raw').style.display='block';
    document.getElementById('raw').textContent=JSON.stringify(data,null,2);
    document.getElementById('results').innerHTML=renderSeries(data);
  }catch(e){setStatus('Network error: '+e.message)}
  finally{setButtons(false)}
}

async function submitReview(){
  const ref=getRef();
  const mbox=document.getElementById('mbox').value;
  const domain=document.getElementById('domain').value.trim()||null;
  if(!ref&&!mbox){setStatus('Please provide a lore ref or mbox.');return}
  const body=ref?{lore_ref:ref,domain}:{mbox,domain};
  setStatus('Running review simulation (may take a few seconds)...');setButtons(true);
  try{
    const r=await fetch('/api/review',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok){setStatus('Error: '+(data.detail||r.statusText));return}
    setStatus('Review complete.');
    document.getElementById('raw').style.display='block';
    document.getElementById('raw').textContent=JSON.stringify(data,null,2);
    document.getElementById('results').innerHTML=renderReport(data);
  }catch(e){setStatus('Network error: '+e.message)}
  finally{setButtons(false)}
}

function renderSeries(s){
  return `<div class="result-section">
    <h2>Patch Series: ${esc(s.series_title||s.series_id)}</h2>
    <p><b>Patches:</b> ${(s.patches||[]).length} | <b>Series ID:</b> <code>${esc(s.series_id)}</code></p>
    ${(s.patches||[]).map(p=>`<div style="margin:0.3rem 0">• ${esc(p.subject||p.patch_id)}</div>`).join('')}
  </div>`;
}

function renderReport(r){
  let html=`<div class="disclaimer">${esc(r.disclaimer)}</div>`;
  html+=`<div class="result-section"><h2>Review: ${esc(r.metadata.series_title)}</h2>
    <p><b>Decisions:</b> ${r.metadata.total_decisions} |
    <b>Publishable:</b> ${r.metadata.publishable_decisions} |
    <b>Domain:</b> ${esc(r.metadata.dkp_name||'none')}</p></div>`;
  for(const d of r.decisions||[]){
    const pub=d.publishable;
    const score=((d.confidence?.score||0)*100).toFixed(1);
    const color=pub?'#27ae60':'#e74c3c';
    html+=`<div class="decision ${pub?'publishable':'unpublishable'}">
      <h3>${esc(d.what||d.category)}</h3>
      <p><b>Layer:</b> ${d.layer} | <b>Severity:</b> ${d.severity} | <b>Publishable:</b> ${pub?'Yes':'No'}</p>
      <p><b>Confidence:</b> ${score}% (${d.confidence?.level||'?'})</p>
      <div class="confidence-bar"><div class="confidence-fill" style="width:${score}%;background:${color}"></div></div>`;
    if(d.confidence?.factor_scores){
      html+=`<p style="margin-top:0.5rem"><b>Factor breakdown:</b></p>`;
      for(const[f,s]of Object.entries(d.confidence.factor_scores)){
        if(s>0){
          const w=d.confidence.factor_weights?.[f]||0;
          html+=`<div class="factor">${f}: ${(s*100).toFixed(0)}% × ${w.toFixed(2)} = ${(s*w*100).toFixed(1)}%</div>`;
        }
      }
    }
    if(d.evidence_graph&&d.evidence_graph.items&&d.evidence_graph.items.length){
      html+=`<p style="margin-top:0.5rem"><b>Evidence (${d.evidence_graph.verified_count}/${d.evidence_graph.evidence_count} verified):</b></p>`;
      for(const e of d.evidence_graph.items){
        const url=e.source_url||e.repo_path||'';
        const link=url.startsWith('http')?`<a href="${esc(url)}" target="_blank">${esc(url)}</a>`:esc(url);
        html+=`<div class="evidence">[${e.verified?'✓':'✗'}] ${esc(e.summary||'')} — ${link}</div>`;
      }
    }
    html+=`</div>`;
  }
  return html;
}

async function submitIntelligent(){
  const ref=getRef();
  const mbox=document.getElementById('mbox').value;
  const domain=document.getElementById('domain').value.trim()||null;
  if(!ref&&!mbox){setStatus('Please provide a lore ref or mbox.');return}
  const body=ref?{lore_ref:ref,domain}:{mbox,domain};
  setStatus('Running intelligent AI review (this may take 30-60 seconds)...');setButtons(true);
  try{
    const r=await fetch('/api/review/intelligent',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await r.json();
    if(!r.ok){setStatus('Error: '+(data.detail||r.statusText));return}
    setStatus('Intelligent review complete.');
    document.getElementById('raw').style.display='block';
    document.getElementById('raw').textContent=JSON.stringify(data,null,2);
    document.getElementById('results').innerHTML=renderIntelligent(data);
  }catch(e){setStatus('Network error: '+e.message)}
  finally{setButtons(false)}
}

function renderIntelligent(r){
  let html=`<div class="disclaimer">${esc(r.disclaimer)}</div>`;
  html+=`<div class="result-section"><h2>Intelligent Review: ${esc(r.series_title)}</h2>`;
  if(r.metadata){
    html+=`<p><b>Model:</b> ${esc(r.metadata.llm_model||'')} | <b>Time:</b> ${r.metadata.processing_time_seconds||0}s</p>`;
  }
  if(r.overall_assessment){
    html+=`<div style="background:#f0f7ff;border:1px solid #3498db;border-radius:4px;padding:0.8rem;margin:0.8rem 0">
      <b>Overall Assessment:</b> ${esc(r.overall_assessment)}</div>`;
  }
  html+=`</div>`;
  for(const pr of r.patches||[]){
    html+=`<div class="result-section"><h3>Patch: ${esc(pr.subject||pr.patch_id)}</h3>`;
    if(pr.summary){
      html+=`<p><b>Summary:</b> ${esc(pr.summary.what_it_does)}</p>`;
      if(pr.summary.change_type)html+=`<p><b>Type:</b> ${esc(pr.summary.change_type)} | <b>Subsystem:</b> ${esc(pr.summary.subsystem)}</p>`;
      if(pr.summary.risk_areas&&pr.summary.risk_areas.length)html+=`<p><b>Risks:</b> ${pr.summary.risk_areas.map(esc).join(', ')}</p>`;
    }
    if(pr.inline_comments&&pr.inline_comments.length){
      html+=`<p><b>Issues Found (${pr.inline_comments.length}):</b></p>`;
      for(const c of pr.inline_comments){
        const sev=c.severity==='blocker'?'#e74c3c':c.severity==='warning'?'#f39c12':'#3498db';
        html+=`<div class="decision" style="border-left:4px solid ${sev}">
          <p><b>${esc(c.file_path)}:${c.line_number}</b> [${esc(c.category)}]</p>
          <p>${esc(c.message)}</p>`;
        if(c.suggestion)html+=`<pre style="background:#2c3e50;color:#ecf0f1;padding:0.5rem;border-radius:4px;font-size:0.85rem">${esc(c.suggestion)}</pre>`;
        html+=`<p class="factor">Confidence: ${(c.confidence*100).toFixed(0)}%</p></div>`;
      }
    }else{
      html+=`<p style="color:#27ae60"><b>No issues found.</b></p>`;
    }
    if(pr.lore_reply){
      html+=`<details style="margin:1rem 0"><summary><b>Lore Email Reply (click to expand)</b></summary>
        <pre style="white-space:pre-wrap;background:#f9f9f9;border:1px solid #ddd;padding:1rem">${esc(pr.lore_reply)}</pre></details>`;
    }
    html+=`</div>`;
  }
  return html;
}

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
</script></body></html>"""


# Module-level app for `uvicorn kri.web.app:app`.
app = create_app()
