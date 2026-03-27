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
});
