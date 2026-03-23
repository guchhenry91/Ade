/* ================================================================
   PARLAY BUILDER — parlay.js
   All probability calculations happen client-side.
   Only /api/parlay/analyze makes a server call.
================================================================ */

const parlayState = (function () {
  /* ── Constants ── */
  const MAX_LEGS = 8;
  const SPORT_EMOJI = { NBA: '🏀', MLB: '⚾', NHL: '🏒', NFL: '🏈', Soccer: '⚽' };

  /* ── State ── */
  let legs = [];
  let aiAnalysis = null;
  let aiLegsHash = null;
  let currentTab = 'builder';
  let _lastConfettiHash = null;

  /* ── SessionStorage persistence ── */
  function save() {
    try { sessionStorage.setItem('parlay_legs', JSON.stringify(legs)); } catch (_) {}
  }

  function load() {
    try {
      const raw = sessionStorage.getItem('parlay_legs');
      if (raw) legs = JSON.parse(raw);
    } catch (_) { legs = []; }
    render();
  }

  /* ── Leg helpers ── */
  function isDuplicate(leg) {
    return legs.some(l => l.player === leg.player && l.stat === leg.stat);
  }

  function legFromBtn(btn) {
    return {
      player:     btn.dataset.player || '',
      team:       btn.dataset.team   || '',
      sport:      btn.dataset.sport  || '',
      game:       btn.dataset.game   || '',
      stat:       btn.dataset.stat   || '',
      line:       parseFloat(btn.dataset.line  || 0),
      pick:       btn.dataset.pick   || 'OVER',
      /* confidence stored as 0-100 in data attr; convert to 0-1 */
      confidence: parseFloat(btn.dataset.confidence || 50) / 100,
      stars:      parseInt(btn.dataset.stars || 3, 10),
      trend:      btn.dataset.trend  || '',
      last5:      btn.dataset.last5  || '',
      avg:        btn.dataset.avg    || '',
    };
  }

  /* ── Add / Remove / Clear ── */
  function addLeg(leg) {
    if (legs.length >= MAX_LEGS) return false;
    if (isDuplicate(leg)) return false;
    legs.push(leg);
    aiAnalysis = null;   // invalidate cached analysis
    save();
    render();
    openPanel();
    return true;
  }

  function removeLeg(idx) {
    legs.splice(idx, 1);
    aiAnalysis = null;
    save();
    render();
  }

  function clearAll() {
    legs = [];
    aiAnalysis = null;
    _lastConfettiHash = null;
    save();
    render();
  }

  /* ── Toggle from button click ── */
  function toggleLeg(btn) {
    const leg = legFromBtn(btn);
    if (btn.classList.contains('added')) {
      const idx = legs.findIndex(l => l.player === leg.player && l.stat === leg.stat);
      if (idx > -1) removeLeg(idx);
    } else {
      if (legs.length >= MAX_LEGS) {
        /* Shake the panel to signal it's full */
        const target = document.getElementById('parlay-panel') || document.getElementById('parlay-badge');
        if (target) {
          target.classList.remove('parlay-shake');
          void target.offsetWidth; // force reflow
          target.classList.add('parlay-shake');
          target.addEventListener('animationend', () => target.classList.remove('parlay-shake'), { once: true });
        }
        _showToast('Maximum 8 legs reached');
        return;
      }
      const ok = addLeg(leg);
      if (ok) {
        /* glow animation on parent card element */
        const card = btn.closest('.prop-card, .player-card, .stat-section');
        if (card) {
          card.classList.add('parlay-glow');
          card.addEventListener('animationend', () => card.classList.remove('parlay-glow'), { once: true });
        }
      }
    }
  }

  /* ================================================================
     CALCULATIONS
  ================================================================ */

  /* Combined probability with same-game correlation penalty */
  function getCombinedProb() {
    if (legs.length === 0) return 0;
    let prob = legs.reduce((acc, l) => acc * l.confidence, 1);
    /* Count legs per game; apply 0.95 per same-game pair */
    const gameCounts = {};
    legs.forEach(l => { gameCounts[l.game] = (gameCounts[l.game] || 0) + 1; });
    Object.values(gameCounts).forEach(cnt => {
      if (cnt > 1) {
        const pairs = (cnt * (cnt - 1)) / 2;
        prob *= Math.pow(0.95, pairs);
      }
    });
    return prob;
  }

  /* Convert probability to American odds — always show sign */
  function toAmericanOdds(prob) {
    if (prob <= 0 || prob >= 1) return 0;
    if (prob >= 0.5) return Math.round(-(prob / (1 - prob)) * 100);
    return Math.round(((1 - prob) / prob) * 100);
  }

  /* Format American odds with mandatory sign */
  function fmtOdds(american) {
    return american >= 0 ? `+${american}` : `${american}`;
  }

  /*
   * Model edge vs a naive 55% benchmark.
   * edge = (our combined prob - benchmark_prob) / n_legs
   */
  function getModelEdge(combinedProb) {
    if (legs.length === 0 || combinedProb <= 0) return 0;
    const benchmark = Math.pow(0.55, legs.length);
    return (combinedProb - benchmark) / legs.length;
  }

  /* Grade data based on average confidence × leg-count multiplier */
  function getGradeData() {
    if (legs.length === 0) return null;
    const avgConf = legs.reduce((s, l) => s + l.confidence, 0) / legs.length;
    const mult = { 2: 1.0, 3: 0.95, 4: 0.88, 5: 0.80 }[legs.length] || 0.70;
    const score = avgConf * mult;

    let stars, label;
    if      (score >= 0.80) { stars = 5; label = 'Elite Parlay'; }
    else if (score >= 0.70) { stars = 4; label = 'Strong Parlay'; }
    else if (score >= 0.60) { stars = 3; label = 'Solid Parlay'; }
    else if (score >= 0.50) { stars = 2; label = 'Moderate Risk'; }
    else                    { stars = 1; label = 'High Risk — Use Caution'; }

    return { score, stars, label };
  }

  /* Quarter-Kelly stake recommendation clamped to [0.5%, 5%] */
  function getKelly() {
    const prob = getCombinedProb();
    if (prob <= 0 || legs.length === 0) return null;
    const american = toAmericanOdds(prob);
    const decOdds = american >= 0 ? american / 100 : 100 / Math.abs(american);
    const kelly = (prob * (decOdds + 1) - 1) / decOdds;
    const qk = kelly * 0.25;
    return { kelly, pct: Math.max(0.5, Math.min(5, qk * 100)) };
  }

  /* Warning flags */
  function getWarnings() {
    const warns = [];

    legs.forEach((l, i) => {
      if (l.confidence < 0.52) {
        warns.push({ type: 'warn', msg: `⚠️ Leg ${i + 1} (${l.player}) is a weak pick — consider removing` });
      }
    });

    const sportCounts = {};
    legs.forEach(l => { sportCounts[l.sport] = (sportCounts[l.sport] || 0) + 1; });
    Object.entries(sportCounts).forEach(([sport, cnt]) => {
      if (cnt >= 3) warns.push({ type: 'info', msg: `ℹ️ Heavy ${sport} exposure (${cnt} legs)` });
    });

    const games = [...new Set(legs.map(l => l.game))];
    if (legs.length >= 2 && games.length === 1) {
      warns.push({ type: 'danger', msg: '⚠️ All legs from same game — high correlation risk' });
    } else {
      const gameCounts = {};
      legs.forEach(l => { gameCounts[l.game] = (gameCounts[l.game] || 0) + 1; });
      if (Object.values(gameCounts).some(c => c > 1)) {
        warns.push({ type: 'warn', msg: '⚠️ Same-game parlay detected — higher risk' });
      }
    }

    const edge = getModelEdge(getCombinedProb());
    if (edge < 0) {
      warns.push({ type: 'danger', msg: '⚠️ Combined edge is negative — book has advantage' });
    }

    return warns;
  }

  /* ================================================================
     AUTO-SUGGEST BEST PARLAY
  ================================================================ */
  function buildBestParlay() {
    const allBtns = Array.from(document.querySelectorAll('.parlay-add-btn'));
    const available = allBtns.filter(b => !b.classList.contains('added') && !b.disabled);

    if (available.length === 0) {
      _showAutoMsg('No available props on this page to suggest from.');
      return;
    }

    /* Require ≥65% confidence */
    const pool = available
      .map(btn => ({ btn, conf: parseFloat(btn.dataset.confidence || 50) }))
      .filter(c => c.conf >= 65)
      .sort((a, b) => b.conf - a.conf);

    /* Pick exactly 3 from different games */
    const usedGames = new Set();
    const picked = [];
    for (const { btn } of pool) {
      if (picked.length >= 3) break;
      const game = btn.dataset.game || '';
      if (!usedGames.has(game)) {
        usedGames.add(game);
        picked.push(btn);
      }
    }

    if (picked.length < 3) {
      _showAutoMsg('Not enough high-confidence picks today');
      return;
    }

    clearAll();
    picked.forEach(btn => toggleLeg(btn));
    _showAutoMsg('Auto-built from today\'s top picks (≥65% confidence)');
  }

  function _showAutoMsg(msg) {
    const el = document.getElementById('parlay-autosuggest-msg');
    if (!el) return;
    el.textContent = msg;
    el.style.display = 'block';
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.style.display = 'none'; }, 4500);
  }

  /* ================================================================
     COPY PARLAY
  ================================================================ */
  function copyParlay() {
    if (legs.length < 2) {
      _showToast('Add at least 2 legs first');
      return;
    }
    const prob     = getCombinedProb();
    const american = toAmericanOdds(prob);
    const grade    = getGradeData();
    const edge     = getModelEdge(prob);
    const starsStr = grade ? '★'.repeat(grade.stars) + '☆'.repeat(5 - grade.stars) : '';

    const text = [
      '🎯 My Parlay — BetModel Pro',
      '━━━━━━━━━━━━━━━━━━━━━━━━',
      ...legs.map(l =>
        `✅ ${l.player} ${l.stat} ${l.pick} ${l.line} (${Math.round(l.confidence * 100)}%)`
      ),
      '━━━━━━━━━━━━━━━━━━━━━━━━',
      `Combined: ${(prob * 100).toFixed(1)}% | ${fmtOdds(american)} odds`,
      grade ? `Grade: ${starsStr} ${grade.label}` : '',
      `Model Edge: ${edge >= 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`,
      '━━━━━━━━━━━━━━━━━━━━━━━━',
      'Built with BetModel Pro',
    ].filter(Boolean).join('\n');

    const copyDone = () => {
      const btn = document.getElementById('parlay-copy-btn');
      if (!btn) return;
      const orig = btn.innerHTML;
      btn.innerHTML = '✓ Copied!';
      btn.style.color = 'var(--over)';
      setTimeout(() => { btn.innerHTML = orig; btn.style.color = ''; }, 2000);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(copyDone).catch(() => _clipboardFallback(text, copyDone));
    } else {
      _clipboardFallback(text, copyDone);
    }
  }

  function _clipboardFallback(text, cb) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;top:-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); cb(); } catch (_) {}
    document.body.removeChild(ta);
  }

  /* ================================================================
     SAVE PARLAY
  ================================================================ */
  function saveParlay() {
    if (legs.length < 2) return;
    const prob    = getCombinedProb();
    const american = toAmericanOdds(prob);
    const grade   = getGradeData();
    const saved   = getSavedParlays();

    saved.unshift({
      id:           Date.now(),
      date:         new Date().toLocaleDateString(),
      legs:         legs.map(l => ({
        player:     l.player,
        stat:       l.stat,
        line:       l.line,
        pick:       l.pick,
        confidence: l.confidence,
      })),
      combinedProb: prob,
      americanOdds: american,
      grade:        grade ? grade.label : '',
      gradeStars:   grade ? grade.stars : 0,
      result:       'pending',
    });

    if (saved.length > 50) saved.splice(50);
    try { localStorage.setItem('saved_parlays', JSON.stringify(saved)); } catch (_) {}

    renderSaved();

    const btn = document.getElementById('parlay-save-btn');
    if (btn) {
      const orig = btn.innerHTML;
      btn.innerHTML = '✓ Saved!';
      setTimeout(() => { btn.innerHTML = orig; }, 2000);
    }
  }

  function getSavedParlays() {
    try { return JSON.parse(localStorage.getItem('saved_parlays') || '[]'); } catch (_) { return []; }
  }

  function updateSavedResult(id, result) {
    const saved = getSavedParlays();
    const item  = saved.find(p => p.id === id);
    if (!item) return;
    item.result = item.result === result ? 'pending' : result;
    try { localStorage.setItem('saved_parlays', JSON.stringify(saved)); } catch (_) {}
    renderSaved();
  }

  function deleteSaved(id) {
    const saved = getSavedParlays().filter(p => p.id !== id);
    try { localStorage.setItem('saved_parlays', JSON.stringify(saved)); } catch (_) {}
    renderSaved();
  }

  /* ================================================================
     AI ANALYSIS
  ================================================================ */
  async function analyzeParlay() {
    if (legs.length < 2) {
      _showToast('Add at least 2 legs first');
      return;
    }

    const hash = legs.map(l => `${l.player}|${l.stat}|${l.line}|${l.pick}`).join(',');

    /* Use cached analysis if legs haven't changed */
    if (aiAnalysis && aiLegsHash === hash) {
      _showAI(aiAnalysis);
      return;
    }

    const prob  = getCombinedProb();
    const grade = getGradeData();
    const edge  = getModelEdge(prob);
    const sec   = _getOrCreateAISection();

    sec.style.display = 'block';
    sec.innerHTML = '<div class="parlay-ai-loading"><span class="parlay-ai-spinner"></span> Analyzing parlay…</div>';

    try {
      const res = await fetch('/api/parlay/analyze', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          legs:          legs,
          combined_prob: parseFloat((prob * 100).toFixed(1)),
          grade:         grade ? grade.label : '',
          grade_stars:   grade ? grade.stars : 0,
          edge:          parseFloat((edge * 100).toFixed(2)),
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `HTTP ${res.status}`);
      }

      const data = await res.json();
      aiAnalysis  = data.analysis;
      aiLegsHash  = hash;
      _showAI(aiAnalysis);
    } catch (err) {
      sec.innerHTML = `<div class="parlay-ai-text" style="color:var(--under);">Analysis unavailable: ${escHtml(err.message)}</div>`;
    }
  }

  function _showAI(text) {
    const sec = _getOrCreateAISection();
    sec.style.display = 'block';
    sec.innerHTML = `<div class="parlay-ai-text">${escHtml(text).replace(/\n/g, '<br>')}</div>`;
  }

  function _getOrCreateAISection() {
    let sec = document.getElementById('parlay-ai-section');
    if (!sec) {
      sec = document.createElement('div');
      sec.id = 'parlay-ai-section';
      sec.className = 'parlay-ai-section';
      const summary = document.getElementById('parlay-summary');
      if (summary) summary.appendChild(sec);
    }
    return sec;
  }

  /* ================================================================
     PANEL OPEN / CLOSE / BADGE
  ================================================================ */
  function openPanel() {
    const panel = document.getElementById('parlay-panel');
    const badge = document.getElementById('parlay-badge');
    if (panel) panel.classList.add('open');
    if (badge) badge.style.display = 'none';
    badge && badge.classList.remove('has-legs');
  }

  function closePanel() {
    const panel = document.getElementById('parlay-panel');
    if (panel) panel.classList.remove('open');
    _updateBadge();
  }

  function _updateBadge() {
    const badge = document.getElementById('parlay-badge');
    if (!badge) return;
    const panel  = document.getElementById('parlay-panel');
    const isOpen = panel && panel.classList.contains('open');
    if (legs.length > 0 && !isOpen) {
      badge.classList.add('has-legs');
      badge.innerHTML = `🎯 Parlay (${legs.length})`;
    } else {
      badge.classList.remove('has-legs');
    }
  }

  /* Update nav-bar parlay badge */
  function _updateNavBadge() {
    const btn = document.getElementById('parlay-nav-btn');
    if (!btn) return;
    if (legs.length > 0) {
      btn.classList.add('has-legs');
      btn.textContent = `🎯 Parlay (${legs.length})`;
    } else {
      btn.classList.remove('has-legs');
    }
  }

  /* ================================================================
     TAB SWITCHING
  ================================================================ */
  function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.parlay-tab').forEach(
      t => t.classList.toggle('active', t.dataset.tab === tab)
    );
    document.querySelectorAll('.parlay-tab-content').forEach(
      c => c.classList.toggle('active', c.dataset.tab === tab)
    );
    if (tab === 'saved') renderSaved();
  }

  /* ================================================================
     RENDER
  ================================================================ */
  function render() {
    renderLegs();
    renderSummary();
    renderButtons();
    _updateBadge();
    _updateNavBadge();
    _maybeConfetti();
  }

  /* ── Render legs list ── */
  function renderLegs() {
    const container = document.getElementById('parlay-legs');
    if (!container) return;

    if (legs.length === 0) {
      container.innerHTML = `
        <div class="parlay-empty">
          <div class="pei">🎯</div>
          <p>Add picks to build your parlay.<br>Browse props and click <strong style="color:var(--accent)">+ Add to Parlay</strong>.</p>
        </div>
        <button class="parlay-build-best" onclick="parlayState.buildBest()">🎯 Build Best Parlay</button>
        <div id="parlay-autosuggest-msg"></div>
      `;
      return;
    }

    /* Find best and weakest leg indices */
    const bestIdx = legs.reduce((bi, l, i) => l.confidence > legs[bi].confidence ? i : bi, 0);
    const weakIdx = legs.reduce((wi, l, i) => l.confidence < legs[wi].confidence ? i : wi, 0);
    /* Only mark best/weak if they're different legs */
    const markBest = legs.length > 1;
    const markWeak = legs.length > 1 && weakIdx !== bestIdx;

    const gameCounts = {};
    legs.forEach(l => { gameCounts[l.game] = (gameCounts[l.game] || 0) + 1; });

    let html = '<div id="parlay-autosuggest-msg"></div>';
    legs.forEach((leg, i) => {
      const pct       = Math.round(leg.confidence * 100);
      const starsHtml = _starsHtml(leg.stars);
      const isSGP     = gameCounts[leg.game] > 1;
      const confColor = pct >= 65 ? 'var(--over)' : pct >= 55 ? '#ffa500' : 'var(--under)';
      const sportEmoji = SPORT_EMOJI[leg.sport] || '🎯';

      const isBest = markBest && i === bestIdx;
      const isWeak = markWeak && i === weakIdx;
      const legClass = isBest ? ' best-leg' : isWeak ? ' weak-leg' : '';

      const badgeHtml = isBest
        ? '<span class="parlay-leg-badge best">⭐ Best leg</span>'
        : isWeak
          ? '<span class="parlay-leg-badge weak">⚠️ Weakest leg</span>'
          : '';

      html += `
        <div class="parlay-leg${legClass}" id="parlay-leg-${i}" draggable="true" data-idx="${i}">
          <div style="display:flex;align-items:flex-start;gap:6px;">
            <span class="parlay-leg-drag" title="Drag to reorder">⠿</span>
            <div style="flex:1;min-width:0;">
              <div class="parlay-leg-sport">${sportEmoji} ${escHtml(leg.sport)} · ${escHtml(leg.game)}</div>
              <div class="parlay-leg-player">${escHtml(leg.player)}</div>
              <div class="parlay-leg-prop">${escHtml(leg.stat)} ${escHtml(leg.pick)} ${leg.line}</div>
              ${isSGP ? '<span class="parlay-sgp-warn">⚠️ Same-game</span>' : ''}
              ${badgeHtml}
              <div class="parlay-leg-meta">
                <span class="parlay-leg-conf" style="color:${confColor}">${pct}%</span>
                <span class="parlay-leg-stars">${starsHtml}</span>
                <button class="parlay-leg-remove" onclick="parlayState.remove(${i})">✕ Remove</button>
              </div>
            </div>
          </div>
        </div>
      `;
    });

    /* 1-leg hint */
    if (legs.length === 1) {
      html += `<div class="parlay-one-leg-hint">Add 1 more leg to build a parlay</div>`;
    }

    container.innerHTML = html;

    /* Wire up drag-and-drop */
    _initDragAndDrop(container);
  }

  /* ── Render summary card ── */
  function renderSummary() {
    const container = document.getElementById('parlay-summary');
    const actionsEl = document.getElementById('parlay-actions');
    if (!container) return;

    if (legs.length < 2) {
      container.style.display = 'none';
      if (actionsEl) actionsEl.style.display = 'none';
      return;
    }

    container.style.display = 'block';
    if (actionsEl) actionsEl.style.display = 'flex';

    const prob     = getCombinedProb();
    const american = toAmericanOdds(prob);
    const edge     = getModelEdge(prob);
    const grade    = getGradeData();
    const kelly    = getKelly();
    const warns    = getWarnings();

    const americanStr = fmtOdds(american);
    const edgeStr     = edge >= 0 ? `+${(edge * 100).toFixed(1)}%` : `${(edge * 100).toFixed(1)}%`;
    const edgeClass   = edge >= 0 ? 'positive' : 'negative';

    const gradeStarsHtml = grade ? _starsHtml(grade.stars) : '';

    let stakeHtml = '';
    if (kelly) {
      stakeHtml = kelly.kelly <= 0
        ? '<div class="parlay-stake">⚠️ No positive edge detected — not recommended</div>'
        : `<div class="parlay-stake">Recommended Stake: <strong>${kelly.pct.toFixed(1)}% of bankroll</strong></div>`;
    }

    const warnsHtml = warns.map(w =>
      `<div class="parlay-warn-item ${w.type === 'info' ? 'info' : w.type === 'danger' ? 'danger' : ''}">${escHtml(w.msg)}</div>`
    ).join('');

    container.innerHTML = `
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--txt3);margin-bottom:8px;">Parlay Summary</div>
      <div class="parlay-summary-row">
        <span class="parlay-summary-label">Legs</span>
        <span class="parlay-summary-val">${legs.length} / ${MAX_LEGS}</span>
      </div>
      <div class="parlay-summary-row">
        <span class="parlay-summary-label">Combined Prob</span>
        <span class="parlay-summary-val">${(prob * 100).toFixed(1)}%</span>
      </div>
      <div class="parlay-pbar"><div class="parlay-pbar-fill" style="width:${Math.min(100, (prob * 100)).toFixed(1)}%"></div></div>
      <div class="parlay-summary-row">
        <span class="parlay-summary-label">Implied Odds</span>
        <span class="parlay-summary-val">${americanStr}</span>
      </div>
      <div class="parlay-summary-row">
        <span class="parlay-summary-label">Model Edge</span>
        <span class="parlay-summary-val ${edgeClass}">${edgeStr}</span>
      </div>
      ${grade ? `
      <div class="parlay-grade-section">
        <div class="parlay-grade-stars">${gradeStarsHtml}</div>
        <div class="parlay-grade-label">${escHtml(grade.label)}</div>
      </div>` : ''}
      ${stakeHtml}
      ${warnsHtml ? `<div class="parlay-warn-list">${warnsHtml}</div>` : ''}
      <div id="parlay-ai-section" class="parlay-ai-section" style="display:none;"></div>
    `;
  }

  /* ── Update all Add-to-Parlay button states ── */
  function renderButtons() {
    const full = legs.length >= MAX_LEGS;
    document.querySelectorAll('.parlay-add-btn').forEach(btn => {
      const inParlay = legs.some(l => l.player === btn.dataset.player && l.stat === btn.dataset.stat);
      btn.classList.toggle('added', inParlay);
      if (inParlay) {
        btn.innerHTML = '✓ Added';
        btn.disabled  = false;
      } else if (full) {
        btn.innerHTML = '🔒 Full';
        btn.disabled  = true;
      } else {
        btn.innerHTML = '+ Add to Parlay';
        btn.disabled  = false;
      }
    });
  }

  /* ── Render saved parlays tab ── */
  function renderSaved() {
    const container = document.getElementById('parlay-saved-list');
    if (!container) return;

    const saved = getSavedParlays();
    if (saved.length === 0) {
      container.innerHTML = `
        <div class="parlay-empty">
          <div class="pei">💾</div>
          <p>No saved parlays yet.<br>Save your current parlay from the Builder tab.</p>
        </div>`;
      return;
    }

    container.innerHTML = saved.map(p => {
      const legsText  = p.legs.map(l => `${l.player} ${l.stat} ${l.pick} ${l.line}`).join('\n');
      const probStr   = (p.combinedProb * 100).toFixed(1);
      const oddsStr   = fmtOdds(p.americanOdds);
      const starsHtml = _starsHtml(p.gradeStars || 0);
      const resColor  = p.result === 'won' ? 'var(--over)' : p.result === 'lost' ? 'var(--under)' : 'var(--txt3)';

      return `
        <div class="saved-parlay-item">
          <div class="saved-parlay-date">${escHtml(p.date)} · ${p.legs.length} legs · ${escHtml(p.grade || '')}</div>
          <div style="font-size:13px;color:var(--star);letter-spacing:2px;margin-bottom:4px;">${starsHtml}</div>
          <div class="saved-parlay-legs">${escHtml(legsText).replace(/\n/g, '<br>')}</div>
          <div class="saved-parlay-meta">
            <span style="color:var(--txt2);font-size:11px;">${probStr}% · ${oddsStr}</span>
            <div style="display:flex;align-items:center;gap:4px;">
              <span style="font-size:10px;color:${resColor};font-weight:700;text-transform:uppercase;">${escHtml(p.result)}</span>
              <div style="display:flex;gap:3px;">
                <button class="result-btn ${p.result === 'won' ? 'won' : ''}" onclick="parlayState.setResult(${p.id},'won')">W</button>
                <button class="result-btn ${p.result === 'lost' ? 'lost' : ''}" onclick="parlayState.setResult(${p.id},'lost')">L</button>
                <button class="result-btn" onclick="parlayState.delSaved(${p.id})" style="border-color:rgba(255,68,68,.3);color:#ff4444;" title="Delete">✕</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join('');
  }

  /* ================================================================
     UTILITIES
  ================================================================ */
  function _starsHtml(n) {
    const filled = '★'.repeat(Math.max(0, Math.min(5, n)));
    const empty  = `<span style="color:#333355">★</span>`.repeat(Math.max(0, 5 - n));
    return filled + empty;
  }

  /* Safe HTML escaping */
  function escHtml(str) {
    if (!str && str !== 0) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /* ── Toast notification (empty-state guard) ── */
  function _showToast(msg) {
    const panel = document.getElementById('parlay-panel');
    if (!panel) return;
    const old = panel.querySelector('.parlay-toast');
    if (old) old.remove();
    const toast = document.createElement('div');
    toast.className = 'parlay-toast';
    toast.textContent = msg;
    panel.appendChild(toast);
    /* Open panel so user sees the toast */
    openPanel();
    setTimeout(() => {
      toast.classList.add('fade');
      setTimeout(() => { if (toast.parentNode) toast.remove(); }, 400);
    }, 2500);
  }

  /* ── Confetti on 5-star parlay ── */
  function _maybeConfetti() {
    const grade = getGradeData();
    if (!grade || grade.stars < 5) return;
    const hash = legs.map(l => l.player + l.stat).join(',');
    if (hash === _lastConfettiHash) return; // already fired
    _lastConfettiHash = hash;
    if (typeof confetti === 'function') {
      confetti({ particleCount: 90, spread: 65, origin: { y: 0.55 } });
    } else {
      /* CSS fallback: brief gold glow on panel */
      const panel = document.getElementById('parlay-panel');
      if (panel) {
        const orig = panel.style.boxShadow;
        panel.style.boxShadow = '0 0 48px rgba(245,158,11,.65)';
        setTimeout(() => { panel.style.boxShadow = orig; }, 900);
      }
    }
  }

  /* ── Drag-and-drop reordering ── */
  function _initDragAndDrop(container) {
    const legEls = Array.from(container.querySelectorAll('.parlay-leg[draggable]'));
    let dragIdx = null;

    legEls.forEach(el => {
      const idx = parseInt(el.dataset.idx, 10);

      el.addEventListener('dragstart', e => {
        dragIdx = idx;
        el.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });

      el.addEventListener('dragend', () => {
        el.classList.remove('dragging');
        legEls.forEach(e2 => e2.classList.remove('drag-over'));
        dragIdx = null;
      });

      el.addEventListener('dragover', e => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        if (idx !== dragIdx) el.classList.add('drag-over');
      });

      el.addEventListener('dragleave', () => el.classList.remove('drag-over'));

      el.addEventListener('drop', e => {
        e.preventDefault();
        el.classList.remove('drag-over');
        if (dragIdx === null || dragIdx === idx) return;
        const [moved] = legs.splice(dragIdx, 1);
        legs.splice(idx, 0, moved);
        save();
        render();
      });
    });
  }

  /* ================================================================
     INIT
  ================================================================ */
  function init() {
    load();  // loads from sessionStorage and calls render()

    /* Wire up tab buttons */
    document.querySelectorAll('.parlay-tab').forEach(btn => {
      btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
  }

  /* ── Public API ── */
  return {
    addLeg,
    remove:        removeLeg,
    clearAll,
    toggleLeg,
    buildBest:     buildBestParlay,
    copyParlay,
    saveParlay,
    getSavedParlays,
    setResult:     updateSavedResult,
    delSaved:      deleteSaved,
    analyzeParlay,
    openPanel,
    closePanel,
    switchTab,
    init,
    /* exposed for debugging */
    _getLegs:      () => legs,
  };
})();

document.addEventListener('DOMContentLoaded', () => { parlayState.init(); });
