// Dashboard JavaScript for Cullarr

let scoreQueuePage = 1;
let scoreQueuePerPage = 20;
let refreshInterval = null;

// Load all dashboard data
async function loadDashboard() {
    await loadQueueStatus();
    await loadScheduledDeletions();
    await loadScoreQueue();
    await loadFailedDeletions();
    await loadNextRunTimes();
}

// Queue Status
async function loadQueueStatus() {
    try {
        const res = await fetch('/api/dashboard/queue-status');
        const data = await res.json();
        
        document.getElementById('scheduled-count').textContent = data.scheduled_count;
        const percent = data.percent_used || 0;
        document.getElementById('queue-bar').style.width = `${percent}%`;
        document.getElementById('queue-percent').textContent = `${percent}%`;
        document.getElementById('queue-cap').textContent = `of ${data.max_queued} cap`;
        
        // Radarr status
        const radarrDot = document.getElementById('radarr-status-dot');
        const radarrText = document.getElementById('radarr-status-text');
        if (data.radarr.configured && data.radarr.status === 'connected') {
            radarrDot.className = 'status-dot status-connected';
            radarrText.textContent = 'Radarr: Connected';
        } else if (data.radarr.configured && data.radarr.status === 'error') {
            radarrDot.className = 'status-dot status-error';
            radarrText.textContent = 'Radarr: Connection error';
        } else {
            radarrDot.className = 'status-dot status-unknown';
            radarrText.textContent = 'Radarr: Not configured';
        }
        
        // Plex status
        const plexDot = document.getElementById('plex-status-dot');
        const plexText = document.getElementById('plex-status-text');
        const plexDetails = document.getElementById('plex-details');
        if (data.plex.configured && data.plex.status === 'connected') {
            plexDot.className = 'status-dot status-connected';
            plexText.textContent = 'Plex: Connected';
            plexDetails.textContent = data.plex.details || '';
        } else if (data.plex.configured && data.plex.status === 'error') {
            plexDot.className = 'status-dot status-error';
            plexText.textContent = 'Plex: API error';
            plexDetails.textContent = 'Check connection in Settings';
        } else if (data.plex.configured) {
            plexDot.className = 'status-dot status-unknown';
            plexText.textContent = 'Plex: Not connected';
            plexDetails.textContent = '';
        } else {
            plexDot.className = 'status-dot status-unknown';
            plexText.textContent = 'Plex: Not configured';
            plexDetails.textContent = '';
        }
    } catch (e) {
        console.error('Failed to load queue status:', e);
    }
}

// Scheduled Deletions
async function loadScheduledDeletions() {
    try {
        const res = await fetch('/api/dashboard/scheduled?limit=50');
        const data = await res.json();
        const tbody = document.getElementById('scheduled-table');
        document.getElementById('scheduled-badge').textContent = `${data.count} items`;
        
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No scheduled deletions</td></tr>';
            return;
        }
        
        tbody.innerHTML = data.items.map(item => {
            let scoreClass = 'score-high';
            if (item.score < 40) scoreClass = 'score-medium';
            if (item.score < 25) scoreClass = 'score-low';
            const deleteDate = new Date(item.scheduled_date).toLocaleDateString();
            return `
                <tr style="border-bottom: 1px solid var(--border-color);">
                    <td class="px-4 py-2">${escapeHtml(item.movie_title)} (${item.movie_year || 'N/A'})</td>
                    <td class="px-4 py-2"><span class="badge ${scoreClass}">${item.score.toFixed(1)}</span></td>
                    <td class="px-4 py-2">${deleteDate}</td>
                    <td class="px-4 py-2"><span class="badge" style="background: var(--info-bg); color: var(--info);">scheduled</span></td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to load scheduled deletions:', e);
    }
}

// Score Queue
async function loadScoreQueue() {
    try {
        const res = await fetch(`/api/dashboard/score-queue?page=${scoreQueuePage}&per_page=${scoreQueuePerPage}`);
        const data = await res.json();
        const tbody = document.getElementById('score-queue-table');
        
        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No movies found. Configure Radarr first.</td></tr>';
            document.getElementById('score-queue-pagination').innerHTML = '';
            return;
        }
        
        tbody.innerHTML = data.items.map(movie => {
            let scoreClass = 'score-high';
            if (movie.normalized_score < 40) scoreClass = 'score-medium';
            if (movie.normalized_score < 25) scoreClass = 'score-low';
            const tmdbRating = movie.tmdb_rating ? movie.tmdb_rating.toFixed(1) : 'N/A';
            return `
                <tr style="border-bottom: 1px solid var(--border-color);">
                    <td class="px-4 py-2"><span class="badge ${scoreClass}">${movie.normalized_score.toFixed(1)}</span></td>
                    <td class="px-4 py-2 font-medium">${escapeHtml(movie.movie_title)}</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.movie_year || 'N/A'}</td>
                    <td class="px-4 py-2"><span class="star">★</span> ${tmdbRating}</td>
                    <td class="px-4 py-2">${movie.size_gb.toFixed(1)} GB</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${escapeHtml(movie.quality) || 'Unknown'}</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.age_days}d</td>
                    <td class="px-4 py-2"><button onclick="showScoreDetails(${JSON.stringify(escapeHtml(movie.movie_title))}, ${movie.normalized_score}, ${JSON.stringify(movie.factors)})" class="btn-sm btn-outline">🔍 Details</button></td>
                </tr>
            `;
        }).join('');
        
        // Pagination
        const totalPages = data.pages || 1;
        document.getElementById('score-queue-pagination').innerHTML = `
            <span style="color: var(--text-secondary)">${data.total} total movies</span>
            <div class="flex gap-2">
                <button onclick="changeScoreQueuePage(${scoreQueuePage - 1})" class="btn-sm btn-outline" ${scoreQueuePage <= 1 ? 'disabled' : ''}>← Prev</button>
                <span class="text-sm">Page ${scoreQueuePage} of ${totalPages}</span>
                <button onclick="changeScoreQueuePage(${scoreQueuePage + 1})" class="btn-sm btn-outline" ${scoreQueuePage >= totalPages ? 'disabled' : ''}>Next →</button>
            </div>
        `;
    } catch (e) {
        console.error('Failed to load score queue:', e);
    }
}

function changeScoreQueuePage(page) {
    scoreQueuePage = page;
    loadScoreQueue();
}

// Failed Deletions
async function loadFailedDeletions() {
    try {
        const res = await fetch('/api/dashboard/failed');
        const data = await res.json();
        const section = document.getElementById('failed-section');
        const tbody = document.getElementById('failed-table');
        
        if (!data.items || data.items.length === 0) {
            section.classList.add('hidden');
            return;
        }
        
        section.classList.remove('hidden');
        tbody.innerHTML = data.items.map(item => `
            <tr style="border-bottom: 1px solid var(--border-color);">
                <td class="px-4 py-2">${escapeHtml(item.movie_title)} (${item.movie_year || 'N/A'})</td>
                <td class="px-4 py-2">${item.score.toFixed(1)}</td>
                <td class="px-4 py-2">${new Date(item.deleted_at).toLocaleDateString()}</td>
                <td class="px-4 py-2" style="color: var(--danger); font-size: 12px;">${escapeHtml(item.error_message || 'Unknown error')}</td>
            </tr>
        `).join('');
    } catch (e) {
        console.error('Failed to load failed deletions:', e);
    }
}

// Next run times
async function loadNextRunTimes() {
    try {
        const res = await fetch('/api/run/next');
        const data = await res.json();
        document.getElementById('next-score-run').textContent = data.next_score_run || 'Not scheduled';
        document.getElementById('next-cull-run').textContent = data.next_cull_run || 'Not scheduled';
    } catch (e) {
        console.error('Failed to load next run times:', e);
    }
}

// Settings summary
async function loadSettingsSummary() {
    try {
        const res = await fetch('/api/dashboard/settings-summary');
        const data = await res.json();
        document.getElementById('delete-after').textContent = `${data.delete_after_days} days`;
        document.getElementById('protection-days').textContent = `${data.protection_days} days`;
        document.getElementById('collection-grouping').textContent = data.collection_grouping ? 'On' : 'Off';
    } catch (e) {
        console.error('Failed to load settings summary:', e);
    }
}

// Trigger score run
async function triggerScoreRun() {
    const dryRun = document.getElementById('dry-run-toggle').checked;
    const btn = document.getElementById('run-score-btn');
    btn.disabled = true;
    btn.textContent = 'Starting...';
    
    try {
        const res = await fetch(`/api/run/score?dry_run=${dryRun}`, { method: 'POST' });
        if (res.ok) {
            showToast(dryRun ? 'Dry score run started' : 'Score run started', 'success');
            setTimeout(() => loadDashboard(), 2000);
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to start run', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '🎯 Run Score';
    }
}

// Score details modal (simplified - shows alert for now, can be enhanced)
function showScoreDetails(title, score, factors) {
    let msg = `Score Details: ${title}\nScore: ${score.toFixed(1)}\n\nBreakdown:\n`;
    if (factors && factors.length) {
        factors.forEach(f => {
            msg += `\n${f.name}: ${(f.contribution * 100).toFixed(1)}% (raw: ${f.raw_score.toFixed(2)}, weight: ${f.weight}%)`;
            if (f.details) msg += ` - ${f.details}`;
            if (f.skipped) msg += ` [SKIPPED: ${f.skip_reason}]`;
        });
    }
    alert(msg);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[m] || m);
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    loadSettingsSummary();
    
    document.getElementById('run-score-btn').addEventListener('click', triggerScoreRun);
    document.getElementById('refresh-btn').addEventListener('click', () => loadDashboard());
    document.getElementById('refresh-queue-btn').addEventListener('click', () => loadScoreQueue());
    document.getElementById('per-page-select').addEventListener('change', (e) => {
        scoreQueuePerPage = parseInt(e.target.value);
        scoreQueuePage = 1;
        loadScoreQueue();
    });
    
    // Auto-refresh every 30 seconds
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadQueueStatus();
        loadScheduledDeletions();
        loadFailedDeletions();
        loadNextRunTimes();
    }, 30000);
});