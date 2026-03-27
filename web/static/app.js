/* Options Agent — Client-side JS */

// ── Clickable table rows ──
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.clickable-row[data-href]').forEach(function(row) {
        row.addEventListener('click', function() {
            window.location.href = this.dataset.href;
        });
    });

    // ── Auto-refresh toggle ──
    var toggle = document.getElementById('autoRefresh');
    if (toggle) {
        var intervalId = null;
        var savedPref = localStorage.getItem('autoRefresh');
        if (savedPref === 'true') {
            toggle.checked = true;
            intervalId = setInterval(function() { window.location.reload(); }, 60000);
        }
        toggle.addEventListener('change', function() {
            localStorage.setItem('autoRefresh', toggle.checked);
            if (toggle.checked) {
                intervalId = setInterval(function() { window.location.reload(); }, 60000);
            } else if (intervalId) {
                clearInterval(intervalId);
                intervalId = null;
            }
        });
    }

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
