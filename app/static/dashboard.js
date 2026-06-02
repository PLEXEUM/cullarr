// Dashboard JavaScript for Cullarr

let scoreQueuePage = 1;
let scoreQueuePerPage = 12;
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
}

// Queue Status
async function loadQueueStatus() {
    try {
        const res = await fetch('/api/dashboard/queue-status');
        const data = await res.json();

        // Radarr status - update sidebar
        updateSidebarRadarrStatus(data.radarr.configured, data.radarr.status);
        
        // Plex status - update sidebar
        const watchedCount = data.plex.details ? data.plex.details.match(/\d+/)?.[0] || '' : '';
        updateSidebarPlexStatus(data.plex.configured, data.plex.status, watchedCount);
        
        // Update Scheduled Deletions header metrics
        const metricsElement = document.getElementById('scheduled-queue-metrics');
        if (metricsElement) {
            const scheduledCount = data.scheduled_count || 0;
            const percentUsed = data.percent_used || 0;
            const maxQueued = data.max_queued || 20;
            metricsElement.textContent = `${scheduledCount}/${maxQueued} (${percentUsed}%)`;
        }
        
    } catch (e) {
        console.error('Failed to load queue status:', e);
    }
}

// Scheduled Deletions - Load and render as poster grid
async function loadScheduledDeletions() {
    try {
        const res = await fetch('/api/dashboard/score-queue?page=1&per_page=100&scheduled=1&sort_by=score&sort_order=desc');
        
        if (!res.ok) {
            throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        }
        
        const data = await res.json();
        renderScheduledGrid(data);
    } catch (e) {
        console.error('Failed to load scheduled deletions:', e);
        const gridContainer = document.getElementById('scheduled-grid-inner');
        const badge = document.getElementById('scheduled-badge');
        
        if (badge) badge.textContent = '0 items';
        if (gridContainer) {
            gridContainer.innerHTML = `<div class="col-span-full text-center py-8" style="color: var(--danger);">Failed to load scheduled deletions. Please check your connection.</div>`;
        }
    }
}

// Remove a movie from the scheduled deletions queue
async function removeFromQueue(movieId, title, isCollection = false, event = null) {
    let confirmMessage = isCollection ? `Remove entire collection "${title}" from the deletion queue?` : `Remove "${title}" from the deletion queue?`;
    if (!confirm(confirmMessage)) return;
    
    // Get and disable the button if event provided
    let btn = null;
    let originalText = '';
    if (event && event.currentTarget) {
        btn = event.currentTarget;
        originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⟳ Removing...';
    }
    
    try {
        // For collections, use collection_id; for individuals, use movie_id
        let url;
        if (isCollection) {
            url = `/api/dashboard/scheduled/collection/${movieId}`;
        } else {
            url = `/api/dashboard/scheduled/${movieId}`;
        }
                
        const res = await fetch(url, { method: 'DELETE' });
        if (res.ok) {
            showToast(`Removed "${title}" from queue`, 'success');
            await loadScheduledDeletions();
            await loadQueueStatus();
            await loadScoreQueue();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to remove from queue', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        // Button will be replaced on re-render, no need to restore
        if (btn) {
            btn.disabled = false;
        }
    }
}

// Score Queue - Load and render as poster grid
async function loadScoreQueue() {
    try {
        let url;
        
        if (scoreQueueSearchActive && scoreQueueSearch.trim() !== '') {
            url = `/api/dashboard/score-queue/search?q=${encodeURIComponent(scoreQueueSearch)}&page=${scoreQueuePage}&per_page=${scoreQueuePerPage}&sort_by=${scoreQueueSortBy}&sort_order=${scoreQueueSortOrder}`;
        } else {
            url = `/api/dashboard/score-queue?page=${scoreQueuePage}&per_page=${scoreQueuePerPage}&sort_by=${scoreQueueSortBy}&sort_order=${scoreQueueSortOrder}&scheduled=0`;
        }
        
        const res = await fetch(url);
        const data = await res.json();
        const gridContainer = document.getElementById('score-queue-grid-inner');

        if (!data.items || data.items.length === 0) {
            if (scoreQueueSearchActive) {
                gridContainer.innerHTML = `<div class="col-span-full text-center py-8" style="color: var(--text-secondary)">No movies match "${escapeHtml(scoreQueueSearch)}".</div>`;
            } else {
                gridContainer.innerHTML = `<div class="col-span-full text-center py-8" style="color: var(--text-secondary)">No movies found. Run a score cycle or configure Radarr first.</div>`;
            }
            document.getElementById('score-queue-pagination').innerHTML = '';
            return;
        }

        // Generate poster cards
        const cardsHtml = data.items.map(movie => {
            let scoreClass = 'score-high';
            if (movie.normalized_score < 60) scoreClass = 'score-medium';
            if (movie.normalized_score < 30) scoreClass = 'score-low';
            
            let posterUrl = '/static/no-poster.png';
            const title = escapeHtml(movie.movie_title || 'Unknown');
            const movieId = movie.collection_id || movie.movie_id;
            const isCollection = movie.is_collection || false;

            // For collections, find the oldest movie's poster
            if (isCollection && movie.movies && movie.movies.length > 0) {
                // Find the movie with the smallest year (oldest)
                const oldestMovie = [...movie.movies].sort((a, b) => {
                    const yearA = a.movie_year || a.year || 9999;
                    const yearB = b.movie_year || b.year || 9999;
                    return yearA - yearB;
                })[0];
                posterUrl = oldestMovie.poster_url || '/static/no-poster.png';
            } else if (!isCollection && movie.poster_url) {
                posterUrl = movie.poster_url;
            }
            
            // Store data for modal
            const factorsJson = JSON.stringify(movie.factors || []).replace(/'/g, "&#39;");
            const moviesData = isCollection ? JSON.stringify(movie.movies || []).replace(/'/g, "&#39;") : '[]';

            // Add collection badge if this is a collection  ← THIS LINE
            const collectionBadge = isCollection ? `<span class="collection-badge">📁 Collection</span>` : ''; 
            
            return `
                <div class="poster-card" data-movie-id="${movieId}" data-title="${title}" data-score="${movie.normalized_score}" data-factors='${factorsJson}' data-is-collection="${isCollection}" data-movies='${moviesData}' data-movie-count="${movie.movie_count || 0}">
                    <div class="poster-image-container">
                        <img src="${posterUrl}" alt="${title}" class="poster-image" loading="lazy" onerror="this.src='/static/no-poster.png'">
                        <div class="poster-overlay">
                            <button class="details-overlay-btn">🔍 Details</button>
                        </div>
                        ${collectionBadge}
                    </div>
                    <div class="poster-meta-bar">
                        <span class="score-badge ${scoreClass}">${movie.normalized_score.toFixed(1)}</span>
                        ${movie.scheduled_for_deletion ? 
                            `<button class="remove-btn" data-movie-id="${movieId}" data-title="${title}" data-is-collection="${isCollection}">✕ Remove</button>` : 
                            `<button class="queue-btn" data-movie-id="${movieId}" data-title="${title}" data-is-collection="${isCollection}" data-movie-count="${movie.movie_count || 0}">+ Queue</button>`
                        }
                    </div>
                </div>
            `;
        }).join('');
        
        gridContainer.innerHTML = cardsHtml;
        
        // Attach event listeners to cards (poster click opens modal)
        document.querySelectorAll('#score-queue-grid-inner .poster-card').forEach(card => {
            card.addEventListener('click', (e) => {
                // Don't open modal if clicking the queue/remove button
                if (e.target.classList.contains('queue-btn') || e.target.classList.contains('remove-btn')) return;
                
                const title = card.dataset.title;
                const score = parseFloat(card.dataset.score);
                const isCollection = card.dataset.isCollection === 'true';
                const movieCount = parseInt(card.dataset.movieCount || '0');
                
                let factors = [];
                let movies = [];
                
                try {
                    if (card.dataset.factors) {
                        factors = JSON.parse(card.dataset.factors);
                    }
                } catch (err) {
                    console.error('Failed to parse factors:', err);
                }
                
                if (isCollection) {
                    try {
                        if (card.dataset.movies && card.dataset.movies !== '[]') {
                            movies = JSON.parse(card.dataset.movies);
                        }
                    } catch (err) {
                        console.error('Failed to parse movies:', err);
                    }
                }
                
                showScoreDetails(title, score, factors, isCollection, movies, movieCount);
            });
        });
        
        // Attach event listeners to queue/remove buttons
        document.querySelectorAll('#score-queue-grid-inner .queue-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const movieId = parseInt(btn.dataset.movieId);
                const title = btn.dataset.title;
                const isCollection = btn.dataset.isCollection === 'true';
                const movieCount = parseInt(btn.dataset.movieCount || '1');
                manualQueueMovie(movieId, title, isCollection, movieCount, e);
            });
        });
        
        document.querySelectorAll('#score-queue-grid-inner .remove-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const movieId = parseInt(btn.dataset.movieId);
                const title = btn.dataset.title;
                const isCollection = btn.dataset.isCollection === 'true';
                removeFromQueue(movieId, title, isCollection, e);
            });
        });

        // Pagination
        const totalPages = data.pages || 1;
        const searchInfo = scoreQueueSearchActive ? ` (matching "${escapeHtml(scoreQueueSearch)}")` : '';
        const isMobile = window.innerWidth <= 768;
        
        let paginationHtml = '';
        
        if (isMobile) {
            // Mobile: simple text pagination (no input box)
            paginationHtml = `
                <div class="flex justify-between items-center w-full">
                    <span style="color: var(--text-secondary); font-size: 0.75rem;">${data.total} total${searchInfo}</span>
                    <div class="flex items-center gap-2">
                        <button onclick="changeScoreQueuePage(${scoreQueuePage - 1})" class="btn-sm btn-outline" ${scoreQueuePage <= 1 ? 'disabled' : ''} style="font-size: 0.7rem; padding: 0.25rem 0.5rem;">← Prev</button>
                        <span style="color: var(--text-secondary); font-size: 0.75rem;">${scoreQueuePage} / ${totalPages}</span>
                        <button onclick="changeScoreQueuePage(${scoreQueuePage + 1})" class="btn-sm btn-outline" ${scoreQueuePage >= totalPages ? 'disabled' : ''} style="font-size: 0.7rem; padding: 0.25rem 0.5rem;">Next →</button>
                    </div>
                </div>
            `;
        } else {
            // Desktop: full pagination with input box
            paginationHtml = `
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
        }
        
        document.getElementById('score-queue-pagination').innerHTML = paginationHtml;
        
        // Add event listener for page number input (desktop only)
        if (!isMobile) {
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
        }

        // Update sort icons
        updateSortIcons();

    } catch (e) {
        console.error('Failed to load score queue:', e);
        const gridContainer = document.getElementById('score-queue-grid-inner');
        if (gridContainer) {
            gridContainer.innerHTML = `<div class="col-span-full text-center py-8" style="color: var(--danger);">Failed to load score queue.</div>`;
        }
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
    // Remove active-sort class from all sortable headers
    document.querySelectorAll('.sortable').forEach(header => {
        header.classList.remove('active-sort');
    });
    
    // Add active-sort class to the currently sorted column
    const activeHeader = document.querySelector(`.sortable[data-sort="${scoreQueueSortBy}"]`);
    if (activeHeader) {
        activeHeader.classList.add('active-sort');
    }
}

// Clear All Scheduled Deletions
async function clearScheduledDeletions() {
    if (!confirm('Remove ALL movies from the scheduled deletions queue? This cannot be undone.')) return;
    
    try {
        const res = await fetch('/api/dashboard/scheduled/clear', { method: 'DELETE' });
        if (res.ok) {
            showToast('All scheduled deletions cleared', 'success');
            await loadScheduledDeletions();
            await loadQueueStatus();
            await loadScoreQueue();
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to clear scheduled deletions', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Manual Queue Collection
async function manualQueueCollection(collectionId, collectionName, movieCount) {
    const confirmMessage = `Queue collection "${collectionName}" (${movieCount} movies) for deletion?\n\nThis bypasses all protection rules and does not count toward your queue limit.`;
    
    if (!confirm(confirmMessage)) return;
    
    try {
        const res = await fetch(`/api/dashboard/scheduled/collection/${collectionId}`, { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            showToast(data.message, 'success');
            await loadScoreQueue();
            await loadScheduledDeletions();
            await loadQueueStatus();
        } else {
            showToast(data.detail || 'Failed to queue collection', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
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

// Render Scheduled Deletions as Poster Grid
function renderScheduledGrid(data) {
    const gridContainer = document.getElementById('scheduled-grid-inner');
    const badge = document.getElementById('scheduled-badge');
    
    if (!gridContainer) return;
    
    if (!data.items || data.items.length === 0) {
        if (badge) badge.textContent = '0 items';
        gridContainer.innerHTML = `<div class="col-span-full text-center py-8" style="color: var(--text-secondary);">No scheduled deletions</div>`;
        return;
    }
    
    if (badge) badge.textContent = `${data.items.length} items`;
    
    const cardsHtml = data.items.map(item => {
        // Determine score badge class
        let scoreClass = 'score-high';
        if (item.manual_for_deletion) {
            scoreClass = 'badge-manual';
        } else {
            if (item.normalized_score < 60) scoreClass = 'score-medium';
            if (item.normalized_score < 30) scoreClass = 'score-low';
        }
        
        // Calculate countdown text
        let countdownText = '';
        if (item.scheduled_date) {
            const deleteDate = new Date(item.scheduled_date);
            const now = new Date();
            const daysRemaining = Math.ceil((deleteDate - now) / (1000 * 60 * 60 * 24));
            
            if (daysRemaining < 0) {
                countdownText = 'overdue';
            } else if (daysRemaining === 0) {
                countdownText = '0 day(s)';
            } else if (daysRemaining === 1) {
                countdownText = '1 day(s)';
            } else {
                countdownText = `${daysRemaining} day(s)`;
            }
        }
        
        // Get poster URL or fallback
        let posterUrl = '/static/no-poster.png';
        const title = escapeHtml(item.movie_title || 'Unknown');
        const movieId = item.collection_id || item.movie_id;
        const isCollection = !!item.collection_id;

        // For collections, find the oldest movie's poster
        if (isCollection && item.movies && item.movies.length > 0) {
            // Find the movie with the smallest year (oldest)
            const oldestMovie = [...item.movies].sort((a, b) => {
                const yearA = a.movie_year || a.year || 9999;
                const yearB = b.movie_year || b.year || 9999;
                return yearA - yearB;
            })[0];
            posterUrl = oldestMovie.poster_url || '/static/no-poster.png';
        } else if (!isCollection && item.poster_url) {
            posterUrl = item.poster_url;
        }
        
        // Store data for modal
        const factorsJson = JSON.stringify(item.factors || []).replace(/'/g, "&#39;");
        const moviesData = isCollection ? JSON.stringify(item.movies || []).replace(/'/g, "&#39;") : '[]';

        const collectionBadge = isCollection ? `<span class="collection-badge">📁 Collection</span>` : '';
        
        return `
            <div class="poster-card" data-movie-id="${movieId}" data-title="${title}" data-score="${item.normalized_score}" data-factors='${factorsJson}' data-is-collection="${isCollection}" data-movies='${moviesData}' data-movie-count="${item.movie_count || 0}">
                <div class="poster-image-container">
                    <img src="${posterUrl}" alt="${title}" class="poster-image" loading="lazy" onerror="this.src='/static/no-poster.png'">
                    <div class="poster-overlay">
                        <button class="details-overlay-btn">🔍 Details</button>
                    </div>
                    ${countdownText ? `<div class="countdown-badge">${countdownText}</div>` : ''}
                    ${collectionBadge}
                    </div>
                <div class="poster-meta-bar">
                    <span class="score-badge ${scoreClass}">${item.normalized_score.toFixed(1)}</span>
                    <button class="remove-btn" data-movie-id="${movieId}" data-title="${title}" data-is-collection="${isCollection}">✕ Remove</button>
                </div>
            </div>
        `;
    }).join('');
    
    gridContainer.innerHTML = cardsHtml;
    
    // Attach event listeners to cards (poster click opens modal)
    document.querySelectorAll('.poster-card').forEach(card => {
        card.addEventListener('click', (e) => {
            // Don't open modal if clicking the remove button
            if (e.target.classList.contains('remove-btn')) return;
            
            const title = card.dataset.title;
            const score = parseFloat(card.dataset.score);
            const isCollection = card.dataset.isCollection === 'true';
            const movieCount = parseInt(card.dataset.movieCount || '0');
            
            let factors = [];
            let movies = [];
            
            try {
                if (card.dataset.factors) {
                    factors = JSON.parse(card.dataset.factors);
                }
            } catch (err) {
                console.error('Failed to parse factors:', err);
            }
            
            if (isCollection) {
                try {
                    if (card.dataset.movies && card.dataset.movies !== '[]') {
                        movies = JSON.parse(card.dataset.movies);
                    }
                } catch (err) {
                    console.error('Failed to parse movies:', err);
                }
            }
            
            showScoreDetails(title, score, factors, isCollection, movies, movieCount);
        });
    });
    
    // Attach event listeners to remove buttons
    document.querySelectorAll('.remove-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const movieId = parseInt(btn.dataset.movieId);
            const title = btn.dataset.title;
            const isCollection = btn.dataset.isCollection === 'true';
            removeFromQueue(movieId, title, isCollection, e);
        });
    });
}

function handleScheduledDetailsClick(e) {
    const btn = e.currentTarget;
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
    }
    
    if (isCollection) {
        try {
            const moviesAttr = btn.getAttribute('data-movies');
            if (moviesAttr && moviesAttr !== 'undefined' && moviesAttr !== '[]') {
                movies = JSON.parse(moviesAttr);
            }
        } catch (err) {
            console.error('Failed to parse movies:', err);
        }
    }
    
    showScoreDetails(title, score, factors, isCollection, movies, movieCount);
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

// Manual Queue Movie
async function manualQueueMovie(movieId, title, isCollection = false, movieCount = 1, event = null) {
    // Confirmation dialog
    let confirmMessage = '';
    if (isCollection) {
        confirmMessage = `Queue collection "${title}" (${movieCount} movies) for deletion?\n\nThis bypasses all protection rules and does not count toward your queue limit.`;
    } else {
        confirmMessage = `Manually queue "${title}" for deletion?\n\nThis bypasses all protection rules and does not count toward your queue limit.`;
    }
    
    if (!confirm(confirmMessage)) return;
    
    // Get and disable the button if event provided
    let btn = null;
    let originalText = '';
    if (event && event.currentTarget) {
        btn = event.currentTarget;
        originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⟳ Queuing...';
    }
    
    try {
        const res = await fetch(`/api/dashboard/scheduled/${movieId}`, { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            showToast(data.message, 'success');
            // Refresh both tables
            await loadScoreQueue();
            await loadScheduledDeletions();
            await loadQueueStatus();
        } else {
            showToast(data.detail || 'Failed to queue movie', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        // Button will be replaced on re-render, no need to restore
        if (btn) {
            btn.disabled = false;
        }
    }
}

// Run status polling — updates progress bar and re-loads queue when done
function startRunStatusPolling() {
    const cancelBtn = document.getElementById('cancel-run-sidebar');
    const scoreProgressBar = document.getElementById('progress-bar-sidebar');
    const cullProgressBar = document.getElementById('progress-bar-sidebar');
    const scoreProgressLabel = document.getElementById('progress-text-sidebar');
    const cullProgressLabel = document.getElementById('progress-text-sidebar');
    const progressTitle = document.getElementById('progress-title');

    cancelBtn.classList.remove('hidden');

    if (runStatusInterval) clearInterval(runStatusInterval);

    runStatusInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/run/status');
            const data = await res.json();

            if (data.is_running) {
                // Show sidebar progress container
                showSidebarProgress(data.run_type === 'score' ? 'Score Run' : 'Cull Run', data.current_movie || 'Starting...');
                
                // Ensure indeterminate animation is applied
                if (scoreProgressBar) {
                    scoreProgressBar.classList.add('progress-indeterminate');
                }
                
                cancelBtn.onclick = () => cancelRun(data.run_id);
                cancelBtn.classList.remove('hidden');
            } else {
                // Hide sidebar progress container
                hideSidebarProgress();
                
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

// ========== ADD THIS HELPER FUNCTION HERE (BEFORE showScoreDetails) ==========
function formatWatchedDetailsMobile(details) {
    if (!details) return '';
    
    // Handle "Never watched" case
    if (details.includes('Never watched')) {
        return 'Never watched';
    }
    
    // Parse: "Play count: 1 | Last watched: 2518 days ago"
    const playMatch = details.match(/Play count:\s*(\d+)/);
    const lastMatch = details.match(/Last watched:\s*(\d+)\s*days? ago/);
    
    if (playMatch) {
        const playCount = playMatch[1];
        if (lastMatch) {
            const days = lastMatch[1];
            const playText = playCount === '1' ? '1 time' : `${playCount} times`;
            const dayText = days === '1' ? 'day' : 'days';
            return `Play count: ${playCount} | ${days} ${dayText} ago`;
        }
        return `${playCount} · Never`;
    }
    
    // Fallback to original if parsing fails
    return details;
}
// ========== END HELPER FUNCTION ==========

// Score details modal - handles both individual movies and collections
function showScoreDetails(title, score, factors, isCollection = false, movies = [], movieCount = 0) {
    // Remove any existing modal
    const existingModal = document.getElementById('score-modal');
    if (existingModal) existingModal.remove();

    let contentHtml = '';
    
    if (isCollection && movies && movies.length > 0) {
        // Sort movies by year (oldest first)
        const sortedMovies = [...movies].sort((a, b) => {
            const yearA = a.movie_year || a.year || 0;
            const yearB = b.movie_year || b.year || 0;
            return yearA - yearB;
        });

        // Display collection members
        contentHtml = `
            <div class="border-t pt-4" style="border-color: var(--border-color);">
                <div class="text-sm font-semibold mb-3" style="color: var(--accent);">
                    📁 Collection contains ${movieCount} movie${movieCount !== 1 ? 's' : ''}:
                </div>
                <div class="space-y-2 max-h-96 overflow-y-auto">
                   ${sortedMovies.map((movie, idx) => {
                        const movieTitle = escapeHtml(movie.movie_title || movie.title || 'Unknown');
                        const movieYear = movie.movie_year || movie.year || 'N/A';
                        const movieScore = movie.individual_normalized_score || movie.normalized_score || 'N/A';
                        const movieSize = movie.size_gb ? `${movie.size_gb.toFixed(1)} GB` : '';
                        const movieFactors = movie.factors || [];
                        const expandId = `movie-expand-${Date.now()}-${idx}`;
                        
                        // Build factor breakdown HTML (same format as main modal)
                        let factorsHtml = '';
                        if (movieFactors.length > 0) {
                            factorsHtml = '<div class="mt-2 pt-2 border-t" style="border-color: var(--border-color);">';
                            for (const f of movieFactors) {
                                const pct = (f.contribution * 100).toFixed(1);
                                const barWidth = Math.min(Math.round(f.raw_score * 100), 100);
        
                                // Get details text (same as main modal)
                                let detailsText = f.details || '';
                                if (f.name === 'Watched' && window.innerWidth <= 768) {
                                    detailsText = formatWatchedDetailsMobile(detailsText);
                                }
        
                                factorsHtml += `
                                    <div class="mb-2">
                                        <div class="flex justify-between text-xs mb-1">
                                            <span style="color: var(--text-secondary);">${f.name}</span>
                                            <span style="color: var(--text-secondary);">${detailsText}</span>
                                        </div>
                                        <div class="flex items-center gap-2">
                                            <div class="flex-1 rounded-full h-1.5" style="background: var(--border-color)">
                                                <div class="h-1.5 rounded-full" style="width: ${barWidth}%; background: var(--accent);"></div>
                                            </div>
                                            <span class="text-xs font-mono w-10 text-right" style="color: var(--text-secondary);">${pct}%</span>
                                        </div>
                                    </div>
                                `;
                            }
                            factorsHtml += '</div>';
                        }
                        
                        return `
                            <div class="border rounded-lg" style="border-color: var(--border-color); background: var(--bg-primary);">
                                <div class="p-2 cursor-pointer hover:bg-purple-bg movie-row-header" data-expand-id="${expandId}">
                                    <div class="flex justify-between items-center">
                                        <div class="flex items-center gap-2">
                                            <span class="text-xs expand-icon" id="icon-${expandId}" style="color: var(--accent);">▶</span>
                                            <div>
                                                <span class="font-medium">${movieTitle}</span>
                                                <span class="text-xs ml-1" style="color: var(--text-secondary);">(${movieYear})</span>
                                            </div>
                                        </div>
                                        <span class="badge" style="background: var(--info-bg); color: var(--info); font-size: 10px; flex-shrink: 0; margin-left: auto;">Score: ${typeof movieScore === 'number' ? movieScore.toFixed(1) : movieScore}</span>
                                    </div>
                                    ${movieSize ? `<div class="text-xs mt-1 ml-5" style="color: var(--text-secondary);">${movieSize}</div>` : ''}
                                </div>
                                <div id="${expandId}" class="expandable-content px-3 pb-2" style="display: none;">
                                    ${factorsHtml}
                                </div>
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
                        <span style="color: var(--text-secondary)">
                            ${f.name === 'Watched' && window.innerWidth <= 768 ? formatWatchedDetailsMobile(f.details) : (f.details || '')}
                        </span>
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

    // Add expand/collapse handlers for collection movie rows
    modal.querySelectorAll('.movie-row-header').forEach(header => {
        header.addEventListener('click', (e) => {
            e.stopPropagation();
            const expandId = header.dataset.expandId;
            const content = document.getElementById(expandId);
            const icon = document.getElementById(`icon-${expandId}`);
        
            if (content) {
                if (content.style.display === 'none') {
                    content.style.display = 'block';
                    if (icon) icon.textContent = '▼';
                } else {
                    content.style.display = 'none';
                    if (icon) icon.textContent = '▶';
                }
            }
        });
    });

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

    document.getElementById('run-score-btn').addEventListener('click', triggerScoreRun);
    document.getElementById('run-cull-btn').addEventListener('click', triggerCullRun);
    
    // ⋮ menu for Scheduled Deletions
    const menuBtn = document.getElementById('scheduled-deletions-menu-btn');
    const menu = document.getElementById('scheduled-deletions-menu');
    
    if (menuBtn && menu) {
        menuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('hidden');
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', (e) => {
            if (!menuBtn.contains(e.target) && !menu.contains(e.target)) {
                menu.classList.add('hidden');
            }
        });
        
        // Clear all button inside menu
        const clearMenuBtn = document.getElementById('clear-scheduled-menu-btn');
        if (clearMenuBtn) {
            clearMenuBtn.addEventListener('click', clearScheduledDeletions);
        }
    }

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

    if (searchInput) {
        let debounceTimer;
    
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => {
                scoreQueueSearch = e.target.value;
                scoreQueuePage = 1;
                scoreQueueSearchActive = scoreQueueSearch.trim() !== '';
                loadScoreQueue();
            }, 300);
        });
    }

    // Load saved collapse state for Scheduled Deletions
    loadScheduledDeletionsState();

    // On mobile, force per-page to 12 regardless of dropdown
    if (window.innerWidth <= 768) {
        scoreQueuePerPage = 12;
    }
    
    // Auto-refresh every 30 seconds — queue status and deletions only, not score queue
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(() => {
        loadQueueStatus();
        loadScheduledDeletions();
    }, 30000);
});