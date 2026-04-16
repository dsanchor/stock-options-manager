/* Stock Options Manager — Client-side JS */

// ── Clickable table rows ──
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.clickable-row[data-href]').forEach(function(row) {
        row.addEventListener('click', function(e) {
            if (e.target.closest('.btn-trigger-row')) return;
            window.location.href = this.dataset.href;
        });
    });

    // ── Run Now trigger buttons ──
    document.querySelectorAll('.btn-trigger[data-agent]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var agentType = btn.dataset.agent;
            var origText = btn.textContent;
            btn.textContent = '⏳ Running…';
            btn.classList.add('running');
            btn.disabled = true;

            fetch('/api/trigger/' + agentType, { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'triggered') {
                        btn.textContent = '✓ Triggered';
                        btn.classList.remove('running');
                        btn.classList.add('done');
                    } else {
                        btn.textContent = '✗ Error';
                        btn.classList.remove('running');
                        btn.classList.add('error');
                    }
                })
                .catch(function() {
                    btn.textContent = '✗ Error';
                    btn.classList.remove('running');
                    btn.classList.add('error');
                })
                .finally(function() {
                    setTimeout(function() {
                        btn.textContent = origText;
                        btn.disabled = false;
                        btn.classList.remove('running', 'done', 'error');
                    }, 3000);
                });
        });
    });

    // ── Row-level trigger buttons ──
    document.querySelectorAll('.btn-trigger-row[data-agent][data-symbol]').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            var agentType = btn.dataset.agent;
            var symbol = btn.dataset.symbol;
            var origText = btn.textContent;
            btn.textContent = '⏳';
            btn.classList.add('running');
            btn.disabled = true;

            fetch('/api/trigger/' + agentType, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: symbol })
            })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'triggered') {
                        btn.textContent = '✓';
                        btn.classList.remove('running');
                        btn.classList.add('done');
                    } else {
                        btn.textContent = '✗';
                        btn.classList.remove('running');
                        btn.classList.add('error');
                    }
                })
                .catch(function() {
                    btn.textContent = '✗';
                    btn.classList.remove('running');
                    btn.classList.add('error');
                })
                .finally(function() {
                    setTimeout(function() {
                        btn.textContent = origText;
                        btn.disabled = false;
                        btn.classList.remove('running', 'done', 'error');
                    }, 3000);
                });
        });
    });

    // ── Run Full Analysis button ──
    var runFullBtn = document.getElementById('run-full-analysis');
    if (runFullBtn) {
        var _fullAnalysisOrigText = runFullBtn.textContent;

        function _setIndividualButtons(disabled) {
            document.querySelectorAll('.btn-trigger[data-agent]').forEach(function(b) {
                b.disabled = disabled;
            });
            document.querySelectorAll('.btn-trigger-row[data-agent]').forEach(function(b) {
                b.disabled = disabled;
            });
        }

        function _pollFullAnalysis() {
            fetch('/api/trigger-all/status')
                .then(function(res) { return res.json(); })
                .then(function(s) {
                    if (s.running) {
                        var done = s.completed ? s.completed.length : 0;
                        var label = s.current || '…';
                        runFullBtn.textContent = '⏳ Running ' + (done + 1) + '/' + s.total + ': ' + label + '…';
                        setTimeout(_pollFullAnalysis, 4000);
                    } else {
                        var completed = s.completed ? s.completed.length : 0;
                        var errors = s.errors ? s.errors.length : 0;
                        if (errors > 0) {
                            runFullBtn.textContent = '⚠ Done (' + (completed - errors) + '/' + s.total + ', ' + errors + ' error' + (errors > 1 ? 's' : '') + ')';
                            runFullBtn.classList.remove('running');
                            runFullBtn.classList.add('error');
                        } else {
                            runFullBtn.textContent = '✓ Complete (' + completed + '/' + s.total + ')';
                            runFullBtn.classList.remove('running');
                            runFullBtn.classList.add('done');
                        }
                        setTimeout(function() {
                            runFullBtn.textContent = _fullAnalysisOrigText;
                            runFullBtn.disabled = false;
                            runFullBtn.classList.remove('running', 'done', 'error');
                            _setIndividualButtons(false);
                        }, 5000);
                    }
                })
                .catch(function() {
                    setTimeout(_pollFullAnalysis, 5000);
                });
        }

        runFullBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            runFullBtn.disabled = true;
            runFullBtn.textContent = '⏳ Starting…';
            runFullBtn.classList.add('running');
            _setIndividualButtons(true);

            fetch('/api/trigger-all', { method: 'POST' })
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.status === 'started') {
                        setTimeout(_pollFullAnalysis, 3000);
                    } else {
                        runFullBtn.textContent = '✗ ' + (data.error || 'Error');
                        runFullBtn.classList.remove('running');
                        runFullBtn.classList.add('error');
                        setTimeout(function() {
                            runFullBtn.textContent = _fullAnalysisOrigText;
                            runFullBtn.disabled = false;
                            runFullBtn.classList.remove('error');
                            _setIndividualButtons(false);
                        }, 4000);
                    }
                })
                .catch(function() {
                    runFullBtn.textContent = '✗ Network error';
                    runFullBtn.classList.remove('running');
                    runFullBtn.classList.add('error');
                    setTimeout(function() {
                        runFullBtn.textContent = _fullAnalysisOrigText;
                        runFullBtn.disabled = false;
                        runFullBtn.classList.remove('error');
                        _setIndividualButtons(false);
                    }, 4000);
                });
        });
    }
});

/* ── Filtering ─────────────────────────────────────────────────── */

function cutoffDate(days) {
    const d = new Date();
    d.setDate(d.getDate() - days);
    return d;
}

function applyDashboardFilters() {
    const activePill = document.querySelector('#activity-time-filter .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const symbolSelect = document.getElementById('activity-symbol-filter');
    const selectedSymbol = symbolSelect ? symbolSelect.value : '';
    const agentSelect = document.getElementById('activity-agent-filter');
    const selectedAgent = agentSelect ? agentSelect.value : '';
    const cutoff = cutoffDate(days);
    
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        const ts = new Date(item.dataset.timestamp);
        const sym = item.dataset.symbol || '';
        const agent = item.dataset.agentType || '';
        const timeOk = ts >= cutoff;
        const symOk = !selectedSymbol || sym === selectedSymbol;
        const agentOk = !selectedAgent || agent === selectedAgent;
        item.style.display = (timeOk && symOk && agentOk) ? '' : 'none';
    });
}

function applyTableFilter(pillContainerId, tableSelector, agentFilterId) {
    const activePill = document.querySelector('#' + pillContainerId + ' .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const cutoff = cutoffDate(days);
    const agentSelect = agentFilterId ? document.getElementById(agentFilterId) : null;
    const selectedAgent = agentSelect ? agentSelect.value : '';
    let visible = 0;
    
    document.querySelectorAll(tableSelector + ' tbody tr').forEach(row => {
        if (row.classList.contains('pos-detail-row')) return;
        const ts = new Date(row.dataset.timestamp);
        const agent = row.dataset.agentType || '';
        const timeOk = ts >= cutoff;
        const agentOk = !selectedAgent || agent === selectedAgent;
        const show = timeOk && agentOk;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    
    const header = document.querySelector('#' + pillContainerId)?.closest('.card-header');
    const badge = header?.querySelector('.card-badge');
    if (badge) badge.textContent = visible;
}

document.querySelectorAll('.filter-pills').forEach(container => {
    container.querySelectorAll('.pill').forEach(btn => {
        btn.addEventListener('click', () => {
            container.querySelectorAll('.pill').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            if (container.id === 'activity-time-filter') {
                applyDashboardFilters();
            } else if (container.id === 'sym-activity-time-filter') {
                applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter');
            }
        });
    });
});

const symFilter = document.getElementById('activity-symbol-filter');
if (symFilter) {
    const symbols = new Set();
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        if (item.dataset.symbol) symbols.add(item.dataset.symbol);
    });
    Array.from(symbols).sort().forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        symFilter.appendChild(opt);
    });
    symFilter.addEventListener('change', applyDashboardFilters);
}

// Populate agent type filter on dashboard
const dashAgentFilter = document.getElementById('activity-agent-filter');
if (dashAgentFilter) {
    const agents = new Map();
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        const key = item.dataset.agentType;
        if (key) {
            const label = item.querySelector('.activity-agent');
            if (label && !agents.has(key)) agents.set(key, label.textContent.trim());
        }
    });
    Array.from(agents.entries()).sort((a, b) => a[1].localeCompare(b[1])).forEach(([key, label]) => {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = label;
        dashAgentFilter.appendChild(opt);
    });
    dashAgentFilter.addEventListener('change', applyDashboardFilters);
}

// Populate agent type filter on symbol detail
const symAgentFilter = document.getElementById('sym-activity-agent-filter');
if (symAgentFilter) {
    const agents = new Map();
    document.querySelectorAll('#activities-table tbody tr').forEach(row => {
        const key = row.dataset.agentType;
        if (key && !agents.has(key)) {
            const agentCell = row.querySelectorAll('td')[1];
            if (agentCell) agents.set(key, agentCell.textContent.trim());
        }
    });
    Array.from(agents.entries()).sort((a, b) => a[1].localeCompare(b[1])).forEach(([key, label]) => {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = label;
        symAgentFilter.appendChild(opt);
    });
    symAgentFilter.addEventListener('change', function() {
        applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter');
    });
}

if (document.getElementById('activity-time-filter')) {
    applyDashboardFilters();
}
if (document.getElementById('sym-activity-time-filter')) {
    applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-activity-agent-filter');
}
