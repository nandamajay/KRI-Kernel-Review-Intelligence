# Phase-4V2 — Browser + Source-Level Validation Design
**Date:** 2026-07-25  
**Phase:** PHASE-4V2 — Autonomous Browser + Source-Level Kernel Review Validation  
**Status:** DESIGN — do not implement until GO approved  
**Author:** Autonomous design agent (5-agent governance: design phase)

---

## 1. Environment Audit

### 1.1 Kernel Source

| Property | Value |
|----------|-------|
| Path | `/local/mnt/workspace/Linux_kernel_org/linux` |
| Remote | `git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git` (mainline) |
| HEAD | `30d4efb2f` — Merge tag `for-linus-6.18-rc1-tag` |
| Latest stable tag present | `v6.12.y` family (linux-stable range) |
| Mainline tags | v6.x through v6.18-rc1 (pre-release) |
| v7.x | Not present — mainline is not yet at v7.x |
| `KRI_KERNEL_PATH` env var | **UNSET** — must be set before each validation run |

**Assessment:** Mainline Torvalds repo is present. v6.18-rc1 is the latest available. This is superior to the linux-stable v6.12.97 used in Phase-4V — it covers the latest development surface.

### 1.2 Browser Automation

| Property | Value |
|----------|-------|
| Playwright Python | v1.59.0 — **INSTALLED** |
| Chromium binary | **MISSING** — requires `playwright install --only-shell chromium` |
| Chrome/Chromium system packages | Not installed |
| Firefox system package | Not installed |
| Selenium | Not installed |
| Install feasibility | YES — `~/.cache` has 1.8T available; dry-run shows ~150MB download |

**Assessment:** `GO_WITH_TOOLING_INSTALL_FIRST`. Run `playwright install --only-shell chromium` before browser validation phase. This is a browser binary download, not a project dependency change — safe to execute without approval.

### 1.3 Existing Patch Corpus

Patches from Phase-4V are already fetched at `/tmp/c*.mbox`. These will be incorporated into the V2 corpus. Additional patches must be fetched for:
- S6 (apply-failure patch — needs a patch from older baseline)
- S12 (net subsystem, outside ASoC)

---

## 2. Agent Model

Phase-4V2 uses 12 specialized agents. Multi-agent review required for all fixes. No single-agent validation.

### Agent Roster

| # | Agent | Primary Responsibility |
|---|-------|----------------------|
| 1 | **Validation Architect** | Owns validation matrix; defines pass/fail criteria; prevents fake validation; final PASS/FAIL per sample |
| 2 | **Browser Automation Agent** | Installs/verifies Playwright; starts KRI server; drives browser UI; validates DOM elements |
| 3 | **Lore Acquisition Agent** | Fetches real patches from lore.kernel.org; records URLs/message-IDs; selects diverse categories |
| 4 | **Kernel Source Agent** | Updates linux kernel source; sets KRI_KERNEL_PATH; records baseline commit; creates/cleans worktrees |
| 5 | **Patch Application Agent** | Applies patches to worktrees; classifies APPLY_CLEAN / APPLY_FAILED / APPLY_CONFLICT; gates source-level review |
| 6 | **CLI Validation Agent** | Drives Python TestClient path; captures JSON; validates all Track-A fields |
| 7 | **Web/API Validation Agent** | Starts KRI via uvicorn subprocess; POSTs to `/api/review/intelligent`; validates JSON schema |
| 8 | **UI/UX Review Agent** | Evaluates browser-rendered review for user-friendliness; checks evidence badges, CFM, governance, reducer |
| 9 | **Review Quality Agent** | Classifies each KRI comment (surgical/generic/false-positive/etc.); judges lore-style fitness |
| 10 | **Governance/Constitution Auditor** | Verifies no Track-B scope creep; checks safety floor, CFM shadow-only, evidence provenance, Sec-40 |
| 11 | **Fix Agent** | Implements in-scope fixes; adds tests; re-runs validation; operates under 5-agent governance |
| 12 | **Arbiter** | Decides PASS / REWORK / STOP per sample and per fix; commits allowed fixes; updates report |

### Agent Communication Protocol

```
Validation Architect → assigns samples → CLI/API/Browser/Kernel/Patch Agents (parallel)
Each validation path → reports to → Validation Architect
Validation Architect → finds gap → Fix Agent (if in-scope)
Fix Agent → 5-agent governance review → Arbiter
Arbiter → authorizes commit → Validation Architect continues
Governance Auditor → runs in parallel on every fix → can raise STOP
```

### When to escalate to STOP
- Governance Auditor raises Track-B, safety-floor, or Sec-40 violation
- Arbiter finds unresolvable disagreement (MAX_REWORK_ATTEMPTS=3 exceeded)
- Browser automation changes production behavior

---

## 3. Kernel Source Policy

### 3.1 Canonical Kernel Path

```
KRI_KERNEL_PATH=/local/mnt/workspace/Linux_kernel_org/linux
```

This is the mainline Torvalds tree. It is already present. No clone needed.

### 3.2 Pre-Validation Update Protocol

Before any validation session:

```bash
git -C $KRI_KERNEL_PATH fetch origin --tags --quiet
git -C $KRI_KERNEL_PATH log --oneline -1  # record exact HEAD
```

Record: `KERNEL_BASELINE_COMMIT` = HEAD sha1 at fetch time. All source-level claims reference this commit.

### 3.3 Baseline Selection

| Baseline | Tag / Ref | Rationale |
|----------|-----------|-----------|
| Primary | `v6.12` | Last LTS stable release in mainline tree; most patches target this era |
| Secondary | `HEAD` (`30d4efb2f`) | For testing latest patches (6.18-rc1 era) |
| Apply-failure test | `v6.12` + intentionally old patch | Produces deterministic APPLY_FAILED |

Use `v6.12` as the default baseline for apply tests. This gives the highest chance of clean apply for recent-stable patches while also being deterministic.

### 3.4 Worktree Management

For each patch/series under validation:

```bash
WORKTREE=/tmp/kri_wt_$(echo "$patch_id" | sha256sum | cut -c1-12)
git -C $KRI_KERNEL_PATH worktree add $WORKTREE v6.12 --quiet

# Apply patch
git -C $WORKTREE am --quiet < /tmp/patch.mbox
APPLY_STATUS=$?

# Run KRI with worktree path
KRI_KERNEL_PATH=$WORKTREE python review...

# Always clean up
git -C $KRI_KERNEL_PATH worktree remove --force $WORKTREE 2>/dev/null
```

**Critical invariant:** Source-level review claims (blame evidence, applicability status) must **never** be made when `APPLY_STATUS != 0`. If apply fails, the review still runs (diff-level) but `apply_status.ok=False` and `apply_status.degraded_reason="APPLY_FAILED"` must appear in response.

### 3.5 Mainline vs Stable Policy

| Situation | Action |
|-----------|--------|
| Patch targets `net-next` or `mainline` | Use `HEAD` baseline |
| Patch targets `linux-stable` / LTS | Use `v6.12` baseline |
| Patch targets a subsystem tree not in mainline | Use `v6.12`; record `SUBSYSTEM_TREE_ABSENT` |
| v7.x tags requested | Not available — record `MAINLINE_NOT_AT_v7` |

### 3.6 Post-Validation Cleanup

```bash
# Remove all KRI worktrees
git -C $KRI_KERNEL_PATH worktree prune
# Verify main working tree is untouched
git -C $KRI_KERNEL_PATH status --short  # must be empty
```

Never commit to the kernel repo. Never modify kernel source in the main working tree.

---

## 4. Browser Automation Plan

### 4.1 Installation Protocol

```bash
# Step 1: Install chromium headless shell (no system deps needed)
playwright install --only-shell chromium

# Step 2: Verify browser launch
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.goto('about:blank')
    print('BROWSER_OK:', page.title())
    b.close()
"
```

Expected output: `BROWSER_OK: ` (empty title for about:blank)

If this fails, record `BROWSER_AUTOMATION_BLOCKED` with exact error — but still design and run all non-browser test paths.

### 4.2 KRI Server Launch for Browser Tests

Browser tests require a real HTTP server (TestClient does not serve a browser-accessible URL). Use `uvicorn` subprocess:

```python
import subprocess, time, requests

proc = subprocess.Popen([
    "python", "-m", "uvicorn",
    "kri.web.app:app",
    "--host", "127.0.0.1",
    "--port", "8765",
    "--no-access-log",
], env={**os.environ, "KRI_KERNEL_PATH": KERNEL_PATH},
   stdout=subprocess.PIPE, stderr=subprocess.PIPE)

# Wait for server ready
for _ in range(30):
    try:
        requests.get("http://127.0.0.1:8765/", timeout=1)
        break
    except Exception:
        time.sleep(0.5)
```

### 4.3 Browser Test Suite Structure

Each browser test:
1. Opens `http://127.0.0.1:8765/`
2. Pastes mbox content into the textarea (`#mbox`)
3. Sets domain field if needed (`#domain`)
4. Clicks submit button
5. Waits for results div to populate (polling `#results:not(:empty)`)
6. Extracts and validates DOM elements

```python
from playwright.sync_api import sync_playwright, Page

def browser_review(page: Page, mbox: str, domain: str = "") -> dict:
    page.goto("http://127.0.0.1:8765/")
    page.fill("textarea", mbox)
    if domain:
        page.fill("input[placeholder*='domain']", domain)
    page.click("button[type='submit']")
    # Wait up to 120s for LLM response
    page.wait_for_function("document.getElementById('results').innerHTML.length > 100",
                           timeout=120_000)
    html = page.inner_html("#results")
    return {"html": html, "console_errors": []}
```

### 4.4 DOM Assertions

For each browser validation run, assert:

```python
# Evidence badge rendered when evidence_status != unknown
page.wait_for_selector(".finding-card") if comments_expected
evidence_badges = page.query_selector_all("[style*='Evidence:']")

# CFM shadow score rendered
cfm_elements = page.query_selector_all("*:has-text('shadow mode')")

# Governance warnings rendered when present
gov_elements = page.query_selector_all("*:has-text('Governance Violations')")

# Knowledge state rendered when domain set
ks_elements = page.query_selector_all("code")  # knowledge_state_id shown in <code>

# No JS console errors
errors = []
page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

# Multi-patch: each patch section rendered
patch_sections = page.query_selector_all("h3")  # "Patch: <subject>"
```

### 4.5 Fallback: TestClient HTML Assertions

If browser automation is blocked, fall back to TestClient + HTML body assertions — already implemented in test_phase4_wp4f.py and test_phase4_wp4v.py. Document as `BROWSER_VALIDATION_FALLBACK_TESTCLIENT`.

---

## 5. Patch Acquisition Strategy

### 5.1 Validation Corpus (12 Samples)

All patches from public lore.kernel.org. Fetch method: `wget -q -O file "URL/raw"` (curl blocked by Anubis bot).

| # | ID | Source URL | Subject | Subsystem | Patches | Expected Apply | Domain | Validation Path |
|---|----|----|---------|-----------|---------|----------------|--------|----------------|
| S1 | ASoC whitespace | `https://lore.kernel.org/alsa-devel/20260720103505.1860399-1-mstrozek@opensource.cirrus.com/raw` | ALSA: control: tidy up whitespaces | sound/core | 1 | CLEAN (v6.12) | asoc | CLI+API+Browser+Source |
| S2 | DT binding | `https://lore.kernel.org/linux-i2c/20260724-apple-t603x-initial-devices-v3-1-bbeba0420603@jannau.net/raw` | dt-bindings: arm: apple: Add M3 Pro/Max/Ultra | Documentation/dt | 1 | CLEAN (v6.12) | asoc | CLI+API+Browser+Source |
| S3 | Multi-patch net/phy | `https://lore.kernel.org/netdev/20260724190811.198339-2-ansuelsmth@gmail.com/raw` (1/6 through 6/6) | net: phy: mediatek: calibration series | drivers/net/phy | 6 | CLEAN (mainline) | none | CLI+API+Browser+Source+Series |
| S4 | SPI driver | `https://lore.kernel.org/linux-spi/...spi-cadence-quadspi.../raw` | spi: cadence-quadspi dt-bindings | drivers/spi | 1 | CLEAN (v6.12) | none | CLI+API+Browser+Source |
| S5 | Clean apply | `https://lore.kernel.org/netdev/20260724142909.3270824-2-dnlplm@gmail.com/raw` | net: wwan: add IOCTls support | drivers/net/wwan | 1 | CLEAN (mainline HEAD) | none | CLI+API+Source (apply verification) |
| S6 | Apply-failure | Fetch a 2023-era ASoC patch against v5.15 applied to v6.12 | (old subsystem patch; exact URL TBD by Lore Acquisition Agent) | sound/soc/codecs | 1 | **APPLY_FAILED** | asoc | CLI+API (diff-only; source claims forbidden) |
| S7 | Checkpatch warnings | `https://lore.kernel.org/linux-staging/20260723185217.317981-2-nikolayof23@gmail.com/raw` | staging: media: atomisp: remove unused functions | drivers/staging | 1 | LIKELY_CONFLICT | none | CLI+API+Browser+Checkpatch |
| S8 | Rich git blame | `https://lore.kernel.org/linux-mm/382fb2620d699aed276c8e21e3f5925082c4b5dd.1784856856.git.luizcap@redhat.com/raw` | mm: introduce pgtable_has_pmd_leaves() | mm/ | 1 | CLEAN (mainline) | asoc | CLI+API+Source+**BLAME** |
| S9 | Prior version | `https://lore.kernel.org/netdev/20260724142909.3270824-2-dnlplm@gmail.com/raw` | net: wwan: v3 series | drivers/net/wwan | 2 | CLEAN (mainline) | none | CLI+API+**PriorVersion** |
| S10 | Cross-patch dep | `https://lore.kernel.org/netdev/20260724-ax88179a-v3-1-bdde4f905883@birger-koblitz.de/raw` (1-3/13) | ax88179_178a: USB driver series | drivers/net/usb | 3 | CLEAN (mainline) | none | CLI+API+Browser+**CouplingNotes** |
| S11 | Simple clean | `https://lore.kernel.org/linux-kernel/20260713223234.24812-2-ebiggers@kernel.org/raw` | crypto: pcrypt - Remove pcrypt | crypto/ | 1 | CLEAN (v6.12) | none | CLI+API+Browser |
| S12 | Outside ASoC | `https://lore.kernel.org/netdev/20260724190811.198339-2-ansuelsmth@gmail.com/raw` (1/6) | net: phy: mediatek export | drivers/net/phy | 1 | CLEAN (mainline) | none | CLI+API+Browser (**no evidence engine**) |

**Note on S6:** Lore Acquisition Agent must fetch a patch from 2022-2023 era that targets files significantly changed in v6.12. Candidate: any staging/ASoC patch from 2022 that adds a function later refactored away. Exact URL determined by agent at runtime.

### 5.2 Fetch Commands

```bash
# Standard fetch pattern
wget -q -O /tmp/kri_v2_s1.mbox "https://lore.kernel.org/alsa-devel/20260720103505.1860399-1-mstrozek@opensource.cirrus.com/raw"
# Verify: grep -c "^From mboxrd" /tmp/kri_v2_s1.mbox  must be >= 1

# For multi-patch: fetch each patch individually, concatenate for multi-mbox test
# OR use thread.mbox.gz: https://lore.kernel.org/netdev/20260724190811.198339-2-ansuelsmth@gmail.com/t.mbox.gz
```

### 5.3 Per-Sample Metadata Record

For every fetched patch record in the validation report:

```json
{
  "sample_id": "S3",
  "url": "https://lore.kernel.org/...",
  "message_id": "<20260724190811.198339-2-ansuelsmth@gmail.com>",
  "subject": "[PATCH net-next v2 1/6] ...",
  "subsystem": "drivers/net/phy/mediatek",
  "patch_count": 6,
  "expected_apply_status": "CLEAN",
  "baseline_ref": "v6.12",
  "baseline_commit": "2c97b9f3f",
  "actual_apply_status": null,
  "source_review_possible": null,
  "fetch_timestamp": "2026-07-25T..."
}
```

---

## 6. Validation Matrix

### 6.1 Per-Path Pass/Fail Criteria

#### Path A: CLI / TestClient

| Field | PASS | FAIL |
|-------|------|------|
| `evidence_status` on every comment | Present; value in valid set | Missing; or `supported` without evidence |
| `cfm_confidence` | Present (may be null on mode-off) | Absent when engine wired |
| `knowledge_state_id` in metadata | Present when `domain` set | Missing when domain set |
| `governance_warnings` in response | Present (may be `[]`) | Field absent entirely |
| `apply_status` in patch metadata | Present when `KRI_KERNEL_PATH` set | Missing when gate expected |
| Safety floor | BLOCKER/WARNING ≥ 0.70 always in output | Any such comment suppressed |
| Series context | In metadata when multi-patch | Missing for multi-patch |
| Checkpatch findings | In metadata when checkpatch runs | Exception raised |
| HTTP status | 200 | 4xx/5xx |
| JSON validity | Parseable | Unparseable |
| `APPLY_FAILED` samples | `apply_status.ok=False`; no blame evidence | Apply failure causes HTTP 500 |

#### Path B: Web/API

Same fields as Path A, verified via `POST /api/review/intelligent`. Additionally:
- API response `Content-Type: application/json`
- No unhandled exceptions in server stderr
- Consistent with TestClient output for same input

#### Path C: Browser / Playwright

| DOM Check | PASS | FAIL |
|-----------|------|------|
| Page loads | `<title>` present, no HTTP error | Browser exception |
| Textarea input works | Text accepted | Input rejected |
| Submit works | Button click triggers request | Button unresponsive |
| Results rendered | `#results` non-empty after submit | Still empty after 120s |
| Evidence badge | `.finding-card` with evidence span when `evidence_status != unknown` | Badge missing when evidence present in JSON |
| CFM shadow score | Text "shadow mode" present when CFM non-null | Missing when JSON has CFM score |
| Governance warnings | `<details>` with "Governance Violations" present when `governance_warnings` non-empty | Missing when violations in JSON |
| Knowledge state | `<code>` with truncated hash present when `knowledge_state_id` in metadata | Missing when JSON has ks_id |
| Multi-patch nav | Multiple `<h3>Patch:` entries when multi-patch | Only first patch shown |
| No JS console errors | Console error count == 0 | Any console.error logged |
| Apply status badge | ✅/⚠️/❌ apply badge present when `apply_status` in JSON | Missing |

#### Path D: Source-Level

| Check | PASS | FAIL |
|-------|------|------|
| `KRI_KERNEL_PATH` env var set | Set before review | Unset when source review expected |
| Patch applied to worktree | `git am` exit 0 | Exit non-zero |
| `apply_status.ok=True` in response | Present when apply succeeded | Missing |
| Blame evidence | `blame_backed` comments when blame data found | Blame data present but `evidence_status=unknown` |
| Source claims when APPLY_FAILED | None | Source-level finding despite failed apply |
| Worktree cleanup | `git worktree list` shows no kri_wt_* after run | Dangling worktrees |

### 6.2 Consistency Matrix

For each sample, fill:

| Feature | CLI | API | Browser | Consistent | Gap |
|---------|-----|-----|---------|------------|-----|
| evidence_status | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | description |
| cfm_confidence | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| knowledge_state_id | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| governance_warnings | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| blame_backed evidence | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| apply_status | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| reducer_actions | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| checkpatch_findings | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |
| source-level review | ✅/❌ | ✅/❌ | ✅/❌ | Y/N | |

Any cell where CLI=✅ but Browser=❌ is classified as `UI_SURFACING_GAP` and goes to Fix Agent.

---

## 7. CLI Validation Design

### 7.1 Entry Point

```python
from fastapi.testclient import TestClient
from kri.web.app import create_app
import os

os.environ["KRI_KERNEL_PATH"] = "/local/mnt/workspace/Linux_kernel_org/linux"
os.environ["KRI_SERIES_REDUCER_MODE"] = "shadow"  # activate reducer for coupling notes

client = TestClient(create_app(...))
```

### 7.2 Per-Sample Assertions

```python
def validate_cli(mbox: str, domain: str, expected: dict) -> ValidationResult:
    payload = {"mbox": mbox}
    if domain:
        payload["domain"] = domain
    resp = client.post("/api/review/intelligent", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    patches = data["patches"]

    # Mandatory field checks
    for pr in patches:
        for c in pr.get("inline_comments", []):
            assert "evidence_status" in c
            assert c["evidence_status"] in VALID_EVIDENCE_STATUS_VALUES
        assert "governance_warnings" in pr      # WP4-V field must be present
        
    # Conditional checks
    if expected.get("domain_set"):
        assert "knowledge_state_id" in data.get("metadata", {})
    if expected.get("apply_status_expected"):
        for pr in patches:
            assert "apply_status" in pr.get("metadata", {})
    if expected.get("apply_failed"):
        for pr in patches:
            assert pr["metadata"]["apply_status"]["ok"] == False
```

### 7.3 Source-Level Activation

```python
# For each sample, create a per-patch worktree before calling CLI
worktree = setup_worktree(KERNEL_PATH, baseline_ref, patch_mbox)
os.environ["KRI_KERNEL_PATH"] = worktree
resp = client.post(...)
teardown_worktree(KERNEL_PATH, worktree)
```

---

## 8. Web/API Validation Design

### 8.1 Server Launch

```python
import subprocess, time, requests, signal, os

def start_kri_server(port: int = 8765) -> subprocess.Popen:
    env = {**os.environ,
           "KRI_KERNEL_PATH": KERNEL_PATH,
           "KRI_SERIES_REDUCER_MODE": "shadow"}
    proc = subprocess.Popen(
        ["python", "-m", "uvicorn", "kri.web.app:app",
         "--host", "127.0.0.1", f"--port={port}", "--no-access-log"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(60):
        try:
            r = requests.get(f"http://127.0.0.1:{port}/", timeout=1)
            if r.status_code == 200:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("KRI server did not start within 30s")
```

### 8.2 API Schema Validation

```python
PATCH_REVIEW_REQUIRED_FIELDS = {
    "patch_id", "subject", "inline_comments", "governance_warnings", "metadata"
}
INLINE_COMMENT_REQUIRED_FIELDS = {
    "file_path", "line_number", "category", "severity", "message",
    "confidence", "evidence_status"
}

def validate_api_schema(data: dict) -> list[str]:
    gaps = []
    for pr in data.get("patches", []):
        missing = PATCH_REVIEW_REQUIRED_FIELDS - set(pr.keys())
        if missing:
            gaps.append(f"PatchReview missing fields: {missing}")
        for c in pr.get("inline_comments", []):
            missing_c = INLINE_COMMENT_REQUIRED_FIELDS - set(c.keys())
            if missing_c:
                gaps.append(f"InlineComment missing fields: {missing_c}")
    return gaps
```

### 8.3 CLI vs API Consistency Check

For each sample: run CLI validation, run API validation, compare field by field:
- `evidence_status` distribution must match within ±1 (reducer may differ by ordering)
- `knowledge_state_id` must match (deterministic blake2b hash)
- `governance_warnings` must match (same violation strings)
- `apply_status` must match (same boolean)

---

## 9. Browser Validation Design

### 9.1 Test Suite Structure (pytest-playwright)

```python
# tests/test_phase4v2_browser.py
import pytest
from playwright.sync_api import Page, expect

@pytest.fixture(scope="session")
def kri_server():
    proc = start_kri_server(port=8765)
    yield "http://127.0.0.1:8765"
    proc.terminate()

@pytest.fixture(scope="session")
def browser_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    ctx = browser.new_context()
    page = ctx.new_page()
    yield page
    browser.close()

def test_BW1_page_loads(browser_page, kri_server):
    browser_page.goto(kri_server)
    expect(browser_page).to_have_title(re.compile("KRI|Kernel Review"))
    assert browser_page.query_selector("textarea") is not None

def test_BW2_submit_returns_results(browser_page, kri_server, s1_mbox):
    browser_page.goto(kri_server)
    browser_page.fill("textarea", s1_mbox)
    browser_page.fill("input[placeholder*='domain']", "asoc")
    browser_page.click("button[type='submit']")
    browser_page.wait_for_function("document.getElementById('results').innerHTML.length > 100",
                                    timeout=120_000)
    html = browser_page.inner_html("#results")
    assert "Intelligent Review" in html or "Patch:" in html

def test_BW3_evidence_badge_rendered(browser_page, kri_server, s5_mbox):
    # S5 domain=asoc → rule_backed/blame_backed expected
    ...
    evidence_span = browser_page.query_selector("[style*='rule_backed']")
    assert evidence_span is not None

def test_BW4_cfm_shadow_rendered(browser_page, kri_server, s6_mbox):
    ...
    shadow_text = browser_page.get_by_text("shadow mode")
    expect(shadow_text).to_be_visible()

def test_BW5_governance_warnings_rendered(browser_page, kri_server, govwarn_mbox):
    # Use a mock that returns governance_warnings
    ...

def test_BW6_knowledge_state_rendered(browser_page, kri_server, s5_mbox):
    ...
    ks_code = browser_page.query_selector("code")
    assert ks_code is not None

def test_BW7_no_console_errors(browser_page, kri_server, s11_mbox):
    errors = []
    browser_page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    ...
    assert errors == [], f"Console errors: {errors}"

def test_BW8_multi_patch_navigation(browser_page, kri_server, s10_mbox):
    ...
    patch_headers = browser_page.query_selector_all("h3")
    assert len(patch_headers) >= 3

def test_BW9_apply_status_badge(browser_page, kri_server, s4_mbox):
    # With KRI_KERNEL_PATH set, apply badge expected
    ...
    apply_badge = browser_page.get_by_text(re.compile(r"Applies cleanly|Does not apply|unavailable"))
    expect(apply_badge).to_be_visible()

def test_BW10_error_is_user_friendly(browser_page, kri_server):
    # Submit invalid/empty input
    browser_page.goto(kri_server)
    browser_page.click("button[type='submit']")
    # Should show an error, not a raw Python traceback
    body = browser_page.inner_html("body")
    assert "Traceback" not in body
    assert "Internal Server Error" not in body or "error" in body.lower()
```

### 9.2 Graceful Browser Unavailability

```python
BROWSER_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        b.close()
    BROWSER_AVAILABLE = True
except Exception as e:
    BROWSER_UNAVAILABLE_REASON = str(e)

pytestmark = pytest.mark.skipif(
    not BROWSER_AVAILABLE,
    reason=f"BROWSER_AUTOMATION_BLOCKED: {BROWSER_UNAVAILABLE_REASON}"
)
```

When skipped, all other validation paths (CLI, API, source-level) must still run and pass.

---

## 10. Source-Level Validation Design

### 10.1 Worktree Setup Helper

```python
def setup_review_worktree(kernel_path: str, baseline_ref: str, patch_mbox: str) -> tuple[str, dict]:
    """Create a temporary worktree, apply patch, return (worktree_path, apply_result)."""
    import hashlib, subprocess
    wt_name = hashlib.blake2b(patch_mbox.encode(), digest_size=6).hexdigest()
    worktree = f"/tmp/kri_v2_wt_{wt_name}"
    
    subprocess.run(
        ["git", "-C", kernel_path, "worktree", "add", worktree, baseline_ref, "--quiet"],
        check=True
    )
    
    result = subprocess.run(
        ["git", "-C", worktree, "am", "--quiet", "--3way"],
        input=patch_mbox.encode(),
        capture_output=True
    )
    
    apply_status = {
        "ok": result.returncode == 0,
        "degraded": result.returncode != 0,
        "degraded_reason": result.stderr.decode()[:500] if result.returncode != 0 else None,
        "worktree": worktree,
    }
    
    if result.returncode != 0:
        # Abort failed am
        subprocess.run(["git", "-C", worktree, "am", "--abort"], capture_output=True)
    
    return worktree, apply_status


def teardown_review_worktree(kernel_path: str, worktree: str) -> None:
    subprocess.run(
        ["git", "-C", kernel_path, "worktree", "remove", "--force", worktree],
        capture_output=True
    )
```

### 10.2 Source-Level Invariants

```python
def validate_source_claims(response: dict, apply_status: dict) -> list[str]:
    violations = []
    if not apply_status["ok"]:
        # No blame_backed comments allowed when apply failed
        for pr in response.get("patches", []):
            for c in pr.get("inline_comments", []):
                if c.get("evidence_status") == "blame_backed":
                    violations.append(
                        f"FORBIDDEN: blame_backed at {c['file_path']}:{c['line_number']} "
                        f"but patch did not apply"
                    )
    return violations
```

### 10.3 Blame Activation Test (S8)

S8 (mm/pgtable patch) is the primary blame activation test:

```bash
# With KRI_KERNEL_PATH pointing to worktree where patch applied cleanly:
# - reviewer calls _enrich_with_blame()
# - mm/memory.c and include/linux/pgtable.h have rich blame history
# - expect blame_backed on comments touching modified lines
# - if domain not set → blame path not called (mode-off guard)
# Use domain=asoc for this test? No — asoc rules won't match mm/ files.
# The evidence engine is activated by domain arg, but rules are subsystem-specific.
# With domain=asoc and mm/ patch: EvidenceEngine runs, no ASoC rules match,
# blame enrichment may still run (check _enrich_with_blame call path in WP4-E).
```

**Design note for implementation phase:** Check whether `_enrich_with_blame()` is gated only on `domain` being set or on subsystem match. If gated on domain only, `domain=asoc` on an mm/ patch will attempt blame (and may produce blame_backed). If gated on subsystem, an mm/ domain arg would be needed. This must be verified by CLI Validation Agent during execution.

---

## 11. Review Quality Design

### 11.1 Comment Classification Rubric

For each KRI inline comment produced on each sample:

| Class | Criteria |
|-------|---------|
| **surgical** | References specific function/line/symbol in the diff; not reproducible from diff alone without domain knowledge |
| **useful_generic** | Correct observation applicable to this file/subsystem; reasonable guidance; not file-specific |
| **evidence_backed** | `evidence_status` is `rule_backed` or `blame_backed`; evidence is from a real domain source |
| **plausible_unsupported** | `evidence_status=evidence_missing` or `unknown`; comment is factually plausible but no evidence |
| **false_positive** | Comment claims an issue that does not exist in the diff; verifiable error |
| **reducer_candidate** | Duplicate of another comment in the same series; reducer should have suppressed it |
| **missing_issue** | A real issue visible in the diff that KRI did not comment on (flagged by Review Quality Agent) |
| **unsuitable** | Too generic to be useful in a real lore review; could apply to any patch |

### 11.2 Lore-Style Fitness Score

For each sample, compute:

```
quality_score = (surgical + evidence_backed) / total_comments
lore_fitness = "HIGH" if quality_score >= 0.6
             | "MEDIUM" if quality_score >= 0.3
             | "LOW" otherwise
```

Target: at least 50% of comments classified `surgical` or `evidence_backed` for domain=asoc patches.

### 11.3 False Positive Threshold

```
fp_rate = false_positive / total_comments
FAIL if fp_rate > 0.20  # more than 1-in-5 comments is wrong
```

---

## 12. User-Friendliness Evaluation

For each sample's browser output, Review Quality Agent evaluates:

| Check | PASS | FAIL |
|-------|------|------|
| User understands what happened | Clear summary/disclaimer visible | Wall of JSON / no narrative |
| Apply status visible | ✅/⚠️/❌ badge with human-readable text | No apply info when available |
| Evidence status visible | Color-coded badge per comment | Evidence info hidden |
| CFM shadow score visible | Score % with "(shadow mode)" label | Score hidden or unlabeled |
| Governance warnings visible | Red collapsible section when violations exist | Silent |
| Source vs diff distinction | Apply status clearly indicates source-aware | No distinction |
| Error messages | Human-readable, no stack traces in browser | Raw Python traceback shown |
| Long review readable | Sections collapsible; not one massive block | Unreadable wall of text |
| Multi-patch navigable | Each patch in its own section with header | All patches merged |
| Looks like kernel review | File:line:category structure; code context shown | Generic AI text |

---

## 13. Autonomous Fix Policy

### 13.1 In-Scope Fixes

| Gap Type | Fix Action |
|----------|-----------|
| `UI_SURFACING_GAP`: backend field not in UI | Add JS rendering in `renderIntelligent()` |
| API field missing from response | Add Pydantic field + serialization |
| Browser test missing | Add pytest-playwright test |
| CLI/API/browser inconsistency test missing | Add consistency assertion test |
| `KRI_KERNEL_PATH` not propagated to server | Fix env var forwarding in uvicorn launch |
| Apply status not rendered in browser | Add apply_status JS rendering if missing |
| User-unfriendly error message | Fix error handler in app.py |
| `APPLY_FAILED` not in response when it should be | Fix applicability gate response |

### 13.2 Out-of-Scope (Forbidden) Fixes

- Track-B: lore ingestion, Pattern node creation, CFM production gate
- CFM weight changes
- New DKP rules without separate approval
- Fabricating evidence to make tests pass
- Bypassing safety floor to reduce comments
- Changing mode-off behavior

### 13.3 Fix Governance

Every fix uses the same 5-agent governance model:
1. Agent 1 (Implementer): implements fix
2. Agent 2 (Adversarial): tries to break it
3. Agent 3 (Constitution): checks Sec-40, safety floor, Track-B scope
4. Agent 4 (Test Auditor): verifies test completeness
5. Agent 5 (Arbiter): authorizes commit

MAX_REWORK_ATTEMPTS = 3. Commit only after AUTHORIZED_TO_COMMIT.

---

## 14. STOP Conditions

| Condition | Action |
|-----------|--------|
| Browser automation changes production behavior | STOP immediately; revert |
| CFM becomes production gate | STOP immediately |
| Track-B ingestion accidentally starts | STOP; audit all changes |
| Pattern nodes created | STOP |
| Lore data persisted into EKG | STOP |
| Safety floor bypassed | STOP |
| Evidence marked `supported` without provenance | STOP |
| Source-level claims when patch did not apply | STOP; fix + revalidate |
| mode=off behavior changes | STOP |
| Sec-40 nondeterminism introduced | STOP |
| Kernel source working tree corrupted | STOP; assess damage |
| Tool installation modifies project deps without approval | STOP; revert |
| Full suite regresses; 3 fix attempts exceeded | STOP; escalate |

---

## 15. Execution Order

```
Phase A: Setup (parallel where independent)
  A1. Playwright install: playwright install --only-shell chromium
  A2. Kernel source fetch: git -C /local/mnt/workspace/Linux_kernel_org/linux fetch origin --tags
  A3. Lore Acquisition: fetch all 12 samples; record metadata
  A4. Record KERNEL_BASELINE_COMMIT (HEAD after fetch)

Phase B: Validation (per-sample, agents parallel within each sample)
  For each sample S1..S12:
    B1. Kernel Source Agent: create worktree at baseline, apply patch, record apply status
    B2. CLI Validation Agent: run TestClient with KRI_KERNEL_PATH=worktree
    B3. Web/API Agent: run uvicorn, POST to API, validate JSON schema
    B4. Browser Agent: drive Playwright, validate DOM assertions
    B5. Kernel Source Agent: teardown worktree
    B6. Review Quality Agent: classify each comment per rubric
    B7. UI/UX Agent: evaluate browser output for user-friendliness
    → Validation Architect: record results, identify gaps

Phase C: Gap Resolution (sequential, governance-gated)
  For each gap found in B:
    C1. Arbiter: classify as in-scope / out-of-scope
    C2. If in-scope: Fix Agent implements → 5-agent governance → commit → push
    C3. CLI + API + Browser validation re-run for affected samples
    C4. Governance Auditor: final check

Phase D: Reporting
  D1. Consistency matrix filled
  D2. Review quality scores computed
  D3. docs/PHASE4_REAL_WORLD_VALIDATION_REPORT_V2_2026-07-25.md written
  D4. Committed and pushed
```

---

## 16. Expected Reports

| Report | Path | Contents |
|--------|------|---------|
| This design document | `docs/PHASE4V2_BROWSER_SOURCE_VALIDATION_DESIGN_2026-07-25.md` | Agent model, tooling plan, kernel source policy, patch acquisition strategy, validation matrix |
| Validation report V2 | `docs/PHASE4_REAL_WORLD_VALIDATION_REPORT_V2_2026-07-25.md` | All 12 samples × 4 paths; consistency matrix; quality scores; gap list; fix list |

---

## 17. GO / NO-GO Decision

### Environment Facts at Design Time

| Fact | Status |
|------|--------|
| Playwright Python installed | ✅ v1.59.0 |
| Playwright Chromium binary | ❌ MISSING — needs `playwright install --only-shell chromium` |
| Kernel source at canonical path | ✅ `/local/mnt/workspace/Linux_kernel_org/linux` (mainline, HEAD=6.18-rc1) |
| KRI_KERNEL_PATH set | ❌ UNSET — must set before each validation run |
| Disk space for browser install | ✅ 1.8T available |
| lore patches from Phase-4V | ✅ `/tmp/c*.mbox` (10 of 12 samples covered) |
| Kernel worktree tooling | ✅ git worktree available |
| KRI test suite | ✅ 615 tests, 0 failures |
| Track-B authorized | ❌ NOT authorized |

### Blockers

| Blocker | Severity | Resolution |
|---------|----------|-----------|
| Playwright browser binary missing | MEDIUM | `playwright install --only-shell chromium` (one command, ~150MB, no project dep change) |
| KRI_KERNEL_PATH unset | LOW | Set env var at session start |
| S6 apply-failure patch URL TBD | LOW | Lore Acquisition Agent fetches at runtime |

### Recommendation

**`GO_WITH_TOOLING_INSTALL_FIRST`**

Both blockers are resolved by environment setup commands, not code changes:

```bash
# Step 1: Install browser binary (one-time, ~150MB, ~30s)
playwright install --only-shell chromium

# Step 2: Set kernel path for all subsequent runs
export KRI_KERNEL_PATH=/local/mnt/workspace/Linux_kernel_org/linux
```

After these two commands, all 4 validation paths are unblocked. No project dependency changes. No Track-B. No safety floor risk.

### Execution Prompt for Phase-4V2 Autonomous Run

When user approves, use this exact prompt:

```
PHASE-4V2 AUTONOMOUS BROWSER + SOURCE-LEVEL VALIDATION — EXECUTE

Pre-conditions confirmed:
- playwright install --only-shell chromium has been run (or will be run as first action)
- KRI_KERNEL_PATH=/local/mnt/workspace/Linux_kernel_org/linux
- Design document: docs/PHASE4V2_BROWSER_SOURCE_VALIDATION_DESIGN_2026-07-25.md

Standing constraints (verbatim from prior sessions):
- Real git identity: Ajay Kumar Nandam <ajay.nandam@oss.qualcomm.com>
- No squash. No placeholder info.
- Direct push to main authorized after commit sequence.
- No over-engineering.
- TLS: no verify=False outside LLMClient.
- Sec-40: no random/time.*/datetime.now/uuid outside kri/learning/.
- Safety floor: blockers + warnings @ confidence >= 0.70 never suppressed.
- Never git add -A. Stage by explicit filename.
- Track-B NOT authorized: no lore ingestion, no Pattern nodes, no CFM gate.

Execute Phase-4V2 per design doc:
1. Run playwright install --only-shell chromium
2. Fetch kernel updates (git -C $KRI_KERNEL_PATH fetch --tags)
3. Acquire all 12 patch samples from lore.kernel.org
4. For each sample: worktree setup → CLI validation → API validation → browser validation → quality assessment → worktree teardown
5. For each gap found: Fix Agent with 5-agent governance → commit → push
6. Write validation report V2
7. Commit report and push

Autonomous execution. Do not pause between samples. Continue until all 12 samples validated OR STOP condition triggered.
```
