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
