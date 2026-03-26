"""
dashboard_assets.py — CSS for dashboard_server.py.

Kept in a separate module to keep dashboard_server.py under 800 lines.
JS is in dashboard_js.py.
"""

_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1117; color: #f1f5f9; }
.material-symbols-outlined { font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
  vertical-align: middle; line-height: 1; }

/* === SIDEBAR === */
.sidebar { position: fixed; left: 0; top: 0; width: 220px; height: 100vh;
  background: #1a1f2e; border-right: 1px solid #252b3b;
  display: flex; flex-direction: column; z-index: 50; overflow-y: auto; }
.sidebar-brand { padding: 24px; border-bottom: 1px solid #252b3b; flex-shrink: 0; }
.sidebar-brand h1 { font-size: 1.1rem; font-weight: 600; color: #f1f5f9; }
.sidebar-version { font-size: 0.6rem; font-family: 'Geist Mono', monospace;
  color: #14b8a6; opacity: 0.7; margin-top: 2px; }
.sidebar-nav { padding: 8px 0; flex-shrink: 0; }
.sidebar-item { display: flex; align-items: center; gap: 12px; padding: 12px 16px;
  font-size: 0.875rem; color: #64748b; cursor: pointer; transition: all 0.15s;
  border-left: 2px solid transparent; text-decoration: none; user-select: none; }
.sidebar-item:hover { color: #f1f5f9; background: #0f1117; }
.sidebar-item.active { color: #14b8a6; border-left-color: #14b8a6;
  background: rgba(20,184,166,0.08); font-weight: 500; }
.sidebar-item .material-symbols-outlined { font-size: 1.1rem; flex-shrink: 0; }
.sidebar-item .item-label { flex: 1; }
.stage-indicator { display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; flex-shrink: 0; }
.stage-indicator .material-symbols-outlined { font-size: 0.9rem; color: #22c55e; }
.stage-indicator .pulse-dot { width: 6px; height: 6px; border-radius: 50%;
  background: #14b8a6; animation: pulse-dot 1.4s ease-in-out infinite; }

/* === TOP HEADER === */
.top-header { position: fixed; top: 0; left: 220px; right: 0; height: 64px;
  background: rgba(15,17,23,0.92); backdrop-filter: blur(12px);
  border-bottom: 1px solid #252b3b;
  display: flex; justify-content: space-between; align-items: center;
  padding: 0 24px; z-index: 40; }
.header-left { display: flex; align-items: center; gap: 0; }
.header-title { font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  color: #64748b; text-transform: uppercase; letter-spacing: 0.15em; }
.header-mode { font-family: 'Geist Mono', monospace; font-size: 0.8rem;
  font-weight: 700; color: #14b8a6; text-transform: uppercase; letter-spacing: 0.1em;
  margin-left: 16px; padding-left: 16px; border-left: 1px solid #252b3b; }
.header-right { display: flex; align-items: center; gap: 16px; }

/* === MAIN CONTENT === */
.main-content { margin-left: 220px; padding-top: 64px; min-height: 100vh; }
.view { padding: 32px; max-width: 1200px; }
.view-header { margin-bottom: 32px; }
.view-header h2 { font-size: 1.875rem; font-weight: 600; color: #f1f5f9; }
.view-meta { display: flex; align-items: center; gap: 8px; margin-top: 6px;
  font-size: 0.875rem; color: #94a3b8; flex-wrap: wrap; }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: #14b8a6;
  animation: pulse-dot 1.4s ease-in-out infinite; display: inline-block; }

/* === STAT CARDS === */
.stat-grid { display: grid; gap: 16px; margin-bottom: 32px; }
.stat-grid-3 { grid-template-columns: repeat(3, 1fr); }
.stat-grid-4 { grid-template-columns: repeat(4, 1fr); }
.stat-grid-5 { grid-template-columns: repeat(5, 1fr); }
.stat-card { background: #1a1f2e; border: 1px solid #252b3b; border-radius: 8px; padding: 16px; }
.stat-label { font-size: 0.65rem; font-weight: 600; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 6px; }
.stat-number { font-family: 'Geist Mono', monospace; font-size: 2rem;
  font-weight: 700; line-height: 1; }
.stat-sub { font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  color: #64748b; margin-top: 6px; }
.stat-number.green { color: #22c55e; }
.stat-number.teal  { color: #14b8a6; }
.stat-number.amber { color: #f59e0b; }
.stat-number.red   { color: #ef4444; }
.stat-number.muted { color: #64748b; }
.stat-number.white { color: #f1f5f9; }

/* === PANELS === */
.panel { background: #1a1f2e; border: 1px solid #252b3b; border-radius: 8px;
  overflow: hidden; margin-bottom: 24px; }
.panel-header { padding: 16px 24px; border-bottom: 1px solid #252b3b;
  display: flex; justify-content: space-between; align-items: center; }
.panel-title { font-size: 0.875rem; font-weight: 600; color: #f1f5f9; }

/* === TABLES === */
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
thead tr { background: rgba(15,17,23,0.5); border-bottom: 1px solid #252b3b; }
th { padding: 12px 24px; font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  font-weight: 600; color: #64748b; text-transform: uppercase;
  letter-spacing: 0.1em; text-align: left; white-space: nowrap; }
td { padding: 14px 24px; border-bottom: 1px solid rgba(37,43,59,0.5); }
tr:last-child td { border-bottom: none; }
tr:hover td { background: rgba(37,43,59,0.3); }
.td-mono { font-family: 'Geist Mono', monospace; font-size: 0.75rem; color: #e2e8f0; }
.td-teal  { font-family: 'Geist Mono', monospace; color: #14b8a6; }
.td-green { font-family: 'Geist Mono', monospace; color: #22c55e; }
.td-red   { font-family: 'Geist Mono', monospace; color: #ef4444; }
.empty-row { text-align: center; color: #64748b; padding: 32px !important; }

/* === BADGES === */
.bdg { display: inline-flex; align-items: center; padding: 2px 8px;
  border-radius: 4px; font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; }
.bdg-kalshi     { background: rgba(20,184,166,0.1); color: #14b8a6; border: 1px solid rgba(20,184,166,0.2); }
.bdg-polymarket { background: rgba(168,85,247,0.1); color: #a855f7; border: 1px solid rgba(168,85,247,0.2); }
.bdg-buy        { background: rgba(34,197,94,0.1);  color: #22c55e; border: 1px solid rgba(34,197,94,0.2); }
.bdg-sell       { background: rgba(239,68,68,0.1);  color: #ef4444; border: 1px solid rgba(239,68,68,0.2); }
.bdg-optimal    { background: rgba(34,197,94,0.1);  color: #22c55e; border-radius: 20px; border: 1px solid rgba(34,197,94,0.2); }
.bdg-lowconf    { background: rgba(245,158,11,0.1); color: #f59e0b; border-radius: 20px; border: 1px solid rgba(245,158,11,0.2); }
.bdg-skipped    { background: rgba(100,116,139,0.1); color: #64748b; border-radius: 20px; border: 1px solid rgba(100,116,139,0.2); }
.bdg-pass { color: #22c55e; font-family: 'Geist Mono', monospace; font-size: 0.7rem; font-weight: 700; }
.bdg-fail { color: #ef4444; font-family: 'Geist Mono', monospace; font-size: 0.7rem; font-weight: 700; }
.bdg-live { background: rgba(20,184,166,0.1); color: #14b8a6;
  border: 1px solid rgba(20,184,166,0.2); font-size: 0.6rem; padding: 1px 5px; border-radius: 3px; }
.anomaly-vol   { background: rgba(245,158,11,0.1); color: #f59e0b;
  border: 1px solid rgba(245,158,11,0.2); border-radius: 20px; padding: 2px 8px;
  font-size: 0.65rem; font-family: 'Geist Mono', monospace; white-space: nowrap; }
.anomaly-price { background: rgba(239,68,68,0.1); color: #ef4444;
  border: 1px solid rgba(239,68,68,0.2); border-radius: 20px; padding: 2px 8px;
  font-size: 0.65rem; font-family: 'Geist Mono', monospace; white-space: nowrap; }
.sentiment-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }

/* === MODE TOGGLE === */
.mode-toggle { display: flex; border: 1px solid #252b3b; border-radius: 6px; overflow: hidden; }
.mode-btn { padding: 4px 16px; font-size: 0.75rem; font-weight: 600; background: none;
  border: none; color: #64748b; cursor: pointer; transition: all 0.15s; }
.mode-btn.active { background: #14b8a6; color: #0f1117; }

/* === MODEL AGREEMENT CHIPS === */
.model-chip { width: 18px; height: 18px; border-radius: 3px; display: inline-flex;
  align-items: center; justify-content: center; font-family: 'Geist Mono', monospace;
  font-size: 0.6rem; font-weight: 700; }
.model-chip.agreed    { background: rgba(20,184,166,0.2); color: #14b8a6; }
.model-chip.disagreed { background: #252b3b; color: #64748b; }

/* === ENSEMBLE BENTO (PREDICT) === */
.ensemble-grid { display: grid; grid-template-columns: repeat(4, 1fr);
  border: 1px solid #252b3b; border-radius: 8px; overflow: hidden; margin-bottom: 24px;
  background: #1a1f2e; }
.ensemble-col { padding: 24px; }
.ensemble-col + .ensemble-col { border-left: 1px solid #252b3b; }
.ensemble-col.consensus { background: rgba(20,184,166,0.05); border-left: 4px solid #14b8a6 !important; }
.ensemble-label { font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  color: #64748b; text-transform: uppercase; margin-bottom: 16px; }
.ensemble-prob { font-family: 'Geist Mono', monospace; font-size: 2.25rem;
  font-weight: 700; color: #e2e8f0; }
.ensemble-sub  { font-family: 'Geist Mono', monospace; font-size: 0.6rem;
  color: #64748b; margin-top: 8px; }

/* === ACTIVE MARKET CARD === */
.market-card { background: #252b3b; border: 1px solid #374151; border-radius: 8px;
  padding: 24px; margin-bottom: 24px; position: relative; overflow: hidden; }
.market-card::before { content: ''; position: absolute; top: -32px; right: -32px;
  width: 96px; height: 96px; background: rgba(20,184,166,0.05);
  border-radius: 50%; filter: blur(24px); pointer-events: none; }
.market-card-tag { font-family: 'Geist Mono', monospace; font-size: 0.65rem;
  color: #94a3b8; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; }
.market-card h3 { font-size: 1.1rem; color: #f1f5f9; font-weight: 500; line-height: 1.4; }

/* === FILTER CHIPS === */
.filter-chip { background: rgba(20,184,166,0.1); color: #14b8a6;
  font-family: 'Geist Mono', monospace; font-size: 0.65rem; padding: 4px 10px;
  border-radius: 20px; border: 1px solid rgba(20,184,166,0.3); }
.filter-chip.muted { background: #1a1f2e; color: #64748b;
  border-color: #252b3b; }

/* === PROGRESS BARS === */
.progress-bar { width: 100%; height: 6px; background: #252b3b; border-radius: 999px; overflow: hidden; }
.progress-fill { height: 100%; border-radius: 999px; background: #14b8a6; }
.progress-fill.green { background: #22c55e; }
.progress-fill.amber { background: #f59e0b; }

/* === RISK GATE CARDS === */
.gate-card { background: #1a1f2e; border: 1px solid #252b3b; border-radius: 8px;
  overflow: hidden; display: flex; flex-direction: column; }
.gate-card-header { padding: 16px 20px; border-bottom: 1px solid #252b3b;
  display: flex; justify-content: space-between; align-items: flex-start; }
.gate-card-body { padding: 16px 20px; flex: 1; }
.gate-card-footer { padding: 16px 20px; background: rgba(37,43,59,0.3);
  border-top: 1px solid #252b3b; }
.gate-row { display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; border-bottom: 1px solid rgba(37,43,59,0.4); font-size: 0.82rem; }
.gate-row:last-child { border-bottom: none; }
.gate-label { display: flex; align-items: center; gap: 8px; color: #94a3b8; }
.gate-label .material-symbols-outlined { font-size: 1.1rem; }
.gate-label.pass-icon .material-symbols-outlined { color: #22c55e; }
.gate-label.fail-icon .material-symbols-outlined { color: #ef4444; }
.gate-card-approved { border-color: rgba(34,197,94,0.3) !important; }
.gate-card-blocked  { border-color: rgba(239,68,68,0.3) !important; }
.gate-locked { border: 1px dashed #252b3b; border-radius: 6px; padding: 20px;
  display: flex; flex-direction: column; align-items: center; gap: 8px; opacity: 0.4; margin-top: 8px; }

/* === PNL CHART === */
.chart-panel { background: #1a1f2e; border: 1px solid #252b3b; border-radius: 8px; padding: 24px; }
.chart-panel h3 { font-size: 0.875rem; font-weight: 600; color: #f1f5f9; }

/* === CATEGORY BARS === */
.category-bars { display: flex; flex-direction: column; gap: 20px; }
.cat-row-header { display: flex; justify-content: space-between; margin-bottom: 6px; }
.cat-name { font-family: 'Geist Mono', monospace; font-size: 0.75rem; color: #e2e8f0; }
.cat-wr   { font-family: 'Geist Mono', monospace; font-size: 0.75rem; color: #14b8a6; }
.cat-bar  { height: 8px; background: #252b3b; border-radius: 999px; overflow: hidden; display: flex; }
.cat-bar-win  { height: 100%; background: #22c55e; }
.cat-bar-loss { height: 100%; background: rgba(239,68,68,0.8); }

/* === RESEARCH DETAIL CARD === */
.research-detail-card { background: #1a1f2e; border: 1px solid #252b3b;
  border-radius: 8px; overflow: hidden; margin-bottom: 24px; }
.detail-header { padding: 20px 24px; border-bottom: 1px solid #252b3b; }
.detail-body { padding: 24px; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 24px; }
.detail-donut { display: flex; flex-direction: column; align-items: center;
  justify-content: center; background: rgba(15,17,23,0.5); border: 1px solid #252b3b;
  border-radius: 8px; padding: 16px; min-height: 140px; }
.source-chip { background: #252b3b; border: 1px solid #374151; border-radius: 4px;
  padding: 4px 10px; font-size: 0.75rem; color: #e2e8f0; display: inline-block; margin: 3px 3px 3px 0; }

/* === INFO CARDS (POST MORTEM) === */
.info-card { background: #1a1f2e; border: 1px solid #252b3b; border-radius: 8px; padding: 20px; }
.info-card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.info-card-title { font-size: 0.875rem; font-weight: 600; color: #f1f5f9; }
.failure-item { border-left: 2px solid #f59e0b; background: rgba(245,158,11,0.05);
  padding: 8px 12px; margin-bottom: 8px; border-radius: 0 4px 4px 0; }
.failure-item p { font-size: 0.75rem; color: #e2e8f0; font-weight: 600; }
.failure-meta { font-family: 'Geist Mono', monospace; font-size: 0.65rem; color: #64748b; margin-top: 2px; }

/* === SYSTEM INFO BAR === */
.system-info-bar { border-left: 2px solid #14b8a6; background: rgba(20,184,166,0.03);
  padding: 12px 16px; border-radius: 0 6px 6px 0; display: flex; align-items: center;
  gap: 12px; margin-top: 24px; }
.system-info-bar .material-symbols-outlined { color: #14b8a6; font-size: 1.1rem; flex-shrink: 0; }
.system-info-bar p { font-family: 'Geist Mono', monospace; font-size: 0.65rem; color: #94a3b8; }

/* === RUN LIST (SIDEBAR) === */
.sidebar-section-label { font-size: 0.6rem; font-weight: 600; color: #64748b;
  text-transform: uppercase; letter-spacing: 0.12em; padding: 12px 16px 6px;
  flex-shrink: 0; }
.run-list-scroll { flex: 1; overflow-y: auto; min-height: 0;
  scrollbar-width: thin; scrollbar-color: #252b3b #1a1f2e; }
.run-item { padding: 8px 12px; cursor: pointer; border-left: 2px solid transparent;
  margin: 1px 0; transition: all 0.15s; }
.run-item:hover { background: rgba(15,17,23,0.6); }
.run-item.selected { border-left-color: #14b8a6; background: rgba(20,184,166,0.05); }
.run-meta { display: flex; align-items: center; gap: 6px; }
.run-status-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.run-status-dot.completed { background: #22c55e; }
.run-status-dot.running   { background: #14b8a6; animation: pulse-dot 1.4s infinite; }
.run-status-dot.failed    { background: #ef4444; }
.run-status-dot.pending   { background: #64748b; }
.run-time   { font-family: 'Geist Mono', monospace; font-size: 0.7rem; color: #c9d1d9; }
.run-trades { font-family: 'Geist Mono', monospace; font-size: 0.65rem; color: #22c55e; margin-left: auto; }
.run-id-text { font-size: 0.6rem; color: #64748b; font-family: 'Geist Mono', monospace;
  margin-top: 1px; padding-left: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-footer { padding: 12px 16px; border-top: 1px solid #252b3b; flex-shrink: 0; }
.conn-live         { font-size: 0.72rem; color: #22c55e; }
.conn-reconnecting { font-size: 0.72rem; color: #f59e0b; }
.timestamp { font-size: 0.65rem; color: #64748b; display: block; margin-top: 2px; }

/* === ANIMATIONS === */
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.45; transform: scale(0.75); }
}

/* === RESPONSIVE === */
@media (max-width: 768px) {
  .sidebar { width: 56px; overflow: hidden; }
  .sidebar-brand h1, .sidebar-version, .item-label, .sidebar-section-label,
  .run-list-scroll, .sidebar-footer .timestamp { display: none; }
  .sidebar-item { padding: 12px; justify-content: center; gap: 0; }
  .top-header { left: 56px; }
  .main-content { margin-left: 56px; }
  .stat-grid-3, .stat-grid-4, .stat-grid-5 { grid-template-columns: repeat(2, 1fr); }
  .ensemble-grid { grid-template-columns: 1fr 1fr; }
  .detail-body { grid-template-columns: 1fr; }
  .view { padding: 16px; }
  th, td { padding: 10px 12px; }
}
"""
