// Settings page JavaScript for Cullarr

// Load all settings on page load
document.addEventListener('DOMContentLoaded', () => {
    loadRadarrConfig();
    loadPlexConfig();
    loadWeights();
    loadSettings();
    setupEventListeners();
});

function setupEventListeners() {
    // Radarr
    document.getElementById('radarr-test-btn').addEventListener('click', testAndSaveRadarr);
    document.getElementById('radarr-clear-btn').addEventListener('click', clearRadarr);
    
    // Plex
    document.getElementById('plex-test-btn').addEventListener('click', testAndSavePlex);
    document.getElementById('plex-clear-btn').addEventListener('click', clearPlex);
    document.getElementById('plex-oauth-btn').addEventListener('click', startPlexOAuth);
    
    // Weights
    const weightSliders = ['age-weight', 'size-weight', 'rating-weight', 'quality-weight', 'monitored-weight', 'watched-weight'];
    weightSliders.forEach(id => {
        const slider = document.getElementById(id);
        if (slider) {
            slider.addEventListener('input', () => updateWeightDisplay(id));
        }
    });
    document.getElementById('save-weights-btn').addEventListener('click', saveWeights);
    
    // Settings
    document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
}

// Radarr
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
        // Test connection
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
        
        // Save config
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

// Plex
async function loadPlexConfig() {
    try {
        const res = await fetch('/api/plex/config');
        const data = await res.json();
        document.getElementById('plex-enabled').checked = data.enabled || false;
        document.getElementById('plex-url').value = data.url || '';
        document.getElementById('plex-label').value = data.label_text || 'Cullarr - Pending Deletion';
        
        if (data.configured && data.url) {
            document.getElementById('plex-status').innerHTML = '<span style="color: var(--success);">✅ Configured</span>';
            document.getElementById('plex-authed-status').classList.remove('hidden');
        } else if (data.configured && !data.url) {
            document.getElementById('plex-status').innerHTML = '<span style="color: var(--warning);">⚠ Authenticated but missing server URL</span>';
            document.getElementById('plex-authed-status').classList.add('hidden');
        } else {
            document.getElementById('plex-status').innerHTML = '<span style="color: var(--warning);">⚠ Not configured</span>';
            document.getElementById('plex-authed-status').classList.add('hidden');
        }
    } catch (e) {
        console.error('Failed to load Plex config:', e);
    }
}

async function testAndSavePlex() {
    const url = document.getElementById('plex-url').value;
    const enabled = document.getElementById('plex-enabled').checked;
    const labelText = document.getElementById('plex-label').value;
    
    if (!url) {
        showToast('Please enter Plex server URL', 'error');
        return;
    }
    
    const btn = document.getElementById('plex-test-btn');
    btn.disabled = true;
    btn.textContent = 'Testing...';
    
    try {
        // Save config (preserves token from OAuth)
        const saveRes = await fetch('/api/plex/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, enabled, label_text: labelText })
        });
        
        if (!saveRes.ok) {
            const err = await saveRes.json();
            showToast(err.detail || 'Failed to save configuration', 'error');
            return;
        }
        
        // Test connection
        const testRes = await fetch('/api/plex/config/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!testRes.ok) {
            const err = await testRes.json();
            showToast(err.detail || 'Connection test failed', 'error');
        } else {
            showToast('Plex configured successfully', 'success');
        }
        
        loadPlexConfig();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test & Save';
    }
}

async function clearPlex() {
    if (!confirm('Clear Plex configuration?')) return;
    try {
        const res = await fetch('/api/plex/config', { method: 'DELETE' });
        if (res.ok) {
            showToast('Plex configuration cleared', 'success');
            document.getElementById('plex-url').value = '';
            document.getElementById('plex-enabled').checked = false;
            document.getElementById('plex-label').value = 'Cullarr - Pending Deletion';
            loadPlexConfig();
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// Weights
function updateWeightDisplay(id) {
    const val = document.getElementById(id).value;
    document.getElementById(`${id}-val`).textContent = `${val}%`;
    updateTotalWeight();
}

function updateTotalWeight() {
    const age = parseInt(document.getElementById('age-weight').value) || 0;
    const size = parseInt(document.getElementById('size-weight').value) || 0;
    const rating = parseInt(document.getElementById('rating-weight').value) || 0;
    const quality = parseInt(document.getElementById('quality-weight').value) || 0;
    const monitored = parseInt(document.getElementById('monitored-weight').value) || 0;
    const watched = parseInt(document.getElementById('watched-weight').value) || 0;
    const total = age + size + rating + quality + monitored + watched;
    
    document.getElementById('total-weight').textContent = total;
    const warning = document.getElementById('weight-warning');
    if (total !== 100) {
        warning.classList.remove('hidden');
    } else {
        warning.classList.add('hidden');
    }
}

async function loadWeights() {
    try {
        const res = await fetch('/api/settings/weights');
        const data = await res.json();
        
        document.getElementById('age-weight').value = data.age_weight || 25;
        document.getElementById('age-weight-val').textContent = `${data.age_weight || 25}%`;
        document.getElementById('size-weight').value = data.size_weight || 25;
        document.getElementById('size-weight-val').textContent = `${data.size_weight || 25}%`;
        document.getElementById('rating-weight').value = data.rating_weight || 15;
        document.getElementById('rating-weight-val').textContent = `${data.rating_weight || 15}%`;
        document.getElementById('quality-weight').value = data.quality_weight || 15;
        document.getElementById('quality-weight-val').textContent = `${data.quality_weight || 15}%`;
        document.getElementById('monitored-weight').value = data.monitored_weight || 10;
        document.getElementById('monitored-weight-val').textContent = `${data.monitored_weight || 10}%`;
        document.getElementById('watched-weight').value = data.watched_weight || 10;
        document.getElementById('watched-weight-val').textContent = `${data.watched_weight || 10}%`;
        document.getElementById('age-max-days').value = data.age_max_days || 365;
        document.getElementById('size-max-gb').value = data.size_max_gb || 100;
        
        updateTotalWeight();
    } catch (e) {
        console.error('Failed to load weights:', e);
    }
}

async function saveWeights() {
    const total = parseInt(document.getElementById('total-weight').textContent);
    if (total !== 100) {
        showToast('Weights must sum to 100%', 'error');
        return;
    }
    
    const payload = {
        age_weight: parseInt(document.getElementById('age-weight').value),
        size_weight: parseInt(document.getElementById('size-weight').value),
        rating_weight: parseInt(document.getElementById('rating-weight').value),
        quality_weight: parseInt(document.getElementById('quality-weight').value),
        monitored_weight: parseInt(document.getElementById('monitored-weight').value),
        watched_weight: parseInt(document.getElementById('watched-weight').value),
        age_max_days: parseInt(document.getElementById('age-max-days').value),
        size_max_gb: parseInt(document.getElementById('size-max-gb').value),
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

// Settings
async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        
        document.getElementById('cullarr-enabled').checked = data.enabled || false;
        document.getElementById('score-cron').value = data.score_cron || '0 3 * * 0';
        document.getElementById('cull-cron').value = data.cull_cron || '0 2 * * *';
        document.getElementById('max-queued').value = data.max_queued || 20;
        document.getElementById('delete-after-days').value = data.delete_after_days || 7;
        document.getElementById('protection-days').value = data.protection_days || 30;
        document.getElementById('collection-grouping').checked = data.collection_grouping || false;
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
        delete_after_days: parseInt(document.getElementById('delete-after-days').value),
        protection_days: parseInt(document.getElementById('protection-days').value),
        collection_grouping: document.getElementById('collection-grouping').checked,
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