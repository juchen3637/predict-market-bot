"""
dashboard_assets.py — Static CSS and JS strings for dashboard_server.py.

Kept in a separate module to keep dashboard_server.py under 800 lines.
"""

_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #e6edf3; padding: 24px; }
  h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 4px; }
  .subtitle { font-size: 0.8rem; color: #8b949e; margin-bottom: 0; }
  .header { display: flex; justify-content: space-between; align-items: flex-start;
            flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .header-right { display: flex; align-items: center; gap: 12px; margin-top: 2px; flex-shrink: 0; }
  .timestamp { font-size: 0.75rem; color: #8b949e; }
  .conn-live { font-size: 0.75rem; color: #3fb950; }
  .conn-reconnecting { font-size: 0.75rem; color: #d4a017; }

  .tab-bar { display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid #30363d; padding-bottom: 0; }
  .tab { background: none; border: none; color: #8b949e; font-size: 0.9rem; font-weight: 500;
          padding: 8px 16px; cursor: pointer; border-bottom: 2px solid transparent;
          margin-bottom: -1px; transition: color 0.15s; }
  .tab:hover { color: #e6edf3; }
  .tab.active { color: #58a6ff; border-bottom-color: #58a6ff; }
  .tab .count { font-size: 0.72rem; background: #21262d; border-radius: 10px;
                padding: 1px 6px; margin-left: 6px; }

  .cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 32px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
           padding: 16px 20px; min-width: 140px; flex: 1; }
  .card .label { font-size: 0.72rem; color: #8b949e; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 6px; }
  .card .value { font-size: 1.5rem; font-weight: 600; }
  .pos .value { color: #3fb950; }
  .neg .value { color: #f85149; }

  h2 { font-size: 1rem; font-weight: 600; margin-bottom: 12px; color: #c9d1d9; }
  section { margin-bottom: 32px; }

  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th { text-align: left; padding: 8px 12px; background: #161b22;
        color: #8b949e; font-weight: 500; border-bottom: 1px solid #30363d; white-space: nowrap; }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #161b22; }
  .empty { color: #8b949e; text-align: center; padding: 20px; }
  .pos { color: #3fb950; }
  .neg { color: #f85149; }

  .badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 12px;
            font-weight: 500; text-transform: uppercase; }
  .badge.paper { background: #1f3a5f; color: #58a6ff; }
  .badge.placed { background: #1a3a2a; color: #3fb950; }
  .badge.filled { background: #1a3a2a; color: #3fb950; }
  .badge.backtest { background: #2d2a1f; color: #d4a017; }

  @media (max-width: 640px) {
    body { padding: 12px; }
    h1 { font-size: 1.1rem; }
    .card { min-width: 120px; padding: 12px 14px; }
    .card .value { font-size: 1.2rem; }
    .tab { padding: 8px 10px; font-size: 0.82rem; }
    th, td { padding: 6px 8px; }
    .col-hide { display: none; }
    .pipeline-layout { flex-direction: column; }
    .pipeline-sidebar { width: 100%; }
  }

  /* Pipeline tab */
  .pipeline-layout { display: flex; gap: 20px; min-height: 400px; }
  .pipeline-sidebar { width: 240px; flex-shrink: 0; }
  .pipeline-main { flex: 1; min-width: 0; }
  .pipeline-sidebar-section { margin-bottom: 24px; }
  .pipeline-sidebar-label { font-size: 0.65rem; color: #8b949e; text-transform: uppercase;
    letter-spacing: .08em; margin-bottom: 10px; }

  .stage-step { display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    border-radius: 6px; cursor: pointer; transition: background 0.15s; margin-bottom: 2px; }
  .stage-step:hover { background: #161b22; }
  .stage-step.active-stage { background: #1c2128; border: 1px solid #30363d; }

  .stage-icon { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
    background: #30363d; transition: background 0.3s; }
  .stage-icon.running { background: #14b8a6; animation: pulse-dot 1.4s ease-in-out infinite; }
  .stage-icon.completed { background: #3fb950; }
  .stage-icon.failed { background: #f85149; }

  @keyframes pulse-dot {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.45; transform: scale(0.75); }
  }

  .stage-name { flex: 1; font-size: 0.85rem; color: #c9d1d9; }
  .stage-dur { font-size: 0.72rem; color: #8b949e; }

  .run-item { padding: 8px 10px; border-radius: 6px; cursor: pointer;
    border: 1px solid transparent; margin-bottom: 4px; transition: border-color 0.15s; }
  .run-item:hover { border-color: #30363d; background: #161b22; }
  .run-item.selected { border-color: #14b8a6; background: #0e2a28; }
  .run-item .run-meta { display: flex; align-items: center; gap: 6px; }
  .run-item .run-time { font-size: 0.82rem; color: #c9d1d9; }
  .run-item .run-trades { font-size: 0.72rem; color: #3fb950; margin-left: auto; }
  .run-item .run-id-text { font-size: 0.68rem; color: #8b949e; margin-top: 2px; }
  .run-status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .run-status-dot.completed { background: #3fb950; }
  .run-status-dot.running { background: #14b8a6; animation: pulse-dot 1.4s infinite; }
  .run-status-dot.failed { background: #f85149; }
  .run-status-dot.pending { background: #30363d; }
  .badge.live-badge { background: #0e2a28; color: #14b8a6; font-size: 0.65rem; padding: 1px 5px; }

  .pipeline-empty { color: #8b949e; text-align: center; padding: 60px 20px; font-size: 0.9rem; }

  .stage-detail h3 { font-size: 1rem; font-weight: 600; margin-bottom: 14px; color: #c9d1d9; }
  .stage-stats { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
  .stage-stat { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
    padding: 12px 16px; min-width: 90px; }
  .stage-stat .stat-label { font-size: 0.65rem; color: #8b949e; text-transform: uppercase;
    letter-spacing: .05em; margin-bottom: 4px; }
  .stage-stat .stat-value { font-size: 1.2rem; font-weight: 600; color: #e6edf3; }

  .stage-status-bar { display: flex; align-items: center; gap: 8px;
    padding: 10px 14px; border-radius: 6px; margin-bottom: 16px; font-size: 0.85rem; }
  .stage-status-bar.running { background: #0e2a28; border: 1px solid #14b8a6; color: #14b8a6; }
  .stage-status-bar.completed { background: #0e2a1a; border: 1px solid #3fb950; color: #3fb950; }
  .stage-status-bar.failed { background: #2a0e0e; border: 1px solid #f85149; color: #f85149; }
  .stage-status-bar.pending { background: #1c2128; border: 1px solid #30363d; color: #8b949e; }

  .run-list-scroll { max-height: 400px; overflow-y: auto; }
"""

_JS = """
(function() {
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function setEl(id, html) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  function fmtPct(v) { return v != null ? (v * 100).toFixed(1) + '%' : '—'; }
  function fmtNum(v, d) { return v != null ? parseFloat(v).toFixed(d != null ? d : 3) : '—'; }
  function fmtPnl(v) {
    if (v == null) return '—';
    var f = parseFloat(v);
    return (f >= 0 ? '+$' : '-$') + Math.abs(f).toFixed(2);
  }
  function pnlCls(v) { return v == null ? '' : (parseFloat(v) >= 0 ? 'pos' : 'neg'); }

  function openRow(t) {
    var edge = t.edge || 0;
    var dir = (t.direction || '').toLowerCase();
    var dEdge = (dir === 'yes' || dir === 'long') ? edge : -edge;
    var title = (t.title || t.market_id || '').slice(0, 60);
    var placed = (t.placed_at || '').slice(0, 16).replace('T', ' ');
    return '<tr>' +
      '<td title="' + (t.market_id || '') + '">' + title + '</td>' +
      '<td>' + (t.platform || '') + '</td>' +
      '<td>' + (t.direction || '').toUpperCase() + '</td>' +
      '<td>$' + parseFloat(t.size_usd || 0).toFixed(2) + '</td>' +
      '<td class="col-hide">' + fmtNum(t.entry_price) + '</td>' +
      '<td class="col-hide">' + fmtNum(t.p_model) + '</td>' +
      '<td>' + (dEdge >= 0 ? '+' : '') + (dEdge * 100).toFixed(1) + '%</td>' +
      '<td><span class="badge ' + (t.status || '') + '">' + (t.status || '') + '</span></td>' +
      '<td>' + placed + '</td>' +
      '</tr>';
  }

  function resolvedRow(t) {
    var pnl = t.pnl;
    var resolved = (t.resolved_at || '').slice(0, 16).replace('T', ' ');
    var title = (t.title || t.market_id || '').slice(0, 60);
    return '<tr>' +
      '<td title="' + (t.market_id || '') + '">' + title + '</td>' +
      '<td class="col-hide">' + (t.platform || '') + '</td>' +
      '<td class="col-hide">' + (t.direction || '').toUpperCase() + '</td>' +
      '<td>$' + parseFloat(t.size_usd || 0).toFixed(2) + '</td>' +
      '<td>' + (t.outcome || '') + '</td>' +
      '<td class="' + pnlCls(pnl) + '">' + fmtPnl(pnl) + '</td>' +
      '<td>' + resolved + '</td>' +
      '</tr>';
  }

  function updateMode(mode, md, aiCost, brier) {
    if (!md) return;
    var m = md.metrics || {};
    setEl(mode + '-win-rate', fmtPct(m.win_rate));
    setEl(mode + '-max-drawdown', fmtPct(m.max_drawdown));
    setEl(mode + '-profit-factor', fmtNum(m.profit_factor));
    setEl(mode + '-brier', fmtNum(brier));
    setEl(mode + '-trade-count', m.trade_count || 0);
    setEl(mode + '-open-count', md.open_count || 0);
    setEl(mode + '-open-heading', md.open_count || 0);
    setEl('count-' + mode, md.open_count || 0);

    var tEl = document.getElementById(mode + '-total-pnl');
    if (tEl) { tEl.innerHTML = fmtPnl(m.total_pnl); tEl.parentElement.className = 'card ' + pnlCls(m.total_pnl); }
    var dEl = document.getElementById(mode + '-daily-pnl');
    if (dEl) { dEl.innerHTML = fmtPnl(md.daily_pnl); dEl.parentElement.className = 'card ' + pnlCls(md.daily_pnl); }
    setEl(mode + '-ai-cost', '$' + parseFloat(aiCost || 0).toFixed(4));

    var ot = document.getElementById(mode + '-open-tbody');
    if (ot) ot.innerHTML = md.open_trades && md.open_trades.length ?
      md.open_trades.map(openRow).join('') :
      '<tr><td colspan="9" class="empty">No open positions</td></tr>';

    var rt = document.getElementById(mode + '-resolved-tbody');
    if (rt) rt.innerHTML = md.resolved_trades && md.resolved_trades.length ?
      md.resolved_trades.map(resolvedRow).join('') :
      '<tr><td colspan="7" class="empty">No resolved trades yet</td></tr>';
  }

  function updateDashboard(data) {
    setEl('last-updated', 'Updated ' + data.updated_at);
    updateMode('paper', data.paper, data.ai_cost_today, data.brier_score);
    updateMode('live',  data.live,  data.ai_cost_today, data.brier_score);
  }

  var statusEl = document.getElementById('conn-status');
  var es = new EventSource('/events');

  es.onopen = function() {
    if (statusEl) { statusEl.className = 'conn-live'; statusEl.textContent = '● Live'; }
  };
  es.onmessage = function(e) {
    try { updateDashboard(JSON.parse(e.data)); } catch(err) {}
  };
  es.onerror = function() {
    if (statusEl) { statusEl.className = 'conn-reconnecting'; statusEl.textContent = '◌ Reconnecting…'; }
  };

  function switchTab(mode) {
    document.getElementById('view-paper').style.display = mode === 'paper' ? 'block' : 'none';
    document.getElementById('view-live').style.display  = mode === 'live'  ? 'block' : 'none';
    document.getElementById('view-pipeline').style.display = mode === 'pipeline' ? 'block' : 'none';
    document.getElementById('tab-paper').className = 'tab' + (mode === 'paper' ? ' active' : '');
    document.getElementById('tab-live').className  = 'tab' + (mode === 'live'  ? ' active' : '');
    document.getElementById('tab-pipeline').className = 'tab' + (mode === 'pipeline' ? ' active' : '');
    if (mode === 'pipeline' && _pipelineRuns.length === 0) loadPipelineRuns();
    try { localStorage.setItem('dashboard_tab', mode); } catch(e) {}
  }
  window.switchTab = switchTab;
  (function() {
    var saved = 'paper';
    try { saved = localStorage.getItem('dashboard_tab') || 'paper'; } catch(e) {}
    switchTab(saved);
  })();

  // ---- Pipeline tab ----
  var _pipelineRuns = [];
  var _selectedRunId = null;
  var _selectedStage = 'scan';

  var _STAGE_STATS = {
    scan:     [['candidates', 'Markets Found']],
    research: [['candidates','Total'],['cache_hits','Cache Hits'],['fresh_fetches','Fresh Fetches'],['low_confidence','Low Conf'],['skipped','Skipped']],
    predict:  [['signaled','Signaled'],['skipped','Skipped'],['cache_hits','Cache Hits'],['avg_edge','Avg Edge']],
    risk:     [['approved','Approved'],['blocked','Blocked']],
  };

  function loadPipelineRuns() {
    fetch('/api/runs').then(function(r){ return r.json(); }).then(function(runs){
      _pipelineRuns = runs;
      renderRunList(runs);
      if (runs.length > 0 && !_selectedRunId) selectRun(runs[0].run_id);
    }).catch(function(){});
  }

  function renderRunList(runs) {
    var el = document.getElementById('pipeline-run-list');
    if (!el) return;
    if (!runs.length) {
      el.innerHTML = '<div class="pipeline-empty" style="padding:16px 0">No runs yet</div>';
      return;
    }
    el.innerHTML = runs.map(function(r) {
      var t = (r.started_at || '').slice(0,16).replace('T',' ');
      var trades = r.trades_placed || 0;
      var sel = _selectedRunId === r.run_id ? ' selected' : '';
      var safeId = escHtml(r.run_id || '');
      return '<div class="run-item' + sel + '" id="run-item-' + safeId + '" onclick="selectRun(\\'' + safeId + '\\')">' +
        '<div class="run-meta">' +
          '<span class="run-status-dot ' + escHtml(r.status||'pending') + '"></span>' +
          '<span class="run-time">' + escHtml(t) + '</span>' +
          (r.status === 'running' ? '<span class="badge live-badge">LIVE</span>' : '') +
          (trades > 0 ? '<span class="run-trades">' + trades + ' trade' + (trades !== 1 ? 's' : '') + '</span>' : '') +
        '</div>' +
        '<div class="run-id-text">' + safeId + '</div>' +
      '</div>';
    }).join('');
  }

  function renderLiveStages(manifest) {
    ['scan','research','predict','risk'].forEach(function(name) {
      var s = ((manifest.stages || {})[name]) || {};
      var status = s.status || 'pending';
      var iconEl = document.getElementById('stage-icon-' + name);
      if (iconEl) iconEl.className = 'stage-icon ' + status;
      var durEl = document.getElementById('stage-dur-' + name);
      if (durEl) durEl.textContent = s.duration_s != null ? s.duration_s + 's' : (status === 'running' ? '…' : '');
    });
  }

  function selectRun(runId) {
    _selectedRunId = runId;
    document.querySelectorAll('.run-item').forEach(function(el) {
      el.className = 'run-item' + (el.id === 'run-item-' + runId ? ' selected' : '');
    });
    var run = _pipelineRuns.find(function(r){ return r.run_id === runId; });
    if (run) {
      renderLiveStages(run);
      renderStageDetail(run, _selectedStage || 'scan');
    }
  }

  function selectStage(stageName) {
    _selectedStage = stageName;
    var run = _pipelineRuns.find(function(r){ return r.run_id === _selectedRunId; });
    if (run) renderStageDetail(run, stageName);
  }
  window.selectStage = selectStage;
  window.selectRun = selectRun;

  function renderStageDetail(manifest, stageName) {
    _selectedStage = stageName;
    document.querySelectorAll('.stage-step').forEach(function(el) {
      el.className = 'stage-step' + (el.dataset.stage === stageName ? ' active-stage' : '');
    });

    var stage = ((manifest.stages || {})[stageName]) || {};
    var status = stage.status || 'pending';
    var detail = document.getElementById('pipeline-stage-detail');
    var noData = document.getElementById('pipeline-no-data');
    if (!detail) return;
    detail.style.display = 'block';
    if (noData) noData.style.display = 'none';

    var label = stageName.charAt(0).toUpperCase() + stageName.slice(1);
    var statusBar = '<div class="stage-status-bar ' + status + '">' +
      '<span>' + label + ' — ' + status.toUpperCase() + '</span>' +
      (stage.duration_s != null ? '<span style="margin-left:auto">' + stage.duration_s + 's</span>' : '') +
    '</div>';

    var stats = '';
    var keys = _STAGE_STATS[stageName] || [];
    if (keys.length && status !== 'pending') {
      stats = '<div class="stage-stats">' + keys.map(function(kv) {
        var val = stage[kv[0]];
        if (val == null) val = '—';
        else if (kv[0] === 'avg_edge') val = (parseFloat(val) * 100).toFixed(1) + '%';
        return '<div class="stage-stat"><div class="stat-label">' + kv[1] + '</div>' +
               '<div class="stat-value">' + val + '</div></div>';
      }).join('') + '</div>';
    }

    var errBar = stage.error
      ? '<div class="stage-status-bar failed" style="margin-top:8px">Error: ' + escHtml(stage.error) + '</div>'
      : '';

    detail.innerHTML = '<div class="stage-detail">' +
      '<h3>' + label + ' Stage</h3>' + statusBar + stats + errBar +
    '</div>';
  }

  es.addEventListener('run_update', function(e) {
    try {
      var updated = JSON.parse(e.data);
      var idx = _pipelineRuns.findIndex(function(r){ return r.run_id === updated.run_id; });
      if (idx >= 0) {
        _pipelineRuns = _pipelineRuns.map(function(r, i){ return i === idx ? updated : r; });
      } else {
        _pipelineRuns = [updated].concat(_pipelineRuns);
      }
      renderRunList(_pipelineRuns);
      if (_selectedRunId === updated.run_id) {
        renderLiveStages(updated);
        renderStageDetail(updated, _selectedStage || 'scan');
      } else if (!_selectedRunId) {
        selectRun(updated.run_id);
      }
    } catch(err) {}
  });
})();
"""
