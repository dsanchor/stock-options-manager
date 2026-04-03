/* Stock Options Manager — Client-side JS */

// ── Clickable table rows ──
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.clickable-row[data-href]').forEach(function(row) {
        row.addEventListener('click', function() {
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

    // ── Run Full Analysis button ──
    var runFullBtn = document.getElementById('run-full-analysis');
    if (runFullBtn) {
        runFullBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            var agents = ['covered_call', 'cash_secured_put', 'open_call_monitor', 'open_put_monitor'];
            var originalText = runFullBtn.textContent;
            
            runFullBtn.disabled = true;
            runFullBtn.textContent = '⏳ Running...';
            runFullBtn.classList.add('running');
            
            var success = 0;
            var failed = 0;
            
            // Sequential execution using promise chain
            agents.reduce(function(promise, agentType, index) {
                return promise.then(function() {
                    runFullBtn.textContent = '⏳ Running... (' + (index + 1) + '/' + agents.length + ')';
                    return fetch('/api/trigger/' + agentType, { method: 'POST' })
                        .then(function(resp) {
                            if (resp.ok) {
                                success++;
                            } else {
                                failed++;
                            }
                        })
                        .catch(function() {
                            failed++;
                        });
                });
            }, Promise.resolve())
            .then(function() {
                runFullBtn.textContent = '✓ Complete (' + success + '/' + agents.length + ')';
                runFullBtn.classList.remove('running');
                runFullBtn.classList.add('done');
            })
            .catch(function() {
                runFullBtn.textContent = '✗ Error';
                runFullBtn.classList.remove('running');
                runFullBtn.classList.add('error');
            })
            .finally(function() {
                setTimeout(function() {
                    runFullBtn.textContent = originalText;
                    runFullBtn.disabled = false;
                    runFullBtn.classList.remove('running', 'done', 'error');
                }, 3000);
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
    const cutoff = cutoffDate(days);
    
    document.querySelectorAll('.activity-feed .activity-item').forEach(item => {
        const ts = new Date(item.dataset.timestamp);
        const sym = item.dataset.symbol || '';
        const timeOk = ts >= cutoff;
        const symOk = !selectedSymbol || sym === selectedSymbol;
        item.style.display = (timeOk && symOk) ? '' : 'none';
    });
}

function applyTableFilter(pillContainerId, tableSelector, alertsOnlyBtnId = null) {
    const activePill = document.querySelector('#' + pillContainerId + ' .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const cutoff = cutoffDate(days);
    
    // Check if alerts-only filter is active
    const alertsOnlyBtn = alertsOnlyBtnId ? document.getElementById(alertsOnlyBtnId) : null;
    const alertsOnly = alertsOnlyBtn ? alertsOnlyBtn.classList.contains('active') : false;
    
    let visible = 0;
    
    document.querySelectorAll(tableSelector + ' tbody tr').forEach(row => {
        if (row.classList.contains('pos-detail-row')) return;
        
        const ts = new Date(row.dataset.timestamp);
        const isAlert = row.dataset.isAlert === 'true';
        
        // Apply both time and alerts-only filters
        const showTime = ts >= cutoff;
        const showAlert = !alertsOnly || isAlert;
        const show = showTime && showAlert;
        
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
                applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-alerts-only-filter');
            }
        });
    });
});

// Alerts-only filter toggle
const alertsOnlyBtn = document.getElementById('sym-alerts-only-filter');
if (alertsOnlyBtn) {
    alertsOnlyBtn.addEventListener('click', () => {
        alertsOnlyBtn.classList.toggle('active');
        applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-alerts-only-filter');
    });
}

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

if (document.getElementById('activity-time-filter')) {
    applyDashboardFilters();
}
if (document.getElementById('sym-activity-time-filter')) {
    applyTableFilter('sym-activity-time-filter', '#activities-table', 'sym-alerts-only-filter');
}
