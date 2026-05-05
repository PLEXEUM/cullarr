// Dashboard JavaScript for Cullarr

let scoreQueuePage = 1;
let scoreQueuePerPage = 10;
let scoreQueueSortBy = 'score';
let scoreQueueSortOrder = 'desc';
let scoreQueueSearch = '';
let scoreQueueSearchActive = false;
let refreshInterval = null;
let runStatusInterval = null;
let scheduledDeletionsCollapsed = false;

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
            tbody.innerHTML = '<tr><td colspan="6" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No scheduled deletions</td></tr>';
            return;
        }

        tbody.innerHTML = data.items.map(item => {
            let scoreClass = 'score-high';
            if (item.score < 60) scoreClass = 'score-medium';
            if (item.score < 30) scoreClass = 'score-low';
            const deleteDate = new Date(item.scheduled_date).toLocaleDateString();
            
            // Calculate countdown
            const now = new Date();
            const deleteDateTime = new Date(item.scheduled_date);
            const daysRemaining = Math.ceil((deleteDateTime - now) / (1000 * 60 * 60 * 24));
            
            let countdownText = '';
            let countdownClass = '';
            if (daysRemaining < 0) {
                countdownText = 'Overdue';
                countdownClass = 'style="color: var(--danger); font-weight: bold;"';
            } else if (daysRemaining === 0) {
                countdownText = 'Today';
                countdownClass = 'style="color: var(--warning); font-weight: bold;"';
            } else if (daysRemaining === 1) {
                countdownText = 'Tomorrow';
                countdownClass = 'style="color: var(--warning);"';
            } else if (daysRemaining <= 3) {
                countdownText = `${daysRemaining} days`;
                countdownClass = 'style="color: var(--warning);"';
            } else {
                countdownText = `${daysRemaining} days`;
                countdownClass = '';
            }
            
            // Get movie year (handle collections)
            let yearDisplay = 'N/A';
            if (item.movie_year !== null && item.movie_year !== undefined) {
                yearDisplay = String(item.movie_year);
            } else if (item.is_collection) {
                yearDisplay = 'Various';
            }
            
            // Determine if this is a collection (for year display)
            const isCollection = item.collection_name ? true : false;
            
            return `
                <tr style="border-bottom: 1px solid var(--border-color);">
                    <td class="px-4 py-2"><span class="badge ${scoreClass}">${item.score.toFixed(1)}</span></td>
                    <td class="px-4 py-2 font-medium">${escapeHtml(item.movie_title)}</span></td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${escapeHtml(yearDisplay)}</span></td>
                    <td class="px-4 py-2" style="color: var(--text-secondary)">${deleteDate}</span></td>
                    <td class="px-4 py-2" ${countdownClass}>${countdownText}</span></td>
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
async function loadScoreQueue() {
    try {
        let url;
        
        // If search is active, use search endpoint
        if (scoreQueueSearchActive && scoreQueueSearch.trim() !== '') {
            url = `/api/dashboard/score-queue/search?q=${encodeURIComponent(scoreQueueSearch)}&page=${scoreQueuePage}&per_page=${scoreQueuePerPage}&sort_by=${scoreQueueSortBy}&sort_order=${scoreQueueSortOrder}`;
        } else {
            // Normal pagination (only load current page)
            url = `/api/dashboard/score-queue?page=${scoreQueuePage}&per_page=${scoreQueuePerPage}&sort_by=${scoreQueueSortBy}&sort_order=${scoreQueueSortOrder}`;
        }
        
        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('score-queue-table');

        if (!data.items || data.items.length === 0) {
            if (scoreQueueSearchActive) {
                tbody.innerHTML = `<tr><td colspan="9" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No movies match "${escapeHtml(scoreQueueSearch)}".</td></tr>`;
            } else {
                tbody.innerHTML = '<tr><td colspan="9" class="px-4 py-8 text-center" style="color: var(--text-secondary)">No movies found. Run a score cycle or configure Radarr first.</td></tr>';
            }
            document.getElementById('score-queue-pagination').innerHTML = '';
            return;
        }

        tbody.innerHTML = data.items.map(movie => {
            let scoreClass = 'score-high';
            if (movie.normalized_score < 60) scoreClass = 'score-medium';
            if (movie.normalized_score < 30) scoreClass = 'score-low';
            const tmdbRating = movie.tmdb_rating ? movie.tmdb_rating.toFixed(1) : 'N/A';

            const playCount = movie.plex_play_count || 0;
            let watchedDisplay = '';
            if (movie.plex_play_count === null || movie.plex_play_count === undefined) {
                watchedDisplay = '<span style="color: var(--text-secondary);">N/A</span>';
            } else {
                watchedDisplay = `<span style="color: var(--text-secondary);">${playCount}</span>`;
            }

        const safeTitle = escapeHtml(movie.movie_title || 'Unknown');
        const factorsJson = JSON.stringify(movie.factors || []).replace(/'/g, "&#39;");
        const isCollection = movie.is_collection || false;
        const moviesData = isCollection ? JSON.stringify(movie.movies || []).replace(/'/g, "&#39;") : '[]';

        return `
            <tr style="border-bottom: 1px solid var(--border-color);">
                <td class="px-4 py-2"><span class="badge ${scoreClass}">${movie.normalized_score.toFixed(1)}</span></td>
                <td class="px-4 py-2 font-medium">${safeTitle}</td>
                <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.movie_year || 'N/A'}</td>
                <td class="px-4 py-2" style="color: var(--text-secondary)">${movie.age_days}d</span></td>
                <td class="px-4 py-2">${movie.size_gb.toFixed(1)} GB</span></td>
                <td class="px-4 py-2"><span class="star">★</span> ${tmdbRating}</span></td>
                <td class="px-4 py-2" style="color: var(--text-secondary)">${escapeHtml(movie.quality) || 'Unknown'}</span></td>
                <td class="px-4 py-2">${watchedDisplay}</span></td>
                <td class="px-4 py-2">
                    <button data-title="${safeTitle}" 
                            data-score="${movie.normalized_score}" 
                            data-factors='${factorsJson}'
                            data-is-collection="${isCollection}"
                            data-movies='${moviesData}'
                            data-movie-count="${movie.movie_count || 0}"
                            class="btn-sm btn-outline details-btn">🔍 Details</button>
                </span>
            </tr>
        `;
        }).join('');

        // Pagination with page number input
        const totalPages = data.pages || 1;
        const searchInfo = scoreQueueSearchActive ? ` (matching "${escapeHtml(scoreQueueSearch)}")` : '';
        document.getElementById('score-queue-pagination').innerHTML = `
            <div class="flex justify-between items-center w-full">
                <span style="color: var(--text-secondary)">${data.total} total movies${searchInfo}</span>
                <div class="flex items-center gap-3">
                    <button onclick="changeScoreQueuePage(${scoreQueuePage - 1})" class="btn-sm btn-outline" ${scoreQueuePage <= 1 ? 'disabled' : ''}>← Prev</button>
                    <div class="flex items-center gap-1">
                        <span class="text-sm">Page</span>
                        <input type="number" id="page-number-input" value="${scoreQueuePage}" min="1" max="${totalPages}" 
                            style="width: 60px; text-align: center; background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 0.375rem; padding: 0.25rem 0.5rem; font-size: 0.875rem; color: var(--text-primary);">
                        <span class="text-sm">of ${totalPages}</span>
                    </div>
                    <button onclick="changeScoreQueuePage(${scoreQueuePage + 1})" class="btn-sm btn-outline" ${scoreQueuePage >= totalPages ? 'disabled' : ''}>Next →</button>
                </div>
            </div>
        `;
        
        // Add event listener for page number input
        const pageInput = document.getElementById('page-number-input');
        if (pageInput) {
            pageInput.addEventListener('change', (e) => {
                let newPage = parseInt(e.target.value);
                if (isNaN(newPage)) newPage = 1;
                if (newPage < 1) newPage = 1;
                if (newPage > totalPages) newPage = totalPages;
                if (newPage !== scoreQueuePage) {
                    scoreQueuePage = newPage;
                    loadScoreQueue();
                }
            });
        }

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
        scoreQueueSortOrder = scoreQueueSortOrder === 'desc' ? 'asc' : 'desc';
    } else {
        scoreQueueSortBy = sortBy;
        scoreQueueSortOrder = sortBy === 'score' ? 'desc' : 'asc';
    }
    scoreQueuePage = 1;
    loadScoreQueue();  // This will re-sort the full data client-side
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

// Deletion History
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
        tbody.innerHTML = data.items.map(item => {
            let statusHtml = '';
            if (item.status === 'deleted') {
                statusHtml = '<span style="color: var(--success);">✅ deleted</span>';
            } else {
                statusHtml = '<span style="color: var(--danger);">❌ failed</span>';
            }
            return `
                <tr style="border-bottom: 1px solid var(--border-color);">
                    <td class="px-4 py-2">${escapeHtml(item.movie_title)} (${item.movie_year || 'N/A'})</td>
                    <td class="px-4 py-2">${item.score.toFixed(1)}</td>
                    <td class="px-4 py-2">${new Date(item.deleted_at).toLocaleDateString()}</td>
                    <td class="px-4 py-2">${statusHtml}</td>
                    <td class="px-4 py-2" style="color: var(--danger); font-size: 12px;">${escapeHtml(item.error_message || '—')}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to load deletion history:', e);
    }
}

// Clear Failed Deletions
async function clearFailedDeletions() {
    if (!confirm('Clear all deletion history records? This cannot be undone.')) return;
    try {
        const res = await fetch('/api/dashboard/failed', { method: 'DELETE' });
        if (res.ok) {
            showToast('Deletion history cleared', 'success');
            await loadFailedDeletions();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to clear', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
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
        
        // Display deletion rate indicator
        const rateElement = document.getElementById('deletion-rate');
        if (rateElement && data.deletions_per_day !== undefined) {
            const rate = data.deletions_per_day;
            if (rate === 0) {
                rateElement.textContent = '↻ 0/unlimited';
            } else {
                rateElement.textContent = `↻ ${rate}/day`;
            }
        }
    } catch (e) {
        console.error('Failed to load settings summary:', e);
    }
}

// Toggle Scheduled Deletions card collapse/expand
function toggleScheduledDeletions() {
    const section = document.getElementById('scheduled-deletions-section');
    const icon = document.getElementById('scheduled-deletions-icon');
    
    if (!section || !icon) return;
    
    if (section.style.display === 'none') {
        section.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';  // Points UP when expanded
        scheduledDeletionsCollapsed = false;
        localStorage.setItem('scheduledDeletionsCollapsed', 'false');
    } else {
        section.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';    // Points DOWN when collapsed
        scheduledDeletionsCollapsed = true;
        localStorage.setItem('scheduledDeletionsCollapsed', 'true');
    }
}

// Load saved collapse state on page load
function loadScheduledDeletionsState() {
    const savedState = localStorage.getItem('scheduledDeletionsCollapsed');
    if (savedState === 'true') {
        const section = document.getElementById('scheduled-deletions-section');
        const icon = document.getElementById('scheduled-deletions-icon');
        if (section && icon) {
            section.style.display = 'none';
            icon.style.transform = 'rotate(0deg)';
            scheduledDeletionsCollapsed = true;
        }
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
            showToast(dryRun ? 'Dry run started — results will appear when complete' : 'Score run started', 'success');
            // Store that this is a dry run so polling knows to show modal
            window.lastRunType = 'Score';
            window.pendingDryRun = dryRun ? 'score' : null;
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

// Trigger cull run
async function triggerCullRun() {
    const dryRun = document.getElementById('dry-run-toggle').checked;
    const btn = document.getElementById('run-cull-btn');
    btn.disabled = true;
    btn.textContent = 'Starting...';

    try {
        const url = dryRun ? '/api/run/cull?dry_run=true' : '/api/run/cull';
        const res = await fetch(url, { method: 'POST' });
        if (res.ok) {
            showToast(dryRun ? 'Cull dry run started — results will appear when complete' : 'Cull run started', 'success');
            window.lastRunType = 'Cull';
            window.pendingDryRun = dryRun ? 'cull' : null;
            startRunStatusPolling();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to start cull run', 'error');
            btn.disabled = false;
            btn.textContent = '🗑️ Run Cull';
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        btn.disabled = false;
        btn.textContent = '🗑️ Run Cull';
    }
}

// Run status polling — updates progress bar and re-loads queue when done
function startRunStatusPolling() {
    const cancelBtn = document.getElementById('cancel-run-btn');
    const scoreProgressBar = document.getElementById('score-progress-bar');
    const cullProgressBar = document.getElementById('cull-progress-bar');
    const scoreProgressPct = document.getElementById('score-progress-pct');
    const cullProgressPct = document.getElementById('cull-progress-pct');
    const scoreProgressLabel = document.getElementById('score-progress-label');
    const cullProgressLabel = document.getElementById('cull-progress-label');

    cancelBtn.classList.remove('hidden');

    if (runStatusInterval) clearInterval(runStatusInterval);

    runStatusInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/run/status');
            const data = await res.json();

            if (data.is_running) {
                // Show indeterminate (pulsing) animation for the active run type
                if (data.run_type === 'score') {
                    // Score run active
                    scoreProgressBar.classList.add('progress-indeterminate');
                    scoreProgressBar.classList.remove('bg-indigo-500');
                    scoreProgressBar.style.width = '';
                    scoreProgressPct.textContent = '⟳';
                    scoreProgressLabel.textContent = data.current_movie || 'Running score cycle...';
                    
                    // Reset cull run display
                    cullProgressBar.classList.remove('progress-indeterminate');
                    cullProgressBar.style.width = '0%';
                    cullProgressPct.textContent = '0%';
                    cullProgressLabel.textContent = 'Idle';
                } else if (data.run_type === 'cull') {
                    // Cull run active
                    cullProgressBar.classList.add('progress-indeterminate');
                    cullProgressBar.style.width = '';
                    cullProgressPct.textContent = '⟳';
                    cullProgressLabel.textContent = data.current_movie || 'Running cull cycle...';
                    
                    // Reset score run display
                    scoreProgressBar.classList.remove('progress-indeterminate');
                    scoreProgressBar.style.width = '0%';
                    scoreProgressPct.textContent = '0%';
                    scoreProgressLabel.textContent = 'Idle';
                }
    
                cancelBtn.onclick = () => cancelRun(data.run_id);
            } else {
                // Remove indeterminate animation and reset both progress bars
                scoreProgressBar.classList.remove('progress-indeterminate');
                cullProgressBar.classList.remove('progress-indeterminate');
                
                scoreProgressBar.classList.add('bg-indigo-500');
                cullProgressBar.classList.add('bg-indigo-500');

                scoreProgressBar.style.width = '0%';
                scoreProgressPct.textContent = '0%';
                scoreProgressLabel.textContent = 'Idle';
                
                cullProgressBar.style.width = '0%';
                cullProgressPct.textContent = '0%';
                cullProgressLabel.textContent = 'Idle';
                
                cancelBtn.classList.add('hidden');
    
                // Re-enable both buttons
                const scoreBtn = document.getElementById('run-score-btn');
                const cullBtn = document.getElementById('run-cull-btn');
                if (scoreBtn) {
                    scoreBtn.disabled = false;
                    scoreBtn.textContent = '🎯 Run Score';
                }
                if (cullBtn) {
                    cullBtn.disabled = false;
                    cullBtn.textContent = '🗑️ Run Cull';
                }
    
                // Check if this was a dry run with results
                if (data.dry_run && data.dry_run_results && data.dry_run_results.length > 0) {
                    // STOP POLLING FIRST to prevent modal from reopening
                    clearInterval(runStatusInterval);
                    runStatusInterval = null;
                    
                    const title = window.pendingDryRun === 'score' ? 'Score Dry Run Preview' : 'Cull Dry Run Preview';
                    showDryRunModal(title, data.dry_run_results, window.pendingDryRun || 'score');
                    
                    await loadDashboard();
                    window.pendingDryRun = null;
                } else if (data.dry_run) {
                    // STOP POLLING FIRST
                    clearInterval(runStatusInterval);
                    runStatusInterval = null;
                    
                    showToast('Dry run completed - no items would be affected', 'info');
                    
                    await loadDashboard();
                    window.pendingDryRun = null;
                } else if (!data.dry_run && data.is_running === false) {
                    // STOP POLLING FIRST to prevent multiple toasts
                    clearInterval(runStatusInterval);
                    runStatusInterval = null;
                    
                    // THEN show completion toast
                    showToast(`${window.lastRunType} run completed`, 'success');
                    
                    await loadDashboard();
                    window.pendingDryRun = null;
                } else {
                    // If we get here, the run is still in progress or not completed
                    // Keep polling
                }
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

// Score details modal - handles both individual movies and collections
function showScoreDetails(title, score, factors, isCollection = false, movies = [], movieCount = 0) {
    // Remove any existing modal
    const existingModal = document.getElementById('score-modal');
    if (existingModal) existingModal.remove();

    let contentHtml = '';
    
    if (isCollection && movies && movies.length > 0) {
        // Display collection members
        contentHtml = `
            <div class="border-t pt-4" style="border-color: var(--border-color);">
                <div class="text-sm font-semibold mb-3" style="color: var(--accent);">
                    📁 Collection contains ${movieCount} movie${movieCount !== 1 ? 's' : ''}:
                </div>
                <div class="space-y-2 max-h-96 overflow-y-auto">
                    ${movies.map(movie => {
                        const movieTitle = escapeHtml(movie.movie_title || movie.title || 'Unknown');
                        const movieYear = movie.movie_year || movie.year || 'N/A';
                        const movieScore = movie.score || movie.normalized_score || 'N/A';
                        const movieSize = movie.size_gb ? `${movie.size_gb.toFixed(1)} GB` : '';
                        return `
                            <div class="border rounded-lg p-2" style="border-color: var(--border-color); background: var(--bg-primary);">
                                <div class="flex justify-between items-start">
                                    <div>
                                        <span class="font-medium">${movieTitle}</span>
                                        <span class="text-xs ml-1" style="color: var(--text-secondary);">(${movieYear})</span>
                                    </div>
                                    <span class="badge" style="background: var(--info-bg); color: var(--info); font-size: 10px;">Score: ${typeof movieScore === 'number' ? movieScore.toFixed(1) : movieScore}</span>
                                </div>
                                ${movieSize ? `<div class="text-xs mt-1" style="color: var(--text-secondary);">${movieSize}</div>` : ''}
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;
    } else if (factors && factors.length > 0) {
        // Display individual movie factor breakdown
        let factorRows = '';
        for (let i = 0; i < factors.length; i++) {
            const f = factors[i];
            const pct = (f.contribution * 100).toFixed(1);
            const barWidth = Math.min(Math.round(f.raw_score * 100), 100);
            let skippedNote = '';
            if (f.skipped) {
                skippedNote = '<span class="text-xs ml-2" style="color: var(--text-secondary)">(' + (f.skip_reason || 'skipped') + ')</span>';
            }
            factorRows += `
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
        }
        contentHtml = `
            <div class="border-t pt-4" style="border-color: var(--border-color)">
                ${factorRows}
            </div>
        `;
    } else {
        contentHtml = '<div class="border-t pt-4" style="border-color: var(--border-color)"><p style="color: var(--text-secondary)">No details available.</p></div>';
    }

    const modal = document.createElement('div');
    modal.id = 'score-modal';
    modal.className = 'fixed inset-0 flex items-center justify-center z-50';
    modal.style.background = 'rgba(0,0,0,0.6)';
    modal.innerHTML = `
        <div class="card rounded-xl p-6 w-full max-w-2xl mx-4" style="max-height: 85vh; overflow-y: auto;">
            <div class="flex justify-between items-start mb-4">
                <div>
                    <h3 class="font-semibold text-lg">${escapeHtml(title)}</h3>
                    <p class="text-sm mt-1" style="color: var(--text-secondary)">
                        ${isCollection ? `Collection score: ` : `Total score: `}
                        <span class="font-mono font-bold" style="color: var(--accent)">${Number(score).toFixed(1)}</span>
                    </p>
                </div>
                <button onclick="document.getElementById('score-modal').remove()"
                    class="text-lg leading-none" style="color: var(--text-secondary)">✕</button>
            </div>
            ${contentHtml}
            <div class="border-t pt-4 mt-4 flex justify-end" style="border-color: var(--border-color)">
                <button onclick="document.getElementById('score-modal').remove()"
                    class="px-4 py-2 rounded-lg text-sm" style="background: var(--accent); color: white;">
                    Close
                </button>
            </div>
        </div>
    `;

    // Close on backdrop click
    modal.addEventListener('click', function(e) {
        if (e.target === modal) modal.remove();
    });

    document.body.appendChild(modal);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[m] || m);
}

// Show dry run results in a modal popup
function showDryRunModal(title, items, type = 'score') {
    // Remove any existing modal
    const existingModal = document.getElementById('dry-run-modal');
    if (existingModal) existingModal.remove();
    
    if (!items || items.length === 0) {
        showToast('No items would be affected', 'info');
        return;
    }
    
    const accentColor = type === 'score' ? 'var(--accent)' : 'var(--danger)';
    const icon = type === 'score' ? '🎯' : '🗑️';
    
    // Build items list HTML
    const itemsHtml = items.map(item => {
        if (type === 'score') {
            return `
                <div class="border-b pb-2 mb-2" style="border-color: var(--border-color);">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="font-medium">${escapeHtml(item.movie_title)}</span>
                            ${item.movie_year ? `<span class="text-xs" style="color: var(--text-secondary)"> (${item.movie_year})</span>` : ''}
                            ${item.is_collection ? '<span class="badge ml-2" style="background: var(--purple-bg); color: var(--purple);">Collection</span>' : ''}
                        </div>
                        <span class="badge score-high">${item.normalized_score?.toFixed(1) || item.score?.toFixed(1) || '0'}</span>
                    </div>
                    <div class="text-xs mt-1" style="color: var(--text-secondary);">
                        ${item.size_gb ? `${item.size_gb.toFixed(1)} GB` : ''}
                        ${item.age_days ? ` • ${item.age_days} days old` : ''}
                        ${item.is_collection && item.movie_count ? ` • ${item.movie_count} movies` : ''}
                    </div>
                </div>
            `;
        } else {
            // Cull run results
            return `
                <div class="border-b pb-2 mb-2" style="border-color: var(--border-color);">
                    <div class="flex justify-between items-start">
                        <div>
                            <span class="font-medium">${escapeHtml(item.movie_title)}</span>
                            ${item.movie_year ? `<span class="text-xs" style="color: var(--text-secondary)"> (${item.movie_year})</span>` : ''}
                        </div>
                        <span class="badge" style="background: var(--info-bg); color: var(--info);">${new Date(item.scheduled_date).toLocaleDateString()}</span>
                    </div>
                    <div class="text-xs mt-1" style="color: var(--text-secondary);">
                        Score: ${item.score?.toFixed(1) || 'N/A'} • Size: ${item.size_gb?.toFixed(1) || 0} GB
                    </div>
                </div>
            `;
        }
    }).join('');
    
    const modal = document.createElement('div');
    modal.id = 'dry-run-modal';
    modal.className = 'fixed inset-0 flex items-center justify-center z-50';
    modal.style.background = 'rgba(0,0,0,0.6)';
    modal.innerHTML = `
        <div class="card rounded-xl p-6 w-full max-w-2xl mx-4" style="max-height: 80vh; overflow-y: auto;">
            <div class="flex justify-between items-start mb-4">
                <div>
                    <h3 class="font-semibold text-lg">${icon} ${title}</h3>
                    <p class="text-sm mt-1" style="color: var(--text-secondary)">
                        These ${items.length} item${items.length !== 1 ? 's' : ''} would be affected. No actual changes were made.
                    </p>
                </div>
                <button onclick="document.getElementById('dry-run-modal').remove()"
                    class="text-lg leading-none" style="color: var(--text-secondary);">✕</button>
            </div>
            <div class="border-t pt-4" style="border-color: var(--border-color);">
                ${itemsHtml}
            </div>
            <div class="border-t pt-4 mt-2 flex justify-end" style="border-color: var(--border-color);">
                <button onclick="document.getElementById('dry-run-modal').remove()"
                    class="px-4 py-2 rounded-lg text-sm" style="background: var(--accent); color: white;">
                    Close
                </button>
            </div>
        </div>
    `;
    
    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
    
    document.body.appendChild(modal);
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    loadSettingsSummary();

    document.getElementById('run-score-btn').addEventListener('click', triggerScoreRun);
    document.getElementById('run-cull-btn').addEventListener('click', triggerCullRun);
    document.getElementById('clear-failed-btn').addEventListener('click', clearFailedDeletions);
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

        const searchInput = document.getElementById('score-queue-search');
    const clearSearchBtn = document.getElementById('clear-search-btn');
    
    if (searchInput) {
        let debounceTimer;
        
        // Function to show/hide clear button
        const toggleClearButton = () => {
            if (clearSearchBtn) {
                if (searchInput.value.length > 0) {
                    clearSearchBtn.style.display = 'block';
                } else {
                    clearSearchBtn.style.display = 'none';
                }
            }
        };
        
        // Initial check
        toggleClearButton();
        
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            toggleClearButton();
            debounceTimer = setTimeout(() => {
                scoreQueueSearch = e.target.value;
                scoreQueuePage = 1;
                scoreQueueSearchActive = scoreQueueSearch.trim() !== '';
                loadScoreQueue();
            }, 300);  // Debounce to avoid too many API calls
        });
        
        // Clear button click handler
        if (clearSearchBtn) {
            clearSearchBtn.addEventListener('click', () => {
                searchInput.value = '';
                scoreQueueSearch = '';
                scoreQueuePage = 1;
                scoreQueueSearchActive = false;
                toggleClearButton();
                loadScoreQueue();
                searchInput.focus();
            });
        }
    }

    // Event delegation for details buttons (handles dynamically added rows)
    const scoreQueueTable = document.getElementById('score-queue-table');
    if (scoreQueueTable) {
        scoreQueueTable.addEventListener('click', (e) => {
            const btn = e.target.closest('.details-btn');
            if (btn) {
                e.preventDefault();
                const title = btn.getAttribute('data-title');
                const score = parseFloat(btn.getAttribute('data-score'));
                const isCollection = btn.getAttribute('data-is-collection') === 'true';
                const movieCount = parseInt(btn.getAttribute('data-movie-count') || '0');
                
                let factors = [];
                let movies = [];
                
                try {
                    const factorsAttr = btn.getAttribute('data-factors');
                    if (factorsAttr && factorsAttr !== 'undefined') {
                        factors = JSON.parse(factorsAttr);
                    }
                } catch (err) {
                    console.error('Failed to parse factors:', err);
                    factors = [];
                }
                
                if (isCollection) {
                    try {
                        const moviesAttr = btn.getAttribute('data-movies');
                        if (moviesAttr && moviesAttr !== 'undefined' && moviesAttr !== '[]') {
                            movies = JSON.parse(moviesAttr);
                        }
                    } catch (err) {
                        console.error('Failed to parse movies:', err);
                        movies = [];
                    }
                }
                
                showScoreDetails(title, score, factors, isCollection, movies, movieCount);
            }
        });
    }

    // Load saved collapse state for Scheduled Deletions
    loadScheduledDeletionsState();
    
    // Auto-refresh every 30 seconds — queue status and deletions only, not score queue
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadQueueStatus();
        loadScheduledDeletions();
        loadFailedDeletions();
        loadNextRunTimes();
    }, 30000);
});