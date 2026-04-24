// Dashboard JavaScript for Cullarr

let scoreQueuePage = 1;
let scoreQueuePerPage = 20;
let scoreQueueSortBy = 'score';
let scoreQueueSortOrder = 'desc';
let refreshInterval = null;
let runStatusInterval = null;

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
            tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No scheduled deletions</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(item => {
            let scoreClass = 'score-high';
            if (item.score < 60) scoreClass = 'score-medium';
            if (item.score < 30) scoreClass = 'score-low';
            const deleteDate = new Date(item.scheduled_date).toLocaleDateString();
            return `
                <tr style="border-bottom: 1px solid var(--border-color);">
                    <td class="px-4 py-2">${escapeHtml(item.movie_title)} (${item.movie_year || 'N/A'})</td>
                    <td class="px-4 py-2"><span class="badge ${scoreClass}">${item.score.toFixed(1)}</span></td>
                    <td class="px-4 py-2">${deleteDate}</td>
                    <td class="px-4 py-2"><span class="badge" style="background: var(--info-bg); color: var(--info);">scheduled</span></td>
                    <td class="px-4 py-2">
                        <button onclick="removeFromQueue(${item.movie_id}, '${escapeHtml(item.movie_title)}')"
                            class="btn-sm btn-danger">✕ Remove</button>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to load scheduled deletions:', e);
    }
}

// Remove a movie from the scheduled deletions queue
async function removeFromQueue(movieId, title) {
    if (!confirm(`Remove "${title}" from the deletion queue?`)) return;
    try {
        const res = await fetch(`/api/dashboard/scheduled/${movieId}`, { method: 'DELETE' });
        if (res.ok) {
            showToast(`Removed "${title}" from queue`, 'success');
            await loadScheduledDeletions();
            await loadQueueStatus();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to remove from queue', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Score Queue
async function loadScoreQueue(forceRefresh = false) {
    try {
        const url = `/api/dashboard/score-queue?page=${scoreQueuePage}&per_page=${scoreQueuePerPage}&sort_by=${scoreQueueSortBy}&sort_order=${scoreQueueSortOrder}${forceRefresh ? '&refresh=true' : ''}`;
        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('score-queue-table');

        // Show dry run banner if applicable
        const dryRunBanner = document.getElementById('dry-run-banner');
        if (data.dry_run) {
            dryRunBanner?.classList.remove('hidden');
        } else {
            dryRunBanner?.classList.add('hidden');
        }

        if (!data.items || data.items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No movies found. Run a score cycle or configure Radarr first.</td></tr>';
            document.getElementById('score-queue-pagination').innerHTML = '';
            return;
        }

        tbody.innerHTML = data.items.map(movie => {
            // Score badges based on raw score (0-100 scale)
            let scoreClass = 'score-high';
            if (movie.normalized_score < 60) scoreClass = 'score-medium';
            if (movie.normalized_score < 30) scoreClass = 'score-low';
            const tmdbRating = movie.tmdb_rating ? movie.tmdb_rating.toFixed(1) : 'N/A';
            const dryRunStyle = data.dry_run ? 'opacity: 0.75; font-style: italic;' : '';
    
            // Get play count (watched status) - Show plain numbers
            const playCount = movie.plex_play_count || 0;
            let watchedDisplay = '';
            if (movie.plex_play_count === null || movie.plex_play_count === undefined) {
                watchedDisplay = '<span style="color: var(--text-secondary);">N/A</span>';
            } else {
                watchedDisplay = `<span style="color: var(--text-secondary);">${playCount}</span>`;
            }
    
            return `
                <tr style="border-bottom: 1px solid var(--border-color); ${dryRunStyle}">
                    <td class="px-4 py-2"><span class="badge ${scoreClass}">${movie.normalized_score.toFixed(1)}</span></td>
                    <td class="px-4 py-2 font-medium">${escapeHtml(movie.movie_title)}</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.movie_year || 'N/A'}</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.age_days}d</td>
                    <td class="px-4 py-2">${movie.size_gb.toFixed(1)} GB</td>
                    <td class="px-4 py-2"><span class="star">★</span> ${tmdbRating}</td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${escapeHtml(movie.quality) || 'Unknown'}</td>
                    <td class="px-4 py-2">${watchedDisplay}</td>
                    <td class="px-4 py-2">
                        <button onclick="showScoreDetails(${JSON.stringify(escapeHtml(movie.movie_title))}, ${movie.normalized_score}, ${JSON.stringify(movie.factors)})"
                            class="btn-sm btn-outline">🔍 Details</button>
                    </td>
                </tr>
            `;
        }).join('');

        // Pagination
        const totalPages = data.pages || 1;
        document.getElementById('score-queue-pagination').innerHTML = `
            <span style="color: var(--text-secondary)">${data.total} total movies${data.dry_run ? ' <span class="badge" style="background: var(--warning-bg); color: var(--warning);">Dry Run Preview</span>' : ''}</span>
            <div class="flex gap-2">
                <button onclick="changeScoreQueuePage(${scoreQueuePage - 1})" class="btn-sm btn-outline" ${scoreQueuePage <= 1 ? 'disabled' : ''}>← Prev</button>
                <span class="text-sm">Page ${scoreQueuePage} of ${totalPages}</span>
                <button onclick="changeScoreQueuePage(${scoreQueuePage + 1})" class="btn-sm btn-outline" ${scoreQueuePage >= totalPages ? 'disabled' : ''}>Next →</button>
            </div>
        `;

        // Update sort icons
        updateSortIcons();

    } catch (e) {
        console.error('Failed to load score queue:', e);
    }
}

function changeScoreQueuePage(page) {
    scoreQueuePage = page;
    loadScoreQueue();
}

// Add sort function here
function sortScoreQueue(sortBy) {
    if (scoreQueueSortBy === sortBy) {
        // Toggle order if same column
        scoreQueueSortOrder = scoreQueueSortOrder === 'desc' ? 'asc' : 'desc';
    } else {
        // New column, default to desc for score, asc for others
        scoreQueueSortBy = sortBy;
        scoreQueueSortOrder = sortBy === 'score' ? 'desc' : 'asc';
    }
    scoreQueuePage = 1; // Reset to first page
    loadScoreQueue();
}

function updateSortIcons() {
    document.querySelectorAll('.sortable').forEach(header => {
        const sortColumn = header.dataset.sort;
        const icon = header.querySelector('.sort-icon');
        if (sortColumn === scoreQueueSortBy) {
            icon.textContent = scoreQueueSortOrder === 'desc' ? '↓' : '↑';
        } else {
            icon.textContent = '↕';
        }
    });
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
            showToast(dryRun ? 'Dry run started — results will appear in Score Queue' : 'Score run started', 'success');
            startRunStatusPolling();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to start run', 'error');
            btn.disabled = false;
            btn.textContent = '🎯 Run Score';
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = '🎯 Run Score';
    }
}

// Run status polling — updates progress bar and re-loads queue when done
function startRunStatusPolling() {
    const cancelBtn = document.getElementById('cancel-run-btn');
    const runBtn = document.getElementById('run-score-btn');

    cancelBtn.classList.remove('hidden');

    if (runStatusInterval) clearInterval(runStatusInterval);

    runStatusInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/run/status');
            const data = await res.json();

            if (data.is_running) {
                const pct = data.total > 0 ? Math.round((data.current / data.total) * 100) : 0;
    
                if (data.run_type === 'score') {
                    document.getElementById('score-progress-bar').style.width = `${pct}%`;
                    document.getElementById('score-progress-pct').textContent = `${pct}%`;
                    document.getElementById('score-progress-label').textContent = data.current_movie || 'Scoring movies...';
                } else if (data.run_type === 'cull') {
                    document.getElementById('cull-progress-bar').style.width = `${pct}%`;
                    document.getElementById('cull-progress-pct').textContent = `${pct}%`;
                    document.getElementById('cull-progress-label').textContent = data.current_movie || 'Deleting movies...';
                }
    
                cancelBtn.onclick = () => cancelRun(data.run_id);
            } else {
                // Reset both progress bars when idle
                document.getElementById('score-progress-bar').style.width = '0%';
                document.getElementById('score-progress-pct').textContent = '0%';
                document.getElementById('score-progress-label').textContent = 'Idle';
                document.getElementById('cull-progress-bar').style.width = '0%';
                document.getElementById('cull-progress-pct').textContent = '0%';
                document.getElementById('cull-progress-label').textContent = 'Idle';
                cancelBtn.classList.add('hidden');
                runBtn.disabled = false;
                runBtn.textContent = '🎯 Run Score';
                showToast('Run completed', 'success');
                await loadDashboard();
                
                // ✅ STOP POLLING HERE
                clearInterval(runStatusInterval);
                runStatusInterval = null;
            }
        } catch (e) {
            console.error('Failed to poll run status:', e);
        }
    }, 2000);
}

async function cancelRun(runId) {
    try {
        const res = await fetch(`/api/run/${runId}/cancel`, { method: 'POST' });
        if (res.ok) {
            showToast('Cancellation requested', 'info');
        }
    } catch (e) {
        console.error('Failed to cancel run:', e);
    }
}

// Score details modal
function showScoreDetails(title, score, factors) {
    // Remove any existing modal
    document.getElementById('score-modal')?.remove();

    const factorRows = (factors && factors.length)
        ? factors.map(f => {
            const pct = (f.contribution * 100).toFixed(1);
            const barWidth = Math.min(Math.round(f.raw_score * 100), 100);
            const skippedNote = f.skipped ? `<span class="text-xs ml-2" style="color: var(--text-secondary)">(${f.skip_reason})</span>` : '';
            return `
                <div class="mb-3">
                    <div class="flex justify-between text-sm mb-1">
                        <span class="font-medium">${f.name}${skippedNote}</span>
                        <span style="color: var(--text-secondary)">${f.details || ''}</span>
                    </div>
                    <div class="flex items-center gap-3">
                        <div class="flex-1 rounded-full h-2" style="background: var(--border-color)">
                            <div class="h-2 rounded-full" style="width: ${barWidth}%; background: var(--accent);"></div>
                        </div>
                        <span class="text-xs font-mono w-12 text-right" style="color: var(--text-secondary)">
                            ${pct}%
                        </span>
                    </div>
                </div>
            `;
        }).join('')
        : '<p style="color: var(--text-secondary)">No factor data available.</p>';

    const modal = document.createElement('div');
    modal.id = 'score-modal';
    modal.className = 'fixed inset-0 flex items-center justify-center z-50';
    modal.style.background = 'rgba(0,0,0,0.6)';
    modal.innerHTML = `
        <div class="card rounded-xl p-6 w-full max-w-md mx-4" style="max-height: 90vh; overflow-y: auto;">
            <div class="flex justify-between items-start mb-4">
                <div>
                    <h3 class="font-semibold text-lg">${escapeHtml(title)}</h3>
                    <p class="text-sm mt-1" style="color: var(--text-secondary)">
                        Total score: <span class="font-mono font-bold" style="color: var(--accent)">${score.toFixed(1)}</span>
                    </p>
                </div>
                <button onclick="document.getElementById('score-modal').remove()"
                    class="text-lg leading-none" style="color: var(--text-secondary)">✕</button>
            </div>
            <div class="border-t pt-4" style="border-color: var(--border-color)">
                ${factorRows}
            </div>
        </div>
    `;

    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });

    document.body.appendChild(modal);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[m] || m);
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    loadSettingsSummary();

    document.getElementById('run-score-btn').addEventListener('click', triggerScoreRun);
    document.getElementById('refresh-queue-btn').addEventListener('click', () => loadScoreQueue(true));
    document.getElementById('per-page-select').addEventListener('change', (e) => {
        scoreQueuePerPage = parseInt(e.target.value);
        scoreQueuePage = 1;
        loadScoreQueue();
    });

    // Add sort event listeners
    document.querySelectorAll('.sortable').forEach(header => {
        header.addEventListener('click', () => {
            const sortBy = header.dataset.sort;
            sortScoreQueue(sortBy);
        });
    });

    // Auto-refresh every 30 seconds — queue status and deletions only, not score queue
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadQueueStatus();
        loadScheduledDeletions();
        loadFailedDeletions();
        loadNextRunTimes();
    }, 30000);
});