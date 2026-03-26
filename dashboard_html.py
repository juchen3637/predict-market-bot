"""
dashboard_html.py — HTML scaffold for the predict-market-bot dashboard.

Kept in a separate module to keep dashboard_server.py under 800 lines.
CSS is in dashboard_assets.py, JS is in dashboard_js.py.
"""

from __future__ import annotations

from dashboard_assets import _CSS
from dashboard_js import _JS


def render_dashboard(data: dict) -> str:
    """Render the full dashboard HTML given a pre-built data dict."""
    computed_at = data.get("metrics_snapshot_at", "never")
    now = data.get("updated_at", "")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>predict-market-bot</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@100..900&family=Geist+Mono:wght@100..900&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">
<style>{_CSS}</style>
</head>
<body>

<!-- ============ SIDEBAR ============ -->
<aside class="sidebar">
  <div class="sidebar-brand">
    <h1>Predict Market</h1>
    <p class="sidebar-version">v2.2.0-beta</p>
  </div>
  <nav class="sidebar-nav">
    <a class="sidebar-item" id="nav-scan" onclick="switchView('scan')">
      <span class="material-symbols-outlined">search_insights</span>
      <span class="item-label">Scan</span>
      <span class="stage-indicator" id="stage-ind-scan"></span>
    </a>
    <a class="sidebar-item" id="nav-research" onclick="switchView('research')">
      <span class="material-symbols-outlined">biotech</span>
      <span class="item-label">Research</span>
      <span class="stage-indicator" id="stage-ind-research"></span>
    </a>
    <a class="sidebar-item" id="nav-predict" onclick="switchView('predict')">
      <span class="material-symbols-outlined">online_prediction</span>
      <span class="item-label">Predict</span>
      <span class="stage-indicator" id="stage-ind-predict"></span>
    </a>
    <a class="sidebar-item" id="nav-risk" onclick="switchView('risk')">
      <span class="material-symbols-outlined">gavel</span>
      <span class="item-label">Risk</span>
      <span class="stage-indicator" id="stage-ind-risk"></span>
    </a>
    <a class="sidebar-item" id="nav-postmortem" onclick="switchView('postmortem')">
      <span class="material-symbols-outlined">assessment</span>
      <span class="item-label">Post Mortem</span>
    </a>
  </nav>

  <div class="sidebar-section-label">Run History</div>
  <div class="run-list-scroll" id="pipeline-run-list">
    <div style="padding:8px 16px;font-size:0.7rem;color:#64748b">Loading&hellip;</div>
  </div>

  <div class="sidebar-footer">
    <span id="conn-status" class="conn-reconnecting">&#9700; Connecting&hellip;</span>
    <span class="timestamp">Snapshot: {computed_at}</span>
  </div>
</aside>

<!-- ============ TOP HEADER ============ -->
<header class="top-header">
  <div class="header-left">
    <span class="header-title">PIPELINE TERMINAL</span>
    <span class="header-mode" id="header-mode-label">Scanner Mode</span>
  </div>
  <div class="header-right">
    <span id="last-updated" class="timestamp">Updated {now}</span>
    <div class="mode-toggle" id="mode-toggle" style="display:none">
      <button id="toggle-paper" class="mode-btn active" onclick="switchMode('paper')">Paper</button>
      <button id="toggle-live"  class="mode-btn"        onclick="switchMode('live')">Live</button>
    </div>
  </div>
</header>

<!-- ============ MAIN ============ -->
<main class="main-content">

  <!-- ===== SCAN view ===== -->
  <div id="view-scan" class="view" style="display:none">
    <div class="view-header">
      <h2>Market Scanner</h2>
      <div class="view-meta">
        <span class="live-dot"></span>
        <span>Live filter results</span>
        <span id="scan-meta" style="color:#64748b"></span>
      </div>
    </div>
    <div class="stat-grid stat-grid-3" style="max-width:600px">
      <div class="stat-card">
        <div class="stat-label">Kalshi Markets</div>
        <div class="stat-number teal" id="scan-stat-kalshi">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Polymarket Markets</div>
        <div class="stat-number teal" id="scan-stat-poly">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total Candidates</div>
        <div class="stat-number white" id="scan-stat-total">&mdash;</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap">
      <span class="filter-chip">Volume &ge; 200</span>
      <span class="filter-chip">OI &ge; 50</span>
      <span class="filter-chip">Expiry &le; 30d</span>
      <span class="filter-chip muted">Anomaly flagging on</span>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Filtered Markets</span></div>
      <table>
        <thead><tr>
          <th>Market ID</th><th>Platform</th><th>Category</th>
          <th>Yes Price</th><th>Volume 24h</th><th>OI</th><th>Days Left</th><th>Anomalies</th>
        </tr></thead>
        <tbody id="scan-tbody">
          <tr><td colspan="8" class="empty-row">Loading&hellip;</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ===== RESEARCH view ===== -->
  <div id="view-research" class="view" style="display:none">
    <div class="view-header">
      <h2>Research Pipeline</h2>
      <div class="view-meta"><span class="live-dot"></span><span>Sentiment &amp; gap analysis</span></div>
    </div>
    <div class="stat-grid stat-grid-4">
      <div class="stat-card">
        <div class="stat-label">Cache Hits</div>
        <div class="stat-number green" id="res-stat-cache">&mdash;</div>
        <div class="stat-sub">4h TTL</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Fresh Fetches</div>
        <div class="stat-number teal" id="res-stat-fresh">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Low Confidence</div>
        <div class="stat-number amber" id="res-stat-lowconf">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Skipped</div>
        <div class="stat-number muted" id="res-stat-skip">&mdash;</div>
      </div>
    </div>
    <div class="research-detail-card">
      <div class="detail-header">
        <div style="font-size:0.65rem;font-family:'Geist Mono',monospace;color:#64748b;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px">Selected Market</div>
        <div style="font-size:1rem;font-weight:500;color:#f1f5f9" id="research-detail-title">&mdash;</div>
      </div>
      <div id="research-detail-content">
        <div style="padding:24px;color:#64748b;font-size:0.8rem">Select a market to see details</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Research Queue</span></div>
      <table>
        <thead><tr>
          <th>Market ID</th><th>Sentiment</th><th>Confidence</th><th>Status</th>
        </tr></thead>
        <tbody id="research-tbody">
          <tr><td colspan="4" class="empty-row">Loading&hellip;</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ===== PREDICT view ===== -->
  <div id="view-predict" class="view" style="display:none">
    <div class="view-header">
      <h2>Predict Pipeline</h2>
      <div class="view-meta"><span class="live-dot"></span><span>LLM ensemble + XGBoost</span></div>
    </div>
    <div class="stat-grid stat-grid-4">
      <div class="stat-card">
        <div class="stat-label">Signaled</div>
        <div class="stat-number green" id="pred-stat-signaled">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Skipped</div>
        <div class="stat-number muted" id="pred-stat-skipped">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Cache Hits</div>
        <div class="stat-number teal" id="pred-stat-cache">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Avg Edge</div>
        <div class="stat-number green" id="pred-stat-edge">&mdash;</div>
      </div>
    </div>
    <div class="ensemble-grid">
      <div class="ensemble-col" id="ensemble-claude">
        <div class="ensemble-label">Claude Sonnet</div>
        <div class="ensemble-prob" style="color:#94a3b8">&mdash;</div>
      </div>
      <div class="ensemble-col" id="ensemble-gpt">
        <div class="ensemble-label">GPT-4o Mini</div>
        <div class="ensemble-prob" style="color:#94a3b8">&mdash;</div>
      </div>
      <div class="ensemble-col" id="ensemble-gemini">
        <div class="ensemble-label">Gemini Flash</div>
        <div class="ensemble-prob" style="color:#94a3b8">&mdash;</div>
      </div>
      <div class="ensemble-col consensus" id="ensemble-consensus">
        <div class="ensemble-label">Weighted Consensus</div>
        <div class="ensemble-prob">&mdash;</div>
      </div>
    </div>
    <div class="market-card">
      <div class="market-card-tag" id="predict-market-tag">Active Market</div>
      <h3 id="predict-market-title">Run the pipeline to see predictions</h3>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Signaled Markets</span></div>
      <table>
        <thead><tr>
          <th>Market ID</th><th>p_model</th><th>Edge</th>
          <th>Direction</th><th>Ensemble</th><th>Cache</th>
        </tr></thead>
        <tbody id="predict-tbody">
          <tr><td colspan="6" class="empty-row">Loading&hellip;</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ===== RISK view ===== -->
  <div id="view-risk" class="view" style="display:none">
    <div class="view-header">
      <h2>Risk Pipeline</h2>
      <div class="view-meta"><span class="live-dot"></span><span>Kelly sizing &amp; position limits</span></div>
    </div>
    <div class="stat-grid stat-grid-4">
      <div class="stat-card">
        <div class="stat-label">Wins (Resolved)</div>
        <div class="stat-number green" id="risk-stat-approved">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Losses</div>
        <div class="stat-number red" id="risk-stat-blocked">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Open Positions</div>
        <div class="stat-number teal" id="risk-stat-open">&mdash;</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Max Drawdown</div>
        <div class="stat-number amber" id="risk-stat-drawdown">&mdash;</div>
        <div class="stat-sub">Target: &lt;8%</div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Open Positions</span>
        <span class="bdg bdg-live">LIVE</span>
      </div>
      <table>
        <thead><tr>
          <th>Market ID</th><th>Title</th><th>Direction</th>
          <th>Size</th><th>Entry</th><th>Edge</th><th>Status</th>
        </tr></thead>
        <tbody id="risk-open-tbody">
          <tr><td colspan="7" class="empty-row">No open positions</td></tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Recent Resolved Trades</span></div>
      <table>
        <thead><tr>
          <th>Market ID</th><th>Title</th><th>Size</th>
          <th>Outcome</th><th>P&amp;L</th><th>Resolved</th>
        </tr></thead>
        <tbody id="risk-resolved-tbody">
          <tr><td colspan="6" class="empty-row">No resolved trades yet</td></tr>
        </tbody>
      </table>
    </div>
    <div class="system-info-bar">
      <span class="material-symbols-outlined">info</span>
      <p>Kelly fraction: 0.25x &bull; Max position: $5 &bull; Drawdown kill-switch: 15% &bull; Min edge: 4%</p>
    </div>
  </div>

  <!-- ===== POST MORTEM view ===== -->
  <div id="view-postmortem" class="view" style="display:none">
    <div class="view-header">
      <h2>Post Mortem</h2>
      <div class="view-meta">
        <span>Nightly consolidation &bull; Snapshot: {computed_at}</span>
      </div>
    </div>
    <div class="stat-grid stat-grid-5">
      <div class="stat-card">
        <div class="stat-label">Win Rate</div>
        <div class="stat-number green" id="pm-stat-wr">&mdash;</div>
        <div class="stat-sub">Target: &gt;60%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Sharpe Ratio</div>
        <div class="stat-number white" id="pm-stat-sharpe">&mdash;</div>
        <div class="stat-sub">Target: &gt;2.0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Brier Score</div>
        <div class="stat-number green" id="pm-stat-brier">&mdash;</div>
        <div class="stat-sub">Target: &lt;0.25</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Max Drawdown</div>
        <div class="stat-number amber" id="pm-stat-dd">&mdash;</div>
        <div class="stat-sub">Target: &lt;8%</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Profit Factor</div>
        <div class="stat-number green" id="pm-stat-pf">&mdash;</div>
        <div class="stat-sub">Target: &gt;1.5</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px">
      <div class="chart-panel">
        <h3 style="margin-bottom:20px">Cumulative P&amp;L</h3>
        <svg id="pnl-chart-svg" viewBox="0 0 400 200" style="width:100%;height:200px">
          <text x="200" y="110" text-anchor="middle" fill="#64748b" font-family="Geist Mono,monospace" font-size="12">No resolved trades yet</text>
        </svg>
      </div>
      <div class="chart-panel">
        <h3 style="margin-bottom:20px">Win Rate by Category</h3>
        <div class="category-bars" id="category-bars">
          <p style="color:#64748b;font-size:0.8rem">No category data yet</p>
        </div>
      </div>
    </div>
    <div class="system-info-bar">
      <span class="material-symbols-outlined">psychology</span>
      <p>XGBoost retrains nightly after 10+ resolved trades &bull; Failure patterns fed back into next scan cycle</p>
    </div>
  </div>

</main>

<script>{_JS}</script>
</body>
</html>"""
