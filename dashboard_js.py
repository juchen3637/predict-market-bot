"""
dashboard_js.py — Vanilla JS for the predict-market-bot dashboard.

Kept in a separate module to keep dashboard_server.py under 800 lines.
CSS is in dashboard_assets.py.
"""

_JS = r"""
'use strict';

/* =========================================================
   State
   ========================================================= */
var _currentView = 'scan';
var _currentMode = 'paper';
var _lastState   = null;
var _selectedRunId = null;
var _researchCandidates = [];
var _selectedResearchIdx = -1;
var _predictSignals = [];
var _selectedPredictIdx = -1;

/* =========================================================
   Utilities
   ========================================================= */
function escHtml(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtPct(v, decimals) {
  if (v == null) return '\u2014';
  return (v * 100).toFixed(decimals != null ? decimals : 1) + '%';
}

function fmtNum(v, decimals) {
  if (v == null) return '\u2014';
  return parseFloat(v).toFixed(decimals != null ? decimals : 3);
}

function fmtUsd(v) {
  if (v == null) return '\u2014';
  var n = parseFloat(v);
  return (n >= 0 ? '+' : '') + '$' + n.toFixed(2);
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  var d = new Date(isoStr);
  var diff = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function emptyRow(cols, msg) {
  return '<tr><td colspan="' + cols + '" class="empty-row">' + escHtml(msg || 'No data') + '</td></tr>';
}

/* =========================================================
   Navigation
   ========================================================= */
var _VIEWS = ['scan', 'research', 'predict', 'risk', 'postmortem'];
var _VIEW_LABELS = {
  scan: 'Scanner Mode',
  research: 'Research Mode',
  predict: 'Predict Mode',
  risk: 'Risk Mode',
  postmortem: 'Post Mortem'
};

function switchView(name) {
  _currentView = name;
  _VIEWS.forEach(function(v) {
    var el = document.getElementById('view-' + v);
    if (el) el.style.display = v === name ? 'block' : 'none';
    var nav = document.getElementById('nav-' + v);
    if (nav) nav.className = 'sidebar-item' + (v === name ? ' active' : '');
  });

  var modeLabel = document.getElementById('header-mode-label');
  if (modeLabel) modeLabel.textContent = _VIEW_LABELS[name] || name;

  var toggle = document.getElementById('mode-toggle');
  if (toggle) toggle.style.display = (name === 'risk' || name === 'postmortem') ? 'flex' : 'none';

  if (name === 'scan')        loadScanData();
  else if (name === 'research')   loadResearchData();
  else if (name === 'predict')    loadPredictData();
  else if (name === 'risk')       updateRiskView();
  else if (name === 'postmortem') updatePostMortemView();

  try { localStorage.setItem('dashboard_view', name); } catch(e) {}
}
window.switchView = switchView;

function switchMode(mode) {
  _currentMode = mode;
  var btnPaper = document.getElementById('toggle-paper');
  var btnLive  = document.getElementById('toggle-live');
  if (btnPaper) btnPaper.className = 'mode-btn' + (mode === 'paper' ? ' active' : '');
  if (btnLive)  btnLive.className  = 'mode-btn' + (mode === 'live'  ? ' active' : '');
  updateRiskView();
  updatePostMortemView();
  try { localStorage.setItem('dashboard_mode', mode); } catch(e) {}
}
window.switchMode = switchMode;

/* =========================================================
   Scan view
   ========================================================= */
function loadScanData() {
  fetch('/api/candidates').then(function(r) { return r.json(); }).then(function(data) {
    var candidates = data.candidates || [];
    renderScanCards(candidates);
    renderScanTable(candidates);
    var meta = document.getElementById('scan-meta');
    if (meta && data.scan_id) {
      meta.textContent = 'Run: ' + data.scan_id;
    }
  }).catch(function() {
    renderScanTable([]);
  });
}

function renderScanCards(candidates) {
  var kalshi = candidates.filter(function(c) { return (c.platform || '').toLowerCase() === 'kalshi'; }).length;
  var poly   = candidates.filter(function(c) { return (c.platform || '').toLowerCase() === 'polymarket'; }).length;
  var total  = candidates.length;

  _setStatNumber('scan-stat-kalshi', kalshi, 'teal');
  _setStatNumber('scan-stat-poly', poly, 'teal');
  _setStatNumber('scan-stat-total', total, 'white');
}

function renderScanTable(candidates) {
  var tbody = document.getElementById('scan-tbody');
  if (!tbody) return;
  if (!candidates.length) { tbody.innerHTML = emptyRow(8, 'No candidates — run the pipeline first'); return; }

  tbody.innerHTML = candidates.map(function(c) {
    var platCls = (c.platform || '').toLowerCase() === 'kalshi' ? 'bdg-kalshi' : 'bdg-polymarket';
    var flags = (c.anomaly_flags || []).map(function(f) {
      var cls = (f.toLowerCase().includes('price') || f.toLowerCase().includes('spread')) ? 'anomaly-price' : 'anomaly-vol';
      return '<span class="' + cls + '">' + escHtml(f) + '</span>';
    }).join(' ');
    var yesPrice = c.current_yes_price != null ? (c.current_yes_price * 100).toFixed(1) + '&cent;' : '\u2014';
    var vol = c.volume_24h != null ? '$' + parseFloat(c.volume_24h).toLocaleString('en-US', {maximumFractionDigits: 0}) : '\u2014';
    var oi  = c.open_interest != null ? parseFloat(c.open_interest).toLocaleString('en-US', {maximumFractionDigits: 0}) : '\u2014';
    var days = c.days_to_expiry != null ? Math.round(c.days_to_expiry) + 'd' : '\u2014';
    var mid = (c.market_id || '').slice(-14);
    return '<tr>' +
      '<td class="td-mono" title="' + escHtml(c.market_id) + '">' + escHtml(mid) + '</td>' +
      '<td><span class="bdg ' + platCls + '">' + escHtml((c.platform || '').toUpperCase()) + '</span></td>' +
      '<td style="color:#94a3b8;font-size:0.75rem">' + escHtml(c.category || '') + '</td>' +
      '<td class="td-teal">' + yesPrice + '</td>' +
      '<td class="td-mono">' + vol + '</td>' +
      '<td class="td-mono">' + oi + '</td>' +
      '<td class="td-mono">' + days + '</td>' +
      '<td>' + (flags || '<span style="color:#374151">\u2014</span>') + '</td>' +
    '</tr>';
  }).join('');
}

/* =========================================================
   Research view
   ========================================================= */
function loadResearchData() {
  fetch('/api/enriched').then(function(r) { return r.json(); }).then(function(data) {
    var candidates = data.candidates || [];
    _researchCandidates = candidates;
    _selectedResearchIdx = candidates.length ? 0 : -1;
    renderResearchCards(candidates);
    renderResearchTable(candidates);
    if (candidates.length) renderResearchDetail(candidates[0]);
  }).catch(function() {
    renderResearchTable([]);
  });
}

function renderResearchCards(candidates) {
  var cacheHits    = candidates.filter(function(c) { return c.sentiment && c.sentiment.cache_hit; }).length;
  var fresh        = candidates.filter(function(c) { return c.sentiment && !c.sentiment.cache_hit && !c.research_skipped; }).length;
  var lowConf      = candidates.filter(function(c) { return c.low_confidence; }).length;
  var skipped      = candidates.filter(function(c) { return c.research_skipped; }).length;

  _setStatNumber('res-stat-cache', cacheHits, 'green');
  _setStatNumber('res-stat-fresh', fresh, 'teal');
  _setStatNumber('res-stat-lowconf', lowConf, 'amber');
  _setStatNumber('res-stat-skip', skipped, 'muted');
}

function renderResearchTable(candidates) {
  var tbody = document.getElementById('research-tbody');
  if (!tbody) return;
  if (!candidates.length) { tbody.innerHTML = emptyRow(5, 'No research data yet'); return; }

  tbody.innerHTML = candidates.map(function(c, i) {
    var sent = c.sentiment || {};
    var score = sent.score != null ? parseFloat(sent.score) : null;
    var conf  = sent.confidence != null ? parseFloat(sent.confidence) : null;
    var dotColor = _sentimentColor(sent.label);
    var confBar = conf != null
      ? '<div class="progress-bar" style="width:80px"><div class="progress-fill" style="width:' + (conf*100).toFixed(0) + '%"></div></div>'
      : '\u2014';
    var status = c.research_skipped
      ? '<span class="bdg bdg-skipped">Skipped</span>'
      : (c.low_confidence
          ? '<span class="bdg bdg-lowconf">Low Conf</span>'
          : '<span class="bdg bdg-optimal">OK</span>');
    var mid = (c.market_id || '').slice(-14);
    var rowStyle = i === _selectedResearchIdx ? ' style="background:#1a2035;cursor:pointer"' : ' style="cursor:pointer"';
    return '<tr onclick="selectResearchMarket(' + i + ')"' + rowStyle + '>' +
      '<td class="td-mono" title="' + escHtml(c.market_id) + '">' + escHtml(mid) + '</td>' +
      '<td><span class="sentiment-dot" style="background:' + dotColor + '"></span>' +
           '<span style="font-size:0.75rem;color:#e2e8f0">' + escHtml(sent.label || '\u2014') + '</span></td>' +
      '<td>' + confBar + '</td>' +
      '<td>' + status + '</td>' +
    '</tr>';
  }).join('');
}

function renderResearchDetail(candidate) {
  var el = document.getElementById('research-detail-content');
  if (!el || !candidate) return;
  var sent = candidate.sentiment || {};
  var gap  = candidate.gap_analysis || {};
  var score = sent.score != null ? parseFloat(sent.score) : null;
  var conf  = sent.confidence != null ? parseFloat(sent.confidence) : null;
  var sources = sent.sources || [];

  var donutSvg = _buildDonutSvg(conf != null ? conf : 0, conf != null ? (conf*100).toFixed(0)+'%' : '\u2014');

  var sourcesHtml = sources.length
    ? sources.map(function(s) { return '<span class="source-chip">' + escHtml(s) + '</span>'; }).join('')
    : '<span style="color:#64748b;font-size:0.75rem">None</span>';

  var gapPct = gap.signal_strength != null ? parseFloat(gap.signal_strength) : null;
  var gapBar = gapPct != null
    ? '<div class="progress-bar"><div class="progress-fill ' + (gap.direction === 'long' ? 'green' : 'amber') + '" style="width:' + (Math.abs(gapPct)*100).toFixed(0) + '%"></div></div>'
    : '<div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>';

  el.innerHTML =
    '<div class="detail-body">' +
    '<div>' +
      '<div class="stat-label">Sentiment Score</div>' +
      '<div style="font-family:\'Geist Mono\',monospace;font-size:1.4rem;font-weight:700;color:' + _sentimentColor(sent.label) + ';margin:4px 0">' +
        (score != null ? (score > 0 ? '+' : '') + score.toFixed(2) : '\u2014') +
      '</div>' +
      '<div class="stat-label" style="margin-top:12px">Gap Signal (' + escHtml(gap.direction || 'none') + ')</div>' +
      gapBar +
      '<div style="font-family:\'Geist Mono\',monospace;font-size:0.65rem;color:#64748b;margin-top:4px">' +
        'Strength: ' + (gapPct != null ? (gapPct*100).toFixed(1)+'%' : '\u2014') +
      '</div>' +
    '</div>' +
    '<div>' +
      '<div class="stat-label">Sources</div>' +
      '<div style="margin-top:8px">' + sourcesHtml + '</div>' +
    '</div>' +
    '<div class="detail-donut">' + donutSvg + '</div>' +
    '</div>';

  var titleEl = document.getElementById('research-detail-title');
  if (titleEl) titleEl.textContent = candidate.title || candidate.market_id || '';
}

function selectResearchMarket(idx) {
  _selectedResearchIdx = idx;
  var candidate = _researchCandidates[idx];
  if (candidate) renderResearchDetail(candidate);
  var rows = document.querySelectorAll('#research-tbody tr');
  rows.forEach(function(row, i) {
    row.style.background = i === idx ? '#1a2035' : '';
  });
}
window.selectResearchMarket = selectResearchMarket;

function _buildDonutSvg(fraction, label) {
  var r = 44, cx = 60, cy = 60, stroke = 8;
  var circ = 2 * Math.PI * r;
  var dash = fraction * circ;
  return '<svg width="120" height="120" viewBox="0 0 120 120">' +
    '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="#252b3b" stroke-width="' + stroke + '"/>' +
    '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="#14b8a6" stroke-width="' + stroke + '"' +
    ' stroke-dasharray="' + dash.toFixed(2) + ' ' + circ.toFixed(2) + '"' +
    ' stroke-linecap="round" transform="rotate(-90 ' + cx + ' ' + cy + ')"/>' +
    '<text x="' + cx + '" y="' + (cy+6) + '" text-anchor="middle" font-family="Geist Mono,monospace" font-size="14" font-weight="700" fill="#f1f5f9">' + escHtml(label) + '</text>' +
    '</svg>';
}

function _sentimentColor(label) {
  if (!label) return '#64748b';
  var l = label.toLowerCase();
  if (l === 'bullish')  return '#22c55e';
  if (l === 'bearish')  return '#ef4444';
  if (l === 'neutral')  return '#64748b';
  if (l === 'mixed')    return '#f59e0b';
  return '#94a3b8';
}

/* =========================================================
   Predict view
   ========================================================= */
function loadPredictData() {
  fetch('/api/signals').then(function(r) { return r.json(); }).then(function(data) {
    var signals = data.signals || [];
    renderPredictCards(signals, data.brier_status);
    var actives = signals.filter(function(s) { return !s.predict_skipped; });
    _predictSignals = actives;
    _selectedPredictIdx = actives.length ? 0 : -1;
    renderEnsemble(actives[0] || signals[0]);
    renderSignaledTable(actives);
  }).catch(function() {
    renderSignaledTable([]);
  });
}

function renderPredictCards(signals, brierStatus) {
  var signaled   = signals.filter(function(s) { return !s.predict_skipped; }).length;
  var skipped    = signals.filter(function(s) { return s.predict_skipped; }).length;
  var cacheHits  = signals.filter(function(s) { return s.cache_hit; }).length;
  var edges = signals.filter(function(s) { return s.edge != null; }).map(function(s) { return Math.abs(parseFloat(s.edge)); });
  var avgEdge = edges.length ? (edges.reduce(function(a,b){return a+b;},0)/edges.length) : null;

  _setStatNumber('pred-stat-signaled', signaled, 'green');
  _setStatNumber('pred-stat-skipped', skipped, 'muted');
  _setStatNumber('pred-stat-cache', cacheHits, 'teal');
  var avgEl = document.getElementById('pred-stat-edge');
  if (avgEl) avgEl.textContent = avgEdge != null ? (avgEdge*100).toFixed(1)+'%' : '\u2014';
}

function renderEnsemble(signal) {
  var colClaude  = document.getElementById('ensemble-claude');
  var colGpt     = document.getElementById('ensemble-gpt');
  var colGemini  = document.getElementById('ensemble-gemini');
  var colConsens = document.getElementById('ensemble-consensus');

  if (!signal || !colConsens) return;

  var llm = signal.llm_consensus || {};
  var prob = llm.consensus_prob != null ? (llm.consensus_prob * 100).toFixed(1) + '%' : '\u2014';
  var agree = llm.weighted_agreement != null ? (llm.weighted_agreement * 100).toFixed(0) + '% agree' : '';
  var models = llm.models_responded || 0;

  // Individual model probs not stored separately — show consensus in all
  if (colClaude)  colClaude.innerHTML  = '<div class="ensemble-label">Claude Sonnet</div>' +
    '<div class="ensemble-prob" style="color:#94a3b8">\u2014</div>' +
    '<div class="ensemble-sub">Not stored individually</div>';
  if (colGpt)     colGpt.innerHTML     = '<div class="ensemble-label">GPT-4o Mini</div>' +
    '<div class="ensemble-prob" style="color:#94a3b8">\u2014</div>' +
    '<div class="ensemble-sub">Not stored individually</div>';
  if (colGemini)  colGemini.innerHTML  = '<div class="ensemble-label">Gemini Flash</div>' +
    '<div class="ensemble-prob" style="color:#94a3b8">\u2014</div>' +
    '<div class="ensemble-sub">Not stored individually</div>';
  if (colConsens) colConsens.innerHTML = '<div class="ensemble-label">Weighted Consensus</div>' +
    '<div class="ensemble-prob" style="color:#14b8a6">' + prob + '</div>' +
    '<div class="ensemble-sub">' + agree + (models ? ' &bull; ' + models + ' models' : '') + '</div>';

  // Update market card
  var cardTag   = document.getElementById('predict-market-tag');
  var cardTitle = document.getElementById('predict-market-title');
  if (cardTag)   cardTag.textContent   = signal.market_id || '';
  if (cardTitle) cardTitle.textContent = signal.title || 'No active market';
}

function renderSignaledTable(signals) {
  var tbody = document.getElementById('predict-tbody');
  if (!tbody) return;
  if (!signals.length) { tbody.innerHTML = emptyRow(7, 'No signals — adjust min_edge_to_signal or run pipeline'); return; }

  tbody.innerHTML = signals.map(function(s, i) {
    var edge = s.edge != null ? parseFloat(s.edge) : null;
    var edgeCls = edge != null ? (edge > 0 ? 'td-green' : 'td-red') : '';
    var edgeTxt = edge != null ? (edge > 0 ? '+' : '') + (edge*100).toFixed(1)+'%' : '\u2014';
    var dir = (s.direction || '').toLowerCase() === 'long'
      ? '<span class="bdg bdg-buy">LONG</span>'
      : '<span class="bdg bdg-sell">SHORT</span>';
    var cacheIcon = s.cache_hit
      ? '<span title="Cache hit" style="color:#14b8a6;font-size:0.7rem">&#9762;</span>'
      : '<span style="color:#374151">\u2014</span>';
    var models = (s.llm_consensus || {}).models_responded || 0;
    var mid = (s.market_id || '').slice(-14);
    var rowStyle = i === _selectedPredictIdx ? ' style="background:#1a2035;cursor:pointer"' : ' style="cursor:pointer"';
    return '<tr onclick="selectPredictMarket(' + i + ')"' + rowStyle + '>' +
      '<td class="td-mono" title="' + escHtml(s.market_id) + '">' + escHtml(mid) + '</td>' +
      '<td class="td-teal">' + fmtPct(s.p_model) + '</td>' +
      '<td class="' + edgeCls + '">' + edgeTxt + '</td>' +
      '<td>' + dir + '</td>' +
      '<td><span class="model-chip agreed" title="Ensemble">E</span>' +
           '<span style="font-family:\'Geist Mono\',monospace;font-size:0.65rem;color:#64748b;margin-left:4px">' + models + ' model' + (models !== 1 ? 's' : '') + '</span></td>' +
      '<td>' + cacheIcon + '</td>' +
    '</tr>';
  }).join('');
}

function selectPredictMarket(idx) {
  _selectedPredictIdx = idx;
  var signal = _predictSignals[idx];
  if (signal) renderEnsemble(signal);
  var rows = document.querySelectorAll('#predict-tbody tr');
  rows.forEach(function(row, i) {
    row.style.background = i === idx ? '#1a2035' : '';
  });
}
window.selectPredictMarket = selectPredictMarket;

/* =========================================================
   Risk view (driven by SSE state)
   ========================================================= */
function updateRiskView() {
  if (!_lastState) return;
  var md = _lastState[_currentMode];
  if (!md) return;

  var m = md.metrics || {};
  var open = md.open_trades || [];
  var resolved = md.resolved_trades || [];

  // Stat cards — use server-computed counts from full trade set (not truncated array)
  var approved = md.win_count != null ? md.win_count : resolved.filter(function(t) { return t.outcome === 'win'; }).length;
  var blocked  = md.loss_count != null ? md.loss_count : 0;
  _setStatNumber('risk-stat-approved', approved, 'green');
  _setStatNumber('risk-stat-blocked', blocked, 'red');
  _setStatNumber('risk-stat-open', md.open_count || 0, 'teal');
  var dd = m.max_drawdown;
  var ddEl = document.getElementById('risk-stat-drawdown');
  if (ddEl) {
    ddEl.textContent = dd != null ? fmtPct(dd) : '\u2014';
    ddEl.className = 'stat-number ' + (dd != null && dd > 0.08 ? 'red' : 'amber');
  }

  // Open positions table
  var openTbody = document.getElementById('risk-open-tbody');
  if (openTbody) {
    openTbody.innerHTML = open.length
      ? open.map(_renderOpenRow).join('')
      : emptyRow(7, 'No open positions');
  }

  // Resolved trades table
  var resolvedTbody = document.getElementById('risk-resolved-tbody');
  if (resolvedTbody) {
    resolvedTbody.innerHTML = resolved.length
      ? resolved.map(_renderResolvedRow).join('')
      : emptyRow(7, 'No resolved trades yet');
  }
}

function _renderOpenRow(t) {
  var edge = t.edge != null ? parseFloat(t.edge) : null;
  var dir = (t.direction || '').toLowerCase() === 'long' || (t.direction || '').toLowerCase() === 'yes'
    ? '<span class="bdg bdg-buy">BUY</span>' : '<span class="bdg bdg-sell">SELL</span>';
  var placed = (t.placed_at || '').slice(0, 16).replace('T', ' ');
  return '<tr>' +
    '<td class="td-mono" title="' + escHtml(t.market_id) + '">' + escHtml((t.market_id || '').slice(-14)) + '</td>' +
    '<td style="font-size:0.75rem;color:#94a3b8;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escHtml(t.title || '') + '</td>' +
    '<td>' + dir + '</td>' +
    '<td class="td-mono">$' + parseFloat(t.size_usd || 0).toFixed(2) + '</td>' +
    '<td class="td-mono">' + (t.entry_price != null ? parseFloat(t.entry_price).toFixed(3) : '\u2014') + '</td>' +
    '<td class="' + (edge != null && edge > 0 ? 'td-green' : 'td-red') + '">' + (edge != null ? (edge>0?'+':'') + (edge*100).toFixed(1)+'%' : '\u2014') + '</td>' +
    '<td><span class="bdg-open">OPEN</span></td>' +
  '</tr>';
}

function _renderResolvedRow(t) {
  var pnl = t.pnl != null ? parseFloat(t.pnl) : null;
  var pnlCls = pnl != null ? (pnl >= 0 ? 'td-green' : 'td-red') : '';
  var pnlTxt = pnl != null ? (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2) : '\u2014';
  var resolved = (t.resolved_at || '').slice(0, 16).replace('T', ' ');
  var outcome = t.outcome === 'win'
    ? '<span style="color:#22c55e;font-family:\'Geist Mono\',monospace;font-size:0.75rem">WIN</span>'
    : t.outcome === 'expired'
    ? '<span style="color:#94a3b8;font-family:\'Geist Mono\',monospace;font-size:0.75rem">EXPIRED</span>'
    : '<span style="color:#ef4444;font-family:\'Geist Mono\',monospace;font-size:0.75rem">LOSS</span>';
  return '<tr>' +
    '<td class="td-mono" title="' + escHtml(t.market_id) + '">' + escHtml((t.market_id || '').slice(-14)) + '</td>' +
    '<td style="font-size:0.75rem;color:#94a3b8">' + escHtml((t.title || '').slice(0, 50)) + '</td>' +
    '<td>$' + parseFloat(t.size_usd || 0).toFixed(2) + '</td>' +
    '<td>' + outcome + '</td>' +
    '<td class="' + pnlCls + '">' + pnlTxt + '</td>' +
    '<td class="td-mono" style="color:#64748b">' + resolved + '</td>' +
  '</tr>';
}

/* =========================================================
   Post Mortem view (driven by SSE state)
   ========================================================= */
function updatePostMortemView() {
  if (!_lastState) return;
  var md = _lastState[_currentMode];
  if (!md) return;
  var m = md.metrics || {};

  // Stat cards
  var wr = m.win_rate;
  var wrEl = document.getElementById('pm-stat-wr');
  if (wrEl) { wrEl.textContent = wr != null ? fmtPct(wr) : '\u2014'; wrEl.className = 'stat-number ' + (wr != null && wr >= 0.6 ? 'green' : 'amber'); }

  var sharpe = _currentMode === 'paper' ? _lastState.sharpe_paper : _lastState.sharpe_live;
  var sharpeEl = document.getElementById('pm-stat-sharpe');
  if (sharpeEl) { sharpeEl.textContent = sharpe != null ? parseFloat(sharpe).toFixed(2) : '\u2014'; sharpeEl.className = 'stat-number ' + (sharpe != null && sharpe >= 2.0 ? 'green' : 'amber'); }

  var totalPnl = m.total_pnl;
  var totalPnlEl = document.getElementById('pm-stat-total-pnl');
  if (totalPnlEl) { totalPnlEl.textContent = totalPnl != null ? '$' + parseFloat(totalPnl).toFixed(2) : '\u2014'; totalPnlEl.className = 'stat-number ' + (totalPnl != null && totalPnl >= 0 ? 'green' : 'red'); }

  var dailyPnl = md.daily_pnl;
  var dailyPnlEl = document.getElementById('pm-stat-daily-pnl');
  if (dailyPnlEl) { dailyPnlEl.textContent = dailyPnl != null ? '$' + parseFloat(dailyPnl).toFixed(2) : '\u2014'; dailyPnlEl.className = 'stat-number ' + (dailyPnl != null && dailyPnl >= 0 ? 'green' : 'red'); }

  var brier = _lastState.brier_score;
  var brierEl = document.getElementById('pm-stat-brier');
  if (brierEl) { brierEl.textContent = brier != null ? parseFloat(brier).toFixed(3) : '\u2014'; brierEl.className = 'stat-number ' + (brier != null && brier < 0.25 ? 'green' : 'amber'); }

  var dd = m.max_drawdown;
  var ddEl = document.getElementById('pm-stat-dd');
  if (ddEl) { ddEl.textContent = dd != null ? fmtPct(dd) : '\u2014'; ddEl.className = 'stat-number ' + (dd != null && dd < 0.08 ? 'green' : 'red'); }

  var pf = m.profit_factor;
  var pfEl = document.getElementById('pm-stat-pf');
  if (pfEl) { pfEl.textContent = pf != null ? parseFloat(pf).toFixed(2) : '\u2014'; pfEl.className = 'stat-number ' + (pf != null && pf >= 1.5 ? 'green' : 'amber'); }

  var xgb = _lastState.xgboost || {};
  var xgbStatusEl = document.getElementById('pm-stat-xgb-status');
  if (xgbStatusEl) { xgbStatusEl.textContent = xgb.model_active ? 'Active' : 'Inactive'; xgbStatusEl.className = 'stat-number ' + (xgb.model_active ? 'green' : 'amber'); }

  var xgbTrainedEl = document.getElementById('pm-stat-xgb-trained');
  if (xgbTrainedEl) {
    var ts = xgb.last_trained_at;
    xgbTrainedEl.textContent = ts ? ts.slice(0, 16).replace('T', ' ') : '\u2014';
    xgbTrainedEl.className = 'stat-number white';
  }

  var xgbRecordsEl = document.getElementById('pm-stat-xgb-records');
  if (xgbRecordsEl) { xgbRecordsEl.textContent = xgb.last_train_trade_count != null ? xgb.last_train_trade_count : '\u2014'; xgbRecordsEl.className = 'stat-number teal'; }

  // PnL chart from equity curve
  var curve = _lastState.equity_curve || [];
  renderPnlChart(curve);

  // Category bars
  var cats = _lastState.category_stats || [];
  renderCategoryBars(cats);
}

function renderPnlChart(equityCurve) {
  var svgEl = document.getElementById('pnl-chart-svg');
  if (!svgEl) return;

  if (!equityCurve || equityCurve.length < 2) {
    svgEl.innerHTML = '<text x="50%" y="50%" text-anchor="middle" fill="#64748b" font-family="Geist Mono,monospace" font-size="12">No resolved trades yet</text>';
    return;
  }

  var W = 400, H = 200, padL = 48, padR = 16, padT = 16, padB = 32;
  var innerW = W - padL - padR, innerH = H - padT - padB;

  var values = equityCurve.map(function(p) { return p.equity; });
  var minV = Math.min.apply(null, values);
  var maxV = Math.max.apply(null, values);
  var range = maxV - minV || 1;

  function xOf(i) { return padL + (i / (equityCurve.length - 1)) * innerW; }
  function yOf(v) { return padT + innerH - ((v - minV) / range) * innerH; }

  // Grid lines (4)
  var grid = '';
  for (var g = 0; g <= 4; g++) {
    var gy = padT + (g / 4) * innerH;
    var gv = maxV - (g / 4) * range;
    grid += '<line x1="' + padL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + gy.toFixed(1) +
            '" stroke="#252b3b" stroke-width="1"/>';
    grid += '<text x="' + (padL - 4) + '" y="' + (gy + 4).toFixed(1) + '" text-anchor="end" fill="#64748b"' +
            ' font-family="Geist Mono,monospace" font-size="9">$' + gv.toFixed(0) + '</text>';
  }

  // Path points
  var pts = equityCurve.map(function(p, i) { return xOf(i).toFixed(1) + ',' + yOf(p.equity).toFixed(1); });
  var pathD = 'M' + pts.join(' L');

  // Gradient fill path (close to bottom)
  var fillD = pathD + ' L' + xOf(equityCurve.length - 1).toFixed(1) + ',' + (padT + innerH) +
              ' L' + padL + ',' + (padT + innerH) + ' Z';

  // Last label
  var last = equityCurve[equityCurve.length - 1];
  var lastX = xOf(equityCurve.length - 1);
  var lastY = yOf(last.equity);
  var lastColor = last.equity >= values[0] ? '#22c55e' : '#ef4444';

  svgEl.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  svgEl.innerHTML =
    '<defs>' +
    '<linearGradient id="pnl-grad" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="0%" stop-color="#14b8a6" stop-opacity="0.25"/>' +
    '<stop offset="100%" stop-color="#14b8a6" stop-opacity="0.02"/>' +
    '</linearGradient></defs>' +
    grid +
    '<path d="' + fillD + '" fill="url(#pnl-grad)"/>' +
    '<path d="' + pathD + '" fill="none" stroke="#14b8a6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<circle cx="' + lastX.toFixed(1) + '" cy="' + lastY.toFixed(1) + '" r="3" fill="' + lastColor + '"/>';
}

function renderCategoryBars(categoryStats) {
  var el = document.getElementById('category-bars');
  if (!el) return;
  if (!categoryStats || !categoryStats.length) {
    el.innerHTML = '<p style="color:#64748b;font-size:0.8rem">No category data yet</p>';
    return;
  }
  el.innerHTML = categoryStats.map(function(cat) {
    var wr = cat.total > 0 ? cat.wins / cat.total : 0;
    var wrPct = (wr * 100).toFixed(0);
    var lossPct = (100 - parseFloat(wrPct)).toFixed(0);
    return '<div class="cat-row">' +
      '<div class="cat-row-header">' +
      '<span class="cat-name">' + escHtml(cat.category) + '</span>' +
      '<span class="cat-wr">' + wrPct + '% WR <span style="color:#64748b">(' + cat.wins + '/' + cat.total + ')</span></span>' +
      '</div>' +
      '<div class="cat-bar">' +
      '<div class="cat-bar-win" style="width:' + wrPct + '%"></div>' +
      '<div class="cat-bar-loss" style="width:' + lossPct + '%"></div>' +
      '</div></div>';
  }).join('');
}

/* =========================================================
   Pipeline run list (sidebar)
   ========================================================= */
function loadPipelineRuns() {
  fetch('/api/runs').then(function(r) { return r.json(); }).then(function(runs) {
    renderRunList(runs);
  }).catch(function() {});
}

function renderRunList(runs) {
  var el = document.getElementById('pipeline-run-list');
  if (!el) return;
  if (!runs || !runs.length) {
    el.innerHTML = '<div style="padding:8px 16px;font-size:0.7rem;color:#64748b">No runs yet</div>';
    return;
  }
  el.innerHTML = runs.slice(0, 20).map(function(r) {
    var status = r.status || 'completed';
    var dotCls = status === 'running' ? 'running' : (status === 'failed' ? 'failed' : 'completed');
    var ts = r.started_at ? new Date(r.started_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '';
    var date = r.started_at ? new Date(r.started_at).toLocaleDateString([], {month:'short',day:'numeric'}) : '';
    var trades = r.trades_placed != null ? r.trades_placed + ' trade' + (r.trades_placed !== 1 ? 's' : '') : '';
    var isSelected = r.run_id === _selectedRunId;
    return '<div class="run-item' + (isSelected ? ' selected' : '') + '" onclick="selectRun(\'' + escHtml(r.run_id) + '\')">' +
      '<div class="run-meta">' +
      '<span class="run-status-dot ' + dotCls + '"></span>' +
      '<span class="run-time">' + date + ' ' + ts + '</span>' +
      (trades ? '<span class="run-trades">' + escHtml(trades) + '</span>' : '') +
      '</div>' +
      '<div class="run-id-text">' + escHtml((r.run_id || '').slice(-18)) + '</div>' +
    '</div>';
  }).join('');
}

function selectRun(runId) {
  _selectedRunId = runId;
  fetch('/api/runs').then(function(r) { return r.json(); }).then(renderRunList).catch(function() {});
  fetch('/api/runs/' + encodeURIComponent(runId)).then(function(r) { return r.json(); }).then(function(run) {
    updateSidebarStageStatus(run);
    renderRunDetail(run);
  }).catch(function() {});
}
window.selectRun = selectRun;

function renderRunDetail(run) {
  var el = document.getElementById('run-detail-panel');
  if (!el) return;
  var stages = run.stages || {};
  var stageNames = ['scan', 'research', 'predict', 'risk'];
  var stageHtml = stageNames.map(function(s) {
    var st = stages[s];
    if (!st) return '';
    var color = st.status === 'completed' ? '#22c55e' : st.status === 'failed' ? '#ef4444' : '#f59e0b';
    var dur = st.duration_s != null ? st.duration_s.toFixed(1) + 's' : '';
    var count = st.count != null ? ' &bull; ' + st.count : '';
    return '<div style="display:flex;justify-content:space-between;font-size:0.68rem;padding:2px 0;border-bottom:1px solid #1e2535">' +
      '<span style="color:' + color + ';text-transform:uppercase;letter-spacing:0.05em">' + s + '</span>' +
      '<span style="color:#64748b">' + dur + count + '</span>' +
    '</div>';
  }).filter(Boolean).join('');

  var ts = run.started_at ? new Date(run.started_at).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '';
  var trades = run.trades_placed != null ? run.trades_placed + ' trade' + (run.trades_placed !== 1 ? 's' : '') : '';

  el.innerHTML =
    '<div style="font-size:0.65rem;color:#94a3b8;margin-bottom:6px;font-family:\'Geist Mono\',monospace">' + escHtml(ts) + (trades ? ' &bull; ' + escHtml(trades) : '') + '</div>' +
    stageHtml;
  el.style.display = 'block';
}
window.renderRunDetail = renderRunDetail;

/* =========================================================
   Sidebar stage status indicators
   ========================================================= */
function updateSidebarStageStatus(run) {
  if (!run) return;
  var stages = run.stages || {};
  ['scan', 'research', 'predict', 'risk'].forEach(function(stage) {
    var ind = document.getElementById('stage-ind-' + stage);
    if (!ind) return;
    var s = stages[stage];
    if (!s) { ind.innerHTML = ''; return; }
    if (s.status === 'completed') {
      ind.innerHTML = '<span class="material-symbols-outlined" style="font-size:0.9rem;color:#22c55e">check_circle</span>';
    } else if (s.status === 'running') {
      ind.innerHTML = '<span class="pulse-dot"></span>';
    } else if (s.status === 'failed') {
      ind.innerHTML = '<span class="material-symbols-outlined" style="font-size:0.9rem;color:#ef4444">cancel</span>';
    } else {
      ind.innerHTML = '';
    }
  });
}

/* =========================================================
   Helpers
   ========================================================= */
function _setStatNumber(id, value, colorCls) {
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = value != null ? value : '\u2014';
  if (colorCls) el.className = 'stat-number ' + colorCls;
}

/* =========================================================
   SSE
   ========================================================= */
var _es = null;

function _connectSSE() {
  if (_es) { _es.close(); }
  _es = new EventSource('/events');

  _es.onopen = function() {
    var el = document.getElementById('conn-status');
    if (el) { el.textContent = '\u25cf Live'; el.className = 'conn-live'; }
  };

  _es.onmessage = function(e) {
    try {
      var data = JSON.parse(e.data);
      _lastState = data;
      var tsEl = document.getElementById('last-updated');
      if (tsEl) tsEl.textContent = 'Updated ' + (data.updated_at || '');
      if (_currentView === 'risk')       updateRiskView();
      if (_currentView === 'postmortem') updatePostMortemView();
    } catch(err) {}
  };

  _es.addEventListener('run_update', function(e) {
    try {
      var run = JSON.parse(e.data);
      updateSidebarStageStatus(run);
      // Prepend to run list
      fetch('/api/runs').then(function(r) { return r.json(); }).then(renderRunList).catch(function() {});
    } catch(err) {}
  });

  _es.onerror = function() {
    var el = document.getElementById('conn-status');
    if (el) { el.textContent = '\u25cc Reconnecting\u2026'; el.className = 'conn-reconnecting'; }
    _es.close();
    setTimeout(_connectSSE, 3000);
  };
}

/* =========================================================
   Startup
   ========================================================= */
(function init() {
  var savedView = 'scan';
  var savedMode = 'paper';
  try { savedView = localStorage.getItem('dashboard_view') || 'scan'; } catch(e) {}
  try { savedMode = localStorage.getItem('dashboard_mode') || 'paper'; } catch(e) {}

  _currentMode = savedMode;
  var btnPaper = document.getElementById('toggle-paper');
  var btnLive  = document.getElementById('toggle-live');
  if (btnPaper) btnPaper.className = 'mode-btn' + (savedMode === 'paper' ? ' active' : '');
  if (btnLive)  btnLive.className  = 'mode-btn' + (savedMode === 'live' ? ' active' : '');

  _connectSSE();
  loadPipelineRuns();
  switchView(savedView);
})();
"""
