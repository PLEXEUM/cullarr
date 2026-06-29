// Settings page JavaScript for Cullarr - Updated

// DOM Elements
let ageSlider, sizeSlider, ratingSlider, qualitySlider, watchedSlider;
let ageVal, sizeVal, ratingVal, qualityVal, watchedVal;
let ageMaxDays, sizeMaxGb;
let minScoreThreshold;
let deletionsPerDay;
let debounceTimer = null;

// Preset configurations (1-10 scale, no sum constraint)
const PRESETS = {
    balanced: { age: 5, size: 5, rating: 5, quality: 5, watched: 5 },
    spaceSaver: { age: 6, size: 8, rating: 2, quality: 2, watched: 2 },
    qualityKeeper: { age: 3, size: 3, rating: 8, quality: 8, watched: 3 },
    freshness: { age: 8, size: 4, rating: 3, quality: 3, watched: 2 }
};

// Collection dropdown helpers
let collectionsLoaded = false;
let collectionsList = [];

async function loadPlexCollections() {
    if (collectionsLoaded) return collectionsList;
    
    try {
        const res = await fetch('/api/plex/collections');
        if (!res.ok) {
            console.error('Failed to load collections:', await res.text());
            return [];
        }
        const data = await res.json();
        collectionsList = data.collections || [];
        collectionsLoaded = true;
        return collectionsList;
    } catch (e) {
        console.error('Failed to load Plex collections:', e);
        return [];
    }
}

async function populateCollectionDropdown() {
    const select = document.getElementById('plex-collection-select');
    if (!select) return;
    
    select.innerHTML = '<option value="">Loading collections...</option>';
    select.disabled = true;
    
    const collections = await loadPlexCollections();
    
    if (collections.length === 0) {
        select.innerHTML = '<option value="">No collections found in Plex</option>';
        select.disabled = true;
        return;
    }
    
    // Get currently saved collection key
    let savedKey = null;
    try {
        const configRes = await fetch('/api/plex/config');
        const config = await configRes.json();
        savedKey = config.collection_key;
    } catch (e) {
        console.error('Failed to load saved collection key:', e);
    }
    
    let html = '<option value="">-- Select a collection --</option>';
    for (const collection of collections) {
        const selected = savedKey === collection.key ? 'selected' : '';
        html += `<option value="${collection.key}" ${selected}>${escapeHtml(collection.title)}</option>`;
    }
    select.innerHTML = html;
    select.disabled = false;
}

// Called when user clicks dropdown
async function onCollectionDropdownClick() {
    if (collectionsLoaded) return;
    const select = document.getElementById('plex-collection-select');
    if (!select) return;
    
    select.innerHTML = '<option value="">Loading collections...</option>';
    select.disabled = true;
    
    const collections = await loadPlexCollections();
    
    if (collections.length === 0) {
        select.innerHTML = '<option value="">No collections found in Plex</option>';
        select.disabled = true;
        return;
    }
    
    let savedKey = null;
    try {
        const configRes = await fetch('/api/plex/config');
        const config = await configRes.json();
        savedKey = config.collection_key;
    } catch (e) {
        console.error('Failed to load saved collection key:', e);
    }
    
    let html = '<option value="">-- Select a collection --</option>';
    for (const collection of collections) {
        const selected = savedKey === collection.key ? 'selected' : '';
        html += `<option value="${collection.key}" ${selected}>${escapeHtml(collection.title)}</option>`;
    }
    select.innerHTML = html;
    select.disabled = false;
}

// Save collection selection
async function saveCollectionSelection() {
    const select = document.getElementById('plex-collection-select');
    const selectedKey = select ? select.value : null;
    const url = document.getElementById('plex-url').value;
    
    if (!url) {
        showToast('Please enter Plex server URL first', 'error');
        return;
    }
    
    try {
        const res = await fetch('/api/plex/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: url,
                collection_key: selectedKey || null,
                enabled: true
            })
        });
        
        if (res.ok) {
            showToast(selectedKey ? 'Collection selection saved' : 'Collection cleared (Cullarr will not add to any collection)', 'success');
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save collection', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

// Load all settings on page load
document.addEventListener('DOMContentLoaded', () => {
    // Initialize DOM references
    ageSlider = document.getElementById('age-weight');
    sizeSlider = document.getElementById('size-weight');
    ratingSlider = document.getElementById('rating-weight');
    qualitySlider = document.getElementById('quality-weight');
    watchedSlider = document.getElementById('watched-weight');
    
    ageVal = document.getElementById('age-weight-val');
    sizeVal = document.getElementById('size-weight-val');
    ratingVal = document.getElementById('rating-weight-val');
    qualityVal = document.getElementById('quality-weight-val');
    watchedVal = document.getElementById('watched-weight-val');
    
    ageMaxDays = document.getElementById('age-max-days');
    sizeMaxGb = document.getElementById('size-max-gb');
    minScoreThreshold = document.getElementById('min-score-threshold');
    deletionsPerDay = document.getElementById('deletions-per-day');
    
    // Load data
    loadRadarrConfig();
    loadPlexConfig();
    loadWeights();
    loadSettings();
    
    // Setup event listeners
    setupEventListeners();
});

function setupEventListeners() {
    // Radarr
    document.getElementById('radarr-test-btn').addEventListener('click', testAndSaveRadarr);
    document.getElementById('radarr-clear-btn').addEventListener('click', clearRadarr);
    
    // Plex
    document.getElementById('plex-auth-btn').addEventListener('click', authenticateAndSavePlex);
    document.getElementById('plex-clear-btn').addEventListener('click', clearPlex);
    
    // Save collection selection button 
    const saveCollectionBtn = document.getElementById('plex-save-collection-select-btn');
    if (saveCollectionBtn) {
        saveCollectionBtn.addEventListener('click', saveCollectionSelection);
    }
   
    // Weight sliders - update display only
    const sliders = [ageSlider, sizeSlider, ratingSlider, qualitySlider, watchedSlider];
    sliders.forEach(slider => {
        if (slider) {
            slider.addEventListener('input', (e) => {
                updateWeightDisplay(e.target.id);
            });
        }
    });
    
    // Refresh Preview button
    const refreshPreviewBtn = document.getElementById('refresh-preview-btn');
    if (refreshPreviewBtn) {
        refreshPreviewBtn.addEventListener('click', () => {
            updateLivePreview();
        });
    }
    
    // Preset buttons
    document.getElementById('preset-balanced')?.addEventListener('click', () => applyPreset('balanced'));
    document.getElementById('preset-space-saver')?.addEventListener('click', () => applyPreset('spaceSaver'));
    document.getElementById('preset-quality-keeper')?.addEventListener('click', () => applyPreset('qualityKeeper'));
    document.getElementById('preset-freshness')?.addEventListener('click', () => applyPreset('freshness'));
    
    // Save buttons
    document.getElementById('save-weights-btn').addEventListener('click', saveWeights);
    document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
    
    // Recalibrate button
    document.getElementById('recalibrate-btn')?.addEventListener('click', recalibrateAdvanced);
}

function toggleAdvancedSettings() {
    const section = document.getElementById('advanced-settings-section');
    const icon = document.getElementById('advanced-settings-icon');
    if (section.style.display === 'none' || section.style.display === '') {
        section.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
    } else {
        section.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
    }
}

function toggleDeletionSpacing() {
    const section = document.getElementById('deletion-spacing-section');
    const icon = document.getElementById('deletion-spacing-icon');
    if (section.style.display === 'none' || section.style.display === '') {
        section.style.display = 'block';
        icon.style.transform = 'rotate(180deg)';
    } else {
        section.style.display = 'none';
        icon.style.transform = 'rotate(0deg)';
    }
}

function updateWeightDisplay(id) {
    const val = document.getElementById(id).value;
    const displayId = id.replace('-weight', '-weight-val');
    const display = document.getElementById(displayId);
    if (display) display.textContent = `${val} / 10`;
}

function applyPreset(presetName) {
    const preset = PRESETS[presetName];
    if (!preset) return;
    
    ageSlider.value = preset.age;
    sizeSlider.value = preset.size;
    ratingSlider.value = preset.rating;
    qualitySlider.value = preset.quality;
    watchedSlider.value = preset.watched;
    
    updateWeightDisplay('age-weight');
    updateWeightDisplay('size-weight');
    updateWeightDisplay('rating-weight');
    updateWeightDisplay('quality-weight');
    updateWeightDisplay('watched-weight');
    
    showToast(`Preset "${presetName}" applied`, 'info');
}

// Manual preview update (only called when Refresh button is clicked)
async function updateLivePreview() {
    const previewDiv = document.getElementById('live-preview-content');
    if (!previewDiv) return;
    
    previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Updating preview...</div>';
    
    try {
        // Get raw 1-10 values
        const ageRaw = parseInt(ageSlider.value);
        const sizeRaw = parseInt(sizeSlider.value);
        const ratingRaw = parseInt(ratingSlider.value);
        const qualityRaw = parseInt(qualitySlider.value);
        const watchedRaw = parseInt(watchedSlider.value);
        
        const totalRaw = ageRaw + sizeRaw + ratingRaw + qualityRaw + watchedRaw;
        
        // Calculate percentages for preview API
        const weights = {
            age_raw: parseInt(ageSlider.value),
            size_raw: parseInt(sizeSlider.value),
            rating_raw: parseInt(ratingSlider.value),
            quality_raw: parseInt(qualitySlider.value),
            watched_raw: parseInt(watchedSlider.value),
            age_max_days: parseInt(ageMaxDays.value),
            size_max_gb: parseFloat(sizeMaxGb.value)
        };
        
        const res = await fetch('/api/settings/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(weights)
        });
        
        if (!res.ok) {
            previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Preview unavailable — run a score cycle first</div>';
            return;
        }
        
        const data = await res.json();
        
        if (!data.movie) {
            previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">No movies found in library</div>';
            return;
        }
        
        renderPreview(data.movie);
        
    } catch (e) {
        console.error('Preview failed:', e);
        previewDiv.innerHTML = '<div class="text-center py-8" style="color: var(--text-secondary);">Preview unavailable</div>';
    }
}

function renderPreview(movie) {
    const previewDiv = document.getElementById('live-preview-content');
    if (!previewDiv) return;
    
    const score = (movie.raw_score * 100).toFixed(1);
    const wouldQueue = movie.raw_score * 100 > (parseInt(minScoreThreshold?.value) || 0);
    const queueBadge = wouldQueue 
        ? '<span class="badge" style="background: var(--success-bg); color: var(--success);">✓ Would queue</span>'
        : '<span class="badge" style="background: var(--danger-bg); color: var(--danger);">✗ Below threshold</span>';
    
    const factors = movie.factors || [];
    const factorHtml = factors.map(f => {
        const displayPct = (f.contribution * 100).toFixed(1);
        const barWidth = Math.min(Math.round(f.raw_score * 100), 100);
        return `
            <div class="mb-2">
                <div class="flex justify-between text-xs mb-1">
                    <span>${f.name}</span>
                    <span style="color: var(--text-secondary);">${f.details || ''}</span>
                </div>
                <div class="flex items-center gap-2">
                    <div class="flex-1 rounded-full h-1.5" style="background: var(--border-color)">
                        <div class="h-1.5 rounded-full" style="width: ${barWidth}%; background: var(--accent);"></div>
                    </div>
                    <span class="text-xs font-mono w-10 text-right" style="color: var(--text-secondary);">${displayPct}%</span>
                </div>
            </div>
        `;
    }).join('');
    
    previewDiv.innerHTML = `
        <div class="space-y-3">
            <div class="flex justify-between items-start">
                <div>
                    <div class="font-semibold">${escapeHtml(movie.movie_title)} (${movie.movie_year || 'N/A'})</div>
                    <div class="text-xs" style="color: var(--text-secondary);">${movie.size_gb?.toFixed(1) || 0} GB • ${movie.age_days || 0} days old</div>
                </div>
                ${queueBadge}
            </div>
            <div class="border-t pt-2" style="border-color: var(--border-color);">
                ${factorHtml}
            </div>
            <div class="border-t pt-2 flex justify-between items-center" style="border-color: var(--border-color);">
                <span class="text-sm font-semibold">TOTAL SCORE:</span>
                <span class="text-xl font-bold" style="color: var(--accent);">${score}</span>
            </div>
        </div>
    `;
}

async function recalibrateAdvanced() {
    const btn = document.getElementById('recalibrate-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Analyzing library...';
    
    try {
        const res = await fetch('/api/settings/recalibrate', { method: 'POST' });
        const data = await res.json();
        
        if (res.ok) {
            ageMaxDays.value = data.age_max_days;
            sizeMaxGb.value = data.size_max_gb;
            showToast(data.message, 'success');
        } else {
            showToast(data.detail || 'Recalibration failed', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

// ----- Radarr Functions -----

async function loadRadarrConfig() {
    try {
        const res = await fetch('/api/radarr/config');
        const data = await res.json();
        if (data.configured) {
            document.getElementById('radarr-url').value = data.url || '';
            document.getElementById('radarr-status').innerHTML = '<span style="color: var(--success);">✅ Configured</span>';
        } else {
            document.getElementById('radarr-status').innerHTML = '<span style="color: var(--warning);">⚠ Not configured</span>';
        }
    } catch (e) {
        console.error('Failed to load Radarr config:', e);
    }
}

async function testAndSaveRadarr() {
    const url = document.getElementById('radarr-url').value;
    const apiKey = document.getElementById('radarr-api-key').value;
    
    if (!url || !apiKey) {
        showToast('Please enter both URL and API key', 'error');
        return;
    }
    
    const btn = document.getElementById('radarr-test-btn');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    
    try {
        const testRes = await fetch('/api/radarr/config/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        
        if (!testRes.ok) {
            const err = await testRes.json();
            showToast(err.detail || 'Connection failed', 'error');
            return;
        }
        
        const saveRes = await fetch('/api/radarr/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        
        if (saveRes.ok) {
            showToast('Radarr configured successfully', 'success');
            loadRadarrConfig();
        } else {
            showToast('Failed to save configuration', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test & Save';
    }
}

async function clearRadarr() {
    if (!confirm('Clear Radarr configuration?')) return;
    try {
        const res = await fetch('/api/radarr/config', { method: 'DELETE' });
        if (res.ok) {
            showToast('Radarr configuration cleared', 'success');
            document.getElementById('radarr-url').value = '';
            document.getElementById('radarr-api-key').value = '';
            loadRadarrConfig();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ----- Plex Functions -----

let plexPopupWindow = null;
let plexPollInterval = null;
let plexTimeoutTimer = null;

async function loadPlexConfig() {
    try {
        const res = await fetch('/api/plex/config');
        const data = await res.json();
        
        document.getElementById('plex-url').value = data.url || '';
        
        const statusDiv = document.getElementById('plex-status');
        if (data.configured && data.url && data.api_key === '[REDACTED]') {
            statusDiv.innerHTML = '<span style="color: var(--success);">✅ Configured</span>';
        } else if (data.api_key === '[REDACTED]') {
            statusDiv.innerHTML = '<span style="color: var(--warning);">⚠ Authenticated (token saved)</span>';
        } else {
            statusDiv.innerHTML = '<span style="color: var(--warning);">⚠ Not configured</span>';
        }
        
        // Load collections and set saved selection
        await populateCollectionDropdown();
        
        const select = document.getElementById('plex-collection-select');
        if (select && data.collection_key) {
            select.value = data.collection_key;
        }
        
    } catch (e) {
        console.error('Failed to load Plex config:', e);
    }
}

async function authenticateAndSavePlex() {
    const url = document.getElementById('plex-url').value;
    
    if (!url) {
        showToast('Please enter Plex server URL', 'error');
        return;
    }
    
    if (plexPopupWindow && !plexPopupWindow.closed) {
        plexPopupWindow.close();
    }
    
    if (plexPollInterval) {
        clearInterval(plexPollInterval);
        plexPollInterval = null;
    }
    if (plexTimeoutTimer) {
        clearTimeout(plexTimeoutTimer);
        plexTimeoutTimer = null;
    }
    
    const btn = document.getElementById('plex-auth-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ Authenticating...';
    
    try {
        const response = await fetch('/api/plex/oauth/pin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            throw new Error('Failed to start Plex login');
        }
        
        const data = await response.json();
        
        plexPopupWindow = window.open(data.auth_url, 'PlexAuth', 'width=800,height=600,scrollbars=yes');
        if (!plexPopupWindow) {
            showToast('Popup blocked. Please allow popups for this site.', 'warning');
            btn.disabled = false;
            btn.textContent = originalText;
            return;
        }
        plexPopupWindow.focus();
        
        let pollCount = 0;
        plexPollInterval = setInterval(async () => {
            pollCount++;
            try {
                const pollRes = await fetch(`/api/plex/oauth/pin/${data.id}`);
                const pollData = await pollRes.json();
                
                if (pollData.authenticated) {
                    clearInterval(plexPollInterval);
                    clearTimeout(plexTimeoutTimer);
                    if (plexPopupWindow && !plexPopupWindow.closed) {
                        plexPopupWindow.close();
                    }
                    
                                        const selectedKey = document.getElementById('plex-collection-select')?.value;
                    const saveRes = await fetch('/api/plex/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ 
                            url: url, 
                            collection_key: selectedKey || null,
                            enabled: true 
                        })
                    });
                    
                    if (saveRes.ok) {
                        showToast('Plex configured successfully!', 'success');
                        await loadPlexConfig();
                    } else {
                        showToast('Token saved but failed to save URL/label', 'error');
                    }
                    
                    btn.disabled = false;
                    btn.textContent = originalText;
                } else if (pollCount > 300) {
                    clearInterval(plexPollInterval);
                    clearTimeout(plexTimeoutTimer);
                    showToast('Plex login timed out', 'error');
                    btn.disabled = false;
                    btn.textContent = originalText;
                }
            } catch (e) {
                console.error('Poll error:', e);
            }
        }, 1000);
        
        plexTimeoutTimer = setTimeout(() => {
            if (plexPollInterval) {
                clearInterval(plexPollInterval);
                showToast('Plex login timed out after 5 minutes', 'error');
                btn.disabled = false;
                btn.textContent = originalText;
            }
        }, 300000);
        
    } catch (error) {
        console.error('Plex setup error:', error);
        showToast('Failed to start Plex login: ' + error.message, 'error');
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

async function clearPlex() {
    if (!confirm('Clear Plex configuration? This will remove URL, token, and collection selection.')) return;
    
    try {
        const res = await fetch('/api/plex/config', { method: 'DELETE' });
        if (res.ok) {
            showToast('Plex configuration cleared', 'success');
            document.getElementById('plex-url').value = '';
            await loadPlexConfig();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ----- Weights Functions -----

async function loadWeights() {
    try {
        const res = await fetch('/api/settings/weights');
        const data = await res.json();
        
        // Read raw values directly (1-10 scale)
        ageSlider.value = data.age_raw || 5;
        sizeSlider.value = data.size_raw || 5;
        ratingSlider.value = data.rating_raw || 5;
        qualitySlider.value = data.quality_raw || 5;
        watchedSlider.value = data.watched_raw || 5;
        
        updateWeightDisplay('age-weight');
        updateWeightDisplay('size-weight');
        updateWeightDisplay('rating-weight');
        updateWeightDisplay('quality-weight');
        updateWeightDisplay('watched-weight');
        
        ageMaxDays.value = data.age_max_days || 365;
        sizeMaxGb.value = data.size_max_gb || 100;
        
    } catch (e) {
        console.error('Failed to load weights:', e);
    }
}

async function saveWeights() {
    // Get raw 1-10 values directly from sliders
    const payload = {
        age_raw: parseInt(ageSlider.value),
        size_raw: parseInt(sizeSlider.value),
        rating_raw: parseInt(ratingSlider.value),
        quality_raw: parseInt(qualitySlider.value),
        watched_raw: parseInt(watchedSlider.value),
        age_max_days: parseInt(ageMaxDays.value),
        size_max_gb: parseFloat(sizeMaxGb.value),
        protection_days: parseInt(document.getElementById('protection-days').value)
    };
    
    try {
        const res = await fetch('/api/settings/weights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            showToast('Weights saved successfully', 'success');
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save weights', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ----- Settings Functions -----

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        
        document.getElementById('cullarr-enabled').checked = data.enabled || false;
        document.getElementById('score-cron').value = data.score_cron || '0 3 * * 0';
        document.getElementById('cull-cron').value = data.cull_cron || '0 2 * * *';
        document.getElementById('max-queued').value = data.max_queued !== undefined ? data.max_queued : 20;
        if (deletionsPerDay) deletionsPerDay.value = data.deletions_per_day !== undefined ? data.deletions_per_day : 0;
        document.getElementById('delete-after-days').value = data.delete_after_days !== undefined ? data.delete_after_days : 7;
        document.getElementById('protection-days').value = data.protection_days || 30;
        document.getElementById('collection-grouping').checked = data.collection_grouping || false;
        if (minScoreThreshold) minScoreThreshold.value = data.min_score_threshold || 0;
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
}

async function saveSettings() {
    const payload = {
        enabled: document.getElementById('cullarr-enabled').checked,
        score_cron: document.getElementById('score-cron').value,
        cull_cron: document.getElementById('cull-cron').value,
        max_queued: parseInt(document.getElementById('max-queued').value),
        deletions_per_day: parseInt(deletionsPerDay?.value || 0),
        delete_after_days: parseInt(document.getElementById('delete-after-days').value),
        collection_grouping: document.getElementById('collection-grouping').checked,
        min_score_threshold: parseInt(minScoreThreshold?.value || 0)
    };
    
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            showToast('Settings saved successfully', 'success');
        } else {
            const err = await res.json();
            showToast(err.detail || 'Failed to save settings', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>]/g, m => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[m] || m);
}