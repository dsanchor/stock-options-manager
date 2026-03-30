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

function applyTableFilter(pillContainerId, tableSelector) {
    const activePill = document.querySelector('#' + pillContainerId + ' .pill.active');
    const days = activePill ? parseInt(activePill.dataset.range, 10) : 7;
    const cutoff = cutoffDate(days);
    let visible = 0;
    
    document.querySelectorAll(tableSelector + ' tbody tr').forEach(row => {
        if (row.classList.contains('pos-detail-row')) return;
        const ts = new Date(row.dataset.timestamp);
        const show = ts >= cutoff;
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
                applyTableFilter('sym-activity-time-filter', '#activities-table');
            } else if (container.id === 'sym-alert-time-filter') {
                applyTableFilter('sym-alert-time-filter', '#alerts-table');
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

if (document.getElementById('activity-time-filter')) {
    applyDashboardFilters();
}
if (document.getElementById('sym-activity-time-filter')) {
    applyTableFilter('sym-activity-time-filter', '#activities-table');
}
if (document.getElementById('sym-alert-time-filter')) {
    applyTableFilter('sym-alert-time-filter', '#alerts-table');
}
