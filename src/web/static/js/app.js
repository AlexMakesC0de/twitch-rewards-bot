/* ─────────────────────────────────────────────────────────────────
   Twitch Drops Bot  —  Frontend SPA  (vanilla JS)
   ───────────────────────────────────────────────────────────────── */

const App = (() => {

    // ── State ─────────────────────────────────────────────────────
    let currentPage = 'dashboard';
    let pollTimer = null;
    let activityTimer = null;
    let searchTimer = null;
    let authenticated = false;
    let historyData = null;
    let currentSort = 'date-desc';
    let soundEnabled = localStorage.getItem('claimSound') !== 'false';

    // ── API helpers ───────────────────────────────────────────────
    async function api(path, opts = {}) {
        try {
            const res = await fetch('/api' + path, {
                headers: { 'Content-Type': 'application/json', ...opts.headers },
                ...opts,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
            return data;
        } catch (err) {
            console.error(`API ${path}:`, err);
            throw err;
        }
    }

    const get  = (path) => api(path);
    const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });
    const del  = (path) => api(path, { method: 'DELETE' });

    // ── Toast ─────────────────────────────────────────────────────
    function toast(message, type = 'info') {
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        const icons = { success: '✓', error: '✗', info: 'ℹ' };
        el.innerHTML = `<span>${icons[type] || ''}</span><span>${message}</span>`;
        document.getElementById('toast-container').appendChild(el);
        setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 4000);
    }

    // ── Navigation ────────────────────────────────────────────────
    function navigate(page) {
        currentPage = page;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        const target = document.getElementById('page-' + page);
        if (target) target.classList.add('active');

        document.querySelectorAll('.nav-link').forEach(a => {
            a.classList.toggle('active', a.dataset.page === page);
        });

        window.location.hash = page;

        // Load page data
        switch (page) {
            case 'dashboard': refreshDashboard(); break;
            case 'games': loadGames(); break;
            case 'drops': loadWatchStatus(); break;
            case 'watch': loadWatchStatus(); loadWatchCampaigns(); break;
            case 'history': loadHistory(); break;
            case 'settings': loadSettings(); break;
        }
    }

    // ── Tour ──────────────────────────────────────────────────────
    const tourSteps = [
        {
            target: '#sidebar .nav-links',
            title: 'Navigation',
            text: 'Use the sidebar to switch between pages. Each page has its own purpose — Dashboard for an overview, Games to pick what to track, and so on.',
        },
        {
            target: '[data-page="settings"]',
            title: 'Settings — Log In First',
            text: 'Head to Settings to connect your Twitch account. Click "Login with Twitch" and enter the code shown on screen.',
            beforeShow: () => navigate('settings'),
        },
        {
            target: '[data-page="games"]',
            title: 'Add Your Games',
            text: 'Go to Games and search for any game you want to track drops for. Just click a result to add it.',
            beforeShow: () => navigate('games'),
        },
        {
            target: '[data-page="dashboard"]',
            title: 'Dashboard',
            text: 'The Dashboard shows your stats at a glance — tracked games, active campaigns, watch sessions, and claimed drops.',
            beforeShow: () => navigate('dashboard'),
        },
        {
            target: '[data-page="drops"]',
            title: 'Drops & Watch',
            text: 'The Drops page shows active campaigns. Auto-Watch is on by default — the bot will find streams and watch them for you automatically.',
        },
        {
            target: '#sidebar-theme-btn',
            title: 'Theme & Preferences',
            text: 'Click the moon icon to switch between dark and light mode. You can also enable a claim sound in Settings.',
        },
    ];

    let tourIndex = -1;

    function showTourWelcome() {
        document.getElementById('tour-welcome').style.display = '';
    }

    function skipTour() {
        document.getElementById('tour-welcome').style.display = 'none';
        localStorage.setItem('tourDone', '1');
    }

    function startTour() {
        document.getElementById('tour-welcome').style.display = 'none';
        tourIndex = -1;
        document.getElementById('tour-backdrop').style.display = '';
        document.getElementById('tour-highlight').style.display = '';
        document.getElementById('tour-tooltip').style.display = '';

        document.getElementById('tour-skip-btn').addEventListener('click', endTour);
        document.getElementById('tour-next-btn').addEventListener('click', nextTourStep);

        nextTourStep();
    }

    function nextTourStep() {
        tourIndex++;
        if (tourIndex >= tourSteps.length) { endTour(); return; }

        const step = tourSteps[tourIndex];
        if (step.beforeShow) step.beforeShow();

        // Small delay so page navigations settle
        setTimeout(() => {
            const el = document.querySelector(step.target);
            if (!el) { nextTourStep(); return; }

            const rect = el.getBoundingClientRect();
            const pad = 6;

            const highlight = document.getElementById('tour-highlight');
            highlight.style.top = (rect.top - pad) + 'px';
            highlight.style.left = (rect.left - pad) + 'px';
            highlight.style.width = (rect.width + pad * 2) + 'px';
            highlight.style.height = (rect.height + pad * 2) + 'px';

            document.getElementById('tour-title').textContent = step.title;
            document.getElementById('tour-text').textContent = step.text;
            document.getElementById('tour-step-indicator').textContent =
                (tourIndex + 1) + ' of ' + tourSteps.length;

            const isLast = tourIndex === tourSteps.length - 1;
            document.getElementById('tour-next-btn').textContent = isLast ? 'Finish' : 'Next';

            // Position tooltip
            const tooltip = document.getElementById('tour-tooltip');
            const tipW = 320;
            let tipTop = rect.bottom + pad + 12;
            let tipLeft = rect.left;

            // Keep within viewport
            if (tipLeft + tipW > window.innerWidth - 16) tipLeft = window.innerWidth - tipW - 16;
            if (tipLeft < 16) tipLeft = 16;
            if (tipTop + 200 > window.innerHeight) tipTop = rect.top - pad - 200;

            tooltip.style.top = tipTop + 'px';
            tooltip.style.left = tipLeft + 'px';
        }, 80);
    }

    function endTour() {
        document.getElementById('tour-backdrop').style.display = 'none';
        document.getElementById('tour-highlight').style.display = 'none';
        document.getElementById('tour-tooltip').style.display = 'none';
        localStorage.setItem('tourDone', '1');
        tourIndex = -1;
    }

    // ── Init ──────────────────────────────────────────────────────
    function init() {
        // Sidebar nav
        document.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                navigate(link.dataset.page);
            });
        });

        // User badge click → settings
        document.getElementById('auth-badge').addEventListener('click', () => navigate('settings'));

        // Game search — real-time as user types
        const searchInput = document.getElementById('game-search-input');
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimer);
            const q = e.target.value.trim();
            if (q.length < 1) { hideSearchResults(); return; }
            // Show loading indicator immediately
            const container = document.getElementById('game-search-results');
            container.innerHTML = '<div style="padding:14px;color:var(--text-dim);font-size:13px;display:flex;align-items:center;gap:8px;"><div class="spinner" style="width:16px;height:16px;margin:0;border-width:2px;"></div>Searching...</div>';
            container.classList.add('visible');
            searchTimer = setTimeout(() => searchGames(q), 250);
        });

        // Click outside search results to close
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.search-box')) hideSearchResults();
        });

        // Apply saved theme
        applyTheme(localStorage.getItem('theme') || 'dark');

        // Sync sound toggle
        const soundToggle = document.getElementById('settings-sound-toggle');
        if (soundToggle) soundToggle.checked = soundEnabled;

        // Read initial hash
        const hash = window.location.hash.replace('#', '') || 'dashboard';

        // Check auth first, then navigate so dashboard data loads correctly
        checkAuth().then(() => {
            navigate(hash);
            // Show tour welcome on first visit
            if (!localStorage.getItem('tourDone')) {
                setTimeout(showTourWelcome, 600);
            }
        });

        // Start polling
        startPolling();
    }

    // ── Polling ───────────────────────────────────────────────────
    function startPolling() {
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(() => {
            if (currentPage === 'dashboard') refreshDashboard();
            if (currentPage === 'watch' || currentPage === 'drops') loadWatchStatus();
            if (currentPage === 'watch') loadWatchCampaigns();
            updateNavBadges();
        }, 15000);

        // Activity log polls more frequently
        if (activityTimer) clearInterval(activityTimer);
        activityTimer = setInterval(() => {
            if (currentPage === 'dashboard') refreshActivityLog();
        }, 5000);
    }

    // ── Auth ──────────────────────────────────────────────────────
    async function checkAuth() {
        try {
            const data = await get('/auth/status');
            authenticated = data.authenticated;
            updateAuthUI(data);
        } catch {
            authenticated = false;
            updateAuthUI({ authenticated: false });
        }
    }

    function updateAuthUI(data) {
        const nameEl = document.getElementById('user-name');
        const statusEl = document.getElementById('user-status');
        const avatarEl = document.getElementById('user-avatar');

        if (data.authenticated && data.username) {
            nameEl.textContent = data.username;
            statusEl.textContent = 'Connected';
            statusEl.style.color = '#00c853';
            avatarEl.textContent = data.username[0].toUpperCase();

            document.getElementById('auth-logged-out').style.display = 'none';
            document.getElementById('auth-logged-in').style.display = 'block';
            document.getElementById('settings-username').textContent = data.username;
            document.getElementById('settings-user-id').textContent = data.user_id || '—';
            document.getElementById('settings-avatar').textContent = data.username[0].toUpperCase();
        } else {
            nameEl.textContent = 'Not logged in';
            statusEl.textContent = 'Click to login';
            statusEl.style.color = '';
            avatarEl.textContent = '?';

            document.getElementById('auth-logged-out').style.display = 'block';
            document.getElementById('auth-logged-in').style.display = 'none';
        }
    }

    let loginPollTimer = null;

    async function startLogin() {
        try {
            document.getElementById('login-card').style.display = 'block';
            document.getElementById('device-code-prompt').style.display = 'block';
            document.getElementById('device-code-display').style.display = 'none';

            const data = await post('/auth/device-code', {});
            if (data.error) {
                toast('Login failed: ' + data.error, 'error');
                document.getElementById('login-card').style.display = 'none';
                return;
            }

            // Show the device code
            document.getElementById('device-code-prompt').style.display = 'none';
            document.getElementById('device-code-display').style.display = 'block';
            document.getElementById('device-code-value').textContent = data.user_code;
            const link = document.getElementById('device-code-link');
            link.href = data.verification_uri;
            link.textContent = data.verification_uri.replace('https://', '').replace('http://', '');

            // Open the activation page
            window.open(data.verification_uri, '_blank');

            // Poll for login completion
            pollLoginStatus();
        } catch (err) {
            toast('Failed to start login: ' + err.message, 'error');
            document.getElementById('login-card').style.display = 'none';
        }
    }

    function pollLoginStatus() {
        if (loginPollTimer) clearInterval(loginPollTimer);
        loginPollTimer = setInterval(async () => {
            try {
                const data = await get('/auth/status');
                if (data.authenticated && data.username) {
                    clearInterval(loginPollTimer);
                    loginPollTimer = null;
                    authenticated = true;
                    updateAuthUI(data);
                    document.getElementById('login-card').style.display = 'none';
                    toast('Logged in as ' + data.username + '!', 'success');
                    refreshDashboard();
                }
            } catch { /* silent */ }
        }, 2000);
        // Stop polling after 2 minutes
        setTimeout(() => {
            if (loginPollTimer) { clearInterval(loginPollTimer); loginPollTimer = null; }
        }, 120000);
    }

    function cancelLogin() {
        if (loginPollTimer) { clearInterval(loginPollTimer); loginPollTimer = null; }
        document.getElementById('login-card').style.display = 'none';
    }

    async function logout() {
        try {
            await post('/auth/logout');
            authenticated = false;
            updateAuthUI({ authenticated: false });
            toast('Logged out', 'info');
        } catch (err) {
            toast('Logout failed: ' + err.message, 'error');
        }
    }

    // ── Dashboard ─────────────────────────────────────────────────
    async function refreshDashboard() {
        try {
            const status = await get('/bot/status');
            document.getElementById('stat-games').textContent = status.tracked_games || 0;
            document.getElementById('stat-watching').textContent = status.active_watches || 0;

            // Sync auto-watch toggle state
            syncAutoWatchToggles(status.auto_watch !== false);

            // Update nav badges
            updateNavBadges(status);
        } catch { /* silent */ }

        // Load inventory for progress
        if (authenticated) {
            try {
                const inv = await get('/drops/inventory');
                renderDashboardProgress(inv);
                document.getElementById('stat-claimed').textContent = inv.completed?.length || 0;
            } catch { /* silent */ }
        }

        refreshActivityLog();
    }

    function renderDashboardProgress(inv) {
        const container = document.getElementById('dashboard-progress');
        const items = inv.in_progress || [];

        if (!items.length) {
            container.innerHTML = '<div class="empty-state"><p>No drops in progress</p></div>';
            return;
        }

        container.innerHTML = items.map(item => {
            const remaining = item.required_minutes - item.current_minutes;
            const eta = formatETA(remaining);
            return `
            <div class="drop-item">
                ${item.benefits?.[0]?.image_url
                    ? `<img class="drop-icon" src="${item.benefits[0].image_url}" alt="">`
                    : `<div class="drop-icon"></div>`}
                <div class="drop-info">
                    <div class="drop-name">${esc(item.drop_name)}</div>
                    <div class="drop-progress-text">
                        ${esc(item.game_name)} — ${item.current_minutes}/${item.required_minutes} min
                        (${item.progress_percent.toFixed(0)}%)
                    </div>
                    ${eta ? `<div class="eta-text">${eta} remaining</div>` : ''}
                    <div class="drop-progress-bar progress-bar-wrap">
                        <div class="progress-bar-fill ${item.progress_percent >= 100 ? 'complete' : ''}"
                             style="width:${Math.min(100, item.progress_percent)}%"></div>
                    </div>
                </div>
            </div>`;
        }).join('');
    }

    async function refreshDrops() {
        await refreshDashboard();
        toast('Dashboard refreshed', 'info');
    }

    // ── Games ─────────────────────────────────────────────────────
    async function loadGames() {
        try {
            const data = await get('/games');
            renderGames(data.games || []);
        } catch (err) {
            toast('Failed to load games: ' + err.message, 'error');
        }
    }

    function renderGames(games) {
        const container = document.getElementById('games-list');
        document.getElementById('game-count').textContent = games.length;
        document.getElementById('stat-games').textContent = games.length;

        if (!games.length) {
            container.innerHTML = '<div class="empty-state"><p>No games tracked yet. Search above to add one.</p></div>';
            return;
        }

        container.innerHTML = games.map((g, idx) => `
            <div class="game-card" draggable="true" data-game-name="${esc(g.game_name)}" data-idx="${idx}">
                ${g.box_art_url
                    ? `<img class="game-card-art" src="${g.box_art_url}" alt="${esc(g.display_name || g.game_name)}">`
                    : `<div class="game-card-art" style="display:flex;align-items:center;justify-content:center;color:var(--text-dim);font-size:12px;">${esc(g.display_name || g.game_name)}</div>`}
                <div class="game-card-info">
                    <div class="game-card-name">${esc(g.display_name || g.game_name)}</div>
                    <div class="game-card-priority">#${idx + 1} priority</div>
                </div>
                <button class="game-card-remove" onclick="App.removeGame('${esc(g.game_name)}')" title="Remove">✕</button>
                <div class="game-card-drag-handle" title="Drag to reorder">⠿</div>
            </div>
        `).join('');

        // Attach drag-and-drop handlers
        initDragAndDrop(container);
    }

    function initDragAndDrop(container) {
        let draggedEl = null;

        container.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.game-card');
            if (!card) return;
            draggedEl = card;
            card.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        });

        container.addEventListener('dragend', (e) => {
            if (draggedEl) draggedEl.classList.remove('dragging');
            draggedEl = null;
            container.querySelectorAll('.game-card').forEach(c => c.classList.remove('drag-over'));
        });

        container.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            const target = e.target.closest('.game-card');
            if (!target || target === draggedEl) return;
            container.querySelectorAll('.game-card').forEach(c => c.classList.remove('drag-over'));
            target.classList.add('drag-over');
        });

        container.addEventListener('dragleave', (e) => {
            const target = e.target.closest('.game-card');
            if (target) target.classList.remove('drag-over');
        });

        container.addEventListener('drop', async (e) => {
            e.preventDefault();
            const target = e.target.closest('.game-card');
            if (!target || !draggedEl || target === draggedEl) return;

            // Reorder DOM
            const cards = [...container.querySelectorAll('.game-card')];
            const fromIdx = cards.indexOf(draggedEl);
            const toIdx = cards.indexOf(target);

            if (fromIdx < toIdx) {
                target.after(draggedEl);
            } else {
                target.before(draggedEl);
            }

            // Update priority labels
            const reordered = [...container.querySelectorAll('.game-card')];
            const order = reordered.map((c, i) => {
                const label = c.querySelector('.game-card-priority');
                if (label) label.textContent = `#${i + 1} priority`;
                return c.dataset.gameName;
            });

            // Save to server
            try {
                await post('/games/reorder', { order });
                toast('Priority updated', 'success');
            } catch (err) {
                toast('Failed to save order: ' + err.message, 'error');
                loadGames(); // reload on failure
            }
        });
    }

    async function searchGames(query) {
        if (!authenticated) {
            toast('Please login first', 'error');
            return;
        }

        try {
            const data = await get('/games/search?q=' + encodeURIComponent(query));
            renderSearchResults(data.results || []);
        } catch (err) {
            toast('Search failed: ' + err.message, 'error');
        }
    }

    function renderSearchResults(results) {
        const container = document.getElementById('game-search-results');
        if (!results.length) {
            container.innerHTML = '<div style="padding:14px;color:var(--text-dim);font-size:13px;">No games found</div>';
            container.classList.add('visible');
            return;
        }

        container.innerHTML = results.map(g => `
            <div class="search-result-item" onclick="App.addGame('${esc(g.name)}', '${esc(g.id)}', '${esc(g.box_art_url || '')}')">
                ${g.box_art_url
                    ? `<img src="${g.box_art_url}" alt="">`
                    : `<div style="width:40px;height:53px;background:var(--bg-input);border-radius:4px;"></div>`}
                <span class="search-result-name">${esc(g.name)}</span>
            </div>
        `).join('');
        container.classList.add('visible');
    }

    function hideSearchResults() {
        document.getElementById('game-search-results').classList.remove('visible');
    }

    async function addGame(name, id, boxArt) {
        try {
            await post('/games', { name, id, box_art_url: boxArt });
            toast(`Now tracking: ${name}`, 'success');
            hideSearchResults();
            document.getElementById('game-search-input').value = '';
            loadGames();
        } catch (err) {
            toast('Failed to add game: ' + err.message, 'error');
        }
    }

    async function removeGame(name) {
        try {
            await del('/games/' + encodeURIComponent(name));
            toast(`Removed: ${name}`, 'info');
            loadGames();
        } catch (err) {
            toast('Failed to remove game: ' + err.message, 'error');
        }
    }

    // ── Drops ─────────────────────────────────────────────────────
    async function checkDrops() {
        if (!authenticated) { toast('Please login first', 'error'); return; }

        const container = document.getElementById('campaigns-list');
        container.innerHTML = '<div class="empty-state"><div class="spinner"></div><p>Checking for drops...</p></div>';

        try {
            const data = await get('/drops');
            document.getElementById('stat-campaigns').textContent = data.campaigns?.length || 0;
            renderCampaigns(data.campaigns || []);

            if (data.claimable_count > 0) {
                toast(`${data.claimable_count} drop(s) ready to claim!`, 'success');
            }
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><p style="color:var(--red);">Error: ${esc(err.message)}</p></div>`;
        }
    }

    function renderCampaigns(campaigns) {
        const container = document.getElementById('campaigns-list');

        if (!campaigns.length) {
            container.innerHTML = '<div class="empty-state"><p>No active campaigns for your tracked games</p></div>';
            return;
        }

        container.innerHTML = campaigns.map(c => {
            const status = getCampaignStatus(c);
            const dropsHtml = (c.drops || []).map(d => {
                const pct = d.progress_percent || 0;
                const benefitImg = d.benefits?.[0]?.image_url;
                const remaining = d.required_minutes - (d.current_minutes || 0);
                const eta = formatETA(remaining);
                const claimable = pct >= 100 && !d.is_claimed;
                return `
                    <div class="drop-item">
                        ${benefitImg
                            ? `<img class="drop-icon" src="${benefitImg}" alt="">`
                            : `<div class="drop-icon"></div>`}
                        <div class="drop-info">
                            <div class="drop-name">${esc(d.name)}</div>
                            <div class="drop-progress-text">
                                ${d.current_minutes || 0}/${d.required_minutes} min
                                ${d.is_claimed ? '<span style="color:var(--green);">✓ Claimed</span>' : `(${pct.toFixed(0)}%)`}
                            </div>
                            ${!d.is_claimed && eta ? `<div class="eta-text">${eta} remaining</div>` : ''}
                            <div class="drop-progress-bar progress-bar-wrap">
                                <div class="progress-bar-fill ${pct >= 100 ? 'complete' : ''}"
                                     style="width:${Math.min(100, pct)}%"></div>
                            </div>
                        </div>
                        ${claimable ? `<div class="drop-actions"><button class="btn btn-sm btn-green" onclick="App.claimSingleDrop('${esc(d.id)}')">Claim</button></div>` : ''}
                    </div>
                `;
            }).join('');

            const start = c.start_at ? new Date(c.start_at).toLocaleDateString() : '?';
            const end = c.end_at ? new Date(c.end_at).toLocaleDateString() : '?';

            return `
                <div class="campaign-card">
                    <div class="campaign-header">
                        ${c.game_box_art_url
                            ? `<img class="campaign-art" src="${c.game_box_art_url}" alt="">`
                            : ''}
                        <div>
                            <div class="campaign-title">${esc(c.name)}</div>
                            <div class="campaign-game">${esc(c.game_name)}</div>
                            <div class="campaign-meta">
                                <span class="status-pill ${status.cls}">${status.label}</span>
                                <span class="campaign-dates">${start} — ${end}</span>
                            </div>
                        </div>
                    </div>
                    ${dropsHtml}
                </div>
            `;
        }).join('');
    }

    async function loadInventory() {
        if (!authenticated) { toast('Please login first', 'error'); return; }

        const container = document.getElementById('inventory-list');
        container.innerHTML = '<div class="empty-state"><div class="spinner"></div><p>Loading inventory...</p></div>';

        try {
            const data = await get('/drops/inventory');
            renderInventory(data);
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><p style="color:var(--red);">${esc(err.message)}</p></div>`;
        }
    }

    function renderInventory(inv) {
        const container = document.getElementById('inventory-list');
        const items = [...(inv.in_progress || []), ...(inv.completed || [])];

        if (!items.length) {
            container.innerHTML = '<div class="empty-state"><p>Inventory is empty</p></div>';
            return;
        }

        // In-progress
        let html = '';
        for (const item of (inv.in_progress || [])) {
            const pct = item.progress_percent || 0;
            const img = item.benefits?.[0]?.image_url;
            html += `
                <div class="drop-item">
                    ${img ? `<img class="drop-icon" src="${img}" alt="">` : `<div class="drop-icon"></div>`}
                    <div class="drop-info">
                        <div class="drop-name">${esc(item.drop_name)}</div>
                        <div class="drop-progress-text">
                            ${esc(item.game_name)} — ${item.current_minutes}/${item.required_minutes} min
                        </div>
                        <div class="drop-progress-bar progress-bar-wrap">
                            <div class="progress-bar-fill ${pct >= 100 ? 'complete' : ''}"
                                 style="width:${Math.min(100, pct)}%"></div>
                        </div>
                    </div>
                </div>`;
        }

        // Completed
        for (const item of (inv.completed || [])) {
            html += `
                <div class="drop-item">
                    ${item.image_url ? `<img class="drop-icon" src="${item.image_url}" alt="">` : `<div class="drop-icon"></div>`}
                    <div class="drop-info">
                        <div class="drop-name">${esc(item.name)}</div>
                        <div class="drop-progress-text" style="color:var(--green);">
                            ✓ ${esc(item.game)} · ×${item.count}
                        </div>
                    </div>
                </div>`;
        }

        container.innerHTML = html;
    }

    async function claimDrops() {
        if (!authenticated) { toast('Please login first', 'error'); return; }

        try {
            const data = await post('/drops/claim');
            const count = data.claimed?.length || 0;
            toast(count ? `Claimed ${count} drop(s)!` : 'No drops to claim', count ? 'success' : 'info');
            if (count) playClaimSound();
            refreshDashboard();
        } catch (err) {
            toast('Claim failed: ' + err.message, 'error');
        }
    }

    // ── Watch ─────────────────────────────────────────────────────
    async function loadWatchStatus() {
        try {
            const data = await get('/watch/status');
            const sessions = data.sessions || [];
            renderWatchSessions(sessions);
            renderDropsWatchSessions(sessions);
        } catch { /* silent */ }
    }

    function renderWatchSessions(sessions) {
        const container = document.getElementById('watch-sessions');
        document.getElementById('watch-count').textContent = sessions.length;
        document.getElementById('stat-watching').textContent = sessions.length;

        if (!sessions.length) {
            container.innerHTML = '<div class="empty-state"><p>No active watch sessions. Auto-watch will start when drops are available.</p></div>';
            return;
        }

        container.innerHTML = sessions.map(s => {
            const channel = s.channel || s.channel_name || s.channel_login;
            const eta = formatETA(s.remaining_minutes);
            const dropId = s.drop_id || '';
            return `
            <div class="watch-card">
                <div class="watch-status-dot"></div>
                <div class="watch-info">
                    <div class="watch-channel">
                        <a href="https://www.twitch.tv/${esc(s.channel_login || channel)}" target="_blank" rel="noopener">${esc(channel)}</a>
                    </div>
                    <div class="watch-game">${esc(s.game || s.game_name || '—')}</div>
                    <div class="watch-time">
                        ${s.minutes_watched || 0}/${s.required_minutes || '?'} min
                        ${s.drop_name ? ` · ${esc(s.drop_name)}` : ''}
                        ${s.progress_percent != null ? ` (${s.progress_percent.toFixed(0)}%)` : ''}
                    </div>
                    ${eta ? `<div class="watch-eta">${eta} remaining</div>` : ''}
                    ${s.required_minutes ? `
                    <div class="drop-progress-bar progress-bar-wrap" style="margin-top:6px;">
                        <div class="progress-bar-fill ${(s.progress_percent || 0) >= 100 ? 'complete' : ''}"
                             style="width:${Math.min(100, s.progress_percent || 0)}%"></div>
                    </div>` : ''}
                </div>
                <div class="watch-actions">
                    <button class="btn btn-sm btn-red" onclick="App.stopSession('${esc(dropId)}')">Stop</button>
                </div>
            </div>`;
        }).join('');
    }

    function renderDropsWatchSessions(sessions) {
        const container = document.getElementById('drops-watch-sessions');
        if (!container) return;

        if (!sessions.length) {
            container.innerHTML = '<div class="empty-state"><p>Auto-watch will start watching when drops are available</p></div>';
            return;
        }

        container.innerHTML = sessions.map(s => {
            const channel = s.channel || s.channel_name || s.channel_login;
            const eta = formatETA(s.remaining_minutes);
            return `
            <div class="watch-card">
                <div class="watch-status-dot"></div>
                <div class="watch-info">
                    <div class="watch-channel">
                        <a href="https://www.twitch.tv/${esc(s.channel_login || channel)}" target="_blank" rel="noopener">${esc(channel)}</a>
                    </div>
                    <div class="watch-game">${esc(s.game || s.game_name || '—')}</div>
                    <div class="watch-time">
                        ${s.minutes_watched || 0}/${s.required_minutes || '?'} min
                        ${s.drop_name ? ` · ${esc(s.drop_name)}` : ''}
                        ${s.progress_percent != null ? ` (${s.progress_percent.toFixed(0)}%)` : ''}
                    </div>
                    ${eta ? `<div class="watch-eta">${eta} remaining</div>` : ''}
                    ${s.required_minutes ? `
                    <div class="drop-progress-bar progress-bar-wrap" style="margin-top:6px;">
                        <div class="progress-bar-fill ${(s.progress_percent || 0) >= 100 ? 'complete' : ''}"
                             style="width:${Math.min(100, s.progress_percent || 0)}%"></div>
                    </div>` : ''}
                </div>
            </div>`;
        }).join('');
    }

    async function loadWatchCampaigns() {
        if (!authenticated) return;
        const container = document.getElementById('watch-campaigns');
        if (!container) return;

        container.innerHTML = '<div class="empty-state"><div class="spinner"></div><p>Loading campaigns...</p></div>';
        try {
            const data = await get('/drops');
            const campaigns = data.campaigns || [];
            if (!campaigns.length) {
                container.innerHTML = '<div class="empty-state"><p>No active campaigns for your tracked games</p></div>';
                return;
            }
            container.innerHTML = campaigns.map(c => {
                const status = getCampaignStatus(c);
                const dropsHtml = (c.drops || []).map(d => {
                    const pct = d.progress_percent || 0;
                    const benefitImg = d.benefits?.[0]?.image_url;
                    const remaining = d.required_minutes - (d.current_minutes || 0);
                    const eta = formatETA(remaining);
                    return `
                        <div class="drop-item">
                            ${benefitImg
                                ? `<img class="drop-icon" src="${benefitImg}" alt="">`
                                : `<div class="drop-icon"></div>`}
                            <div class="drop-info">
                                <div class="drop-name">${esc(d.name)}</div>
                                <div class="drop-progress-text">
                                    ${d.current_minutes || 0}/${d.required_minutes} min
                                    ${d.is_claimed ? '<span style="color:var(--green);">✓ Claimed</span>' : `(${pct.toFixed(0)}%)`}
                                </div>
                                ${!d.is_claimed && eta ? `<div class="eta-text">${eta} remaining</div>` : ''}
                                <div class="drop-progress-bar progress-bar-wrap">
                                    <div class="progress-bar-fill ${pct >= 100 ? 'complete' : ''}"
                                         style="width:${Math.min(100, pct)}%"></div>
                                </div>
                            </div>
                        </div>`;
                }).join('');

                const start = c.start_at ? new Date(c.start_at).toLocaleDateString() : '?';
                const end = c.end_at ? new Date(c.end_at).toLocaleDateString() : '?';

                return `
                    <div class="campaign-card">
                        <div class="campaign-header">
                            ${c.game_box_art_url
                                ? `<img class="campaign-art" src="${c.game_box_art_url}" alt="">`
                                : ''}
                            <div>
                                <div class="campaign-title">${esc(c.name)}</div>
                                <div class="campaign-game">${esc(c.game_name)}</div>
                                <div class="campaign-meta">
                                    <span class="status-pill ${status.cls}">${status.label}</span>
                                    <span class="campaign-dates">${start} — ${end}</span>
                                </div>
                            </div>
                        </div>
                        ${dropsHtml}
                    </div>`;
            }).join('');
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><p style="color:var(--red);">Error: ${esc(err.message)}</p></div>`;
        }
    }

    async function toggleAutoWatch(enabled) {
        try {
            const data = await post('/watch/auto', { enabled });
            syncAutoWatchToggles(data.enabled);
            toast(data.enabled ? 'Auto-watch enabled' : 'Auto-watch disabled', 'info');
            if (!data.enabled) {
                await post('/watch/stop');
                loadWatchStatus();
            }
        } catch (err) {
            toast('Failed to toggle auto-watch: ' + err.message, 'error');
        }
    }

    function syncAutoWatchToggles(enabled) {
        const toggles = ['drops-auto-watch-toggle', 'watch-auto-watch-toggle'];
        toggles.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.checked = enabled;
        });
    }

    async function stopWatch() {
        try {
            await post('/watch/stop');
            toast('Stopped all watch sessions', 'info');
            loadWatchStatus();
        } catch (err) {
            toast('Stop failed: ' + err.message, 'error');
        }
    }

    async function stopSession(dropId) {
        if (!dropId) return;
        try {
            await post('/watch/stop/' + encodeURIComponent(dropId));
            toast('Session stopped', 'info');
            loadWatchStatus();
        } catch (err) {
            toast('Stop failed: ' + err.message, 'error');
        }
    }

    async function claimSingleDrop(dropId) {
        if (!dropId) return;
        try {
            await post('/drops/claim/' + encodeURIComponent(dropId));
            toast('Drop claimed!', 'success');
            playClaimSound();
            checkDrops();
        } catch (err) {
            toast('Claim failed: ' + err.message, 'error');
        }
    }

    // ── History ───────────────────────────────────────────────────
    async function loadHistory() {
        const container = document.getElementById('history-list');
        container.innerHTML = '<div class="empty-state"><div class="spinner"></div><p>Loading history...</p></div>';
        try {
            const data = await get('/history');
            historyData = data;
            renderHistory(data, currentSort);
            if (!(data.drops?.length || data.watches?.length)) {
                toast('No history found', 'info');
            }
        } catch (err) {
            container.innerHTML = `<div class="empty-state"><p style="color:var(--red);">Error: ${esc(err.message)}</p></div>`;
            toast('Failed to load history', 'error');
        }
    }

    function sortHistory(sortValue) {
        currentSort = sortValue;
        if (historyData) renderHistory(historyData, sortValue);
    }

    function renderHistory(data, sort = 'date-desc') {
        const container = document.getElementById('history-list');
        let drops = [...(data.drops || [])];
        const watches = [...(data.watches || [])];

        if (!drops.length && !watches.length) {
            container.innerHTML = '<div class="empty-state"><p>No history yet</p></div>';
            return;
        }

        // Sort drops
        if (sort === 'date-asc') {
            drops.sort((a, b) => new Date(a.updated_at || 0) - new Date(b.updated_at || 0));
        } else if (sort === 'game') {
            drops.sort((a, b) => (a.game_name || '').localeCompare(b.game_name || ''));
        } else {
            drops.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
        }

        let html = '';

        if (drops.length) {
            // Desktop table
            html += `
                <h3 style="font-size:14px;margin-bottom:10px;color:var(--text-muted);">Drops</h3>
                <table class="history-table">
                    <thead><tr>
                        <th>Drop</th><th>Game</th><th>Status</th><th>Date</th>
                    </tr></thead>
                    <tbody>
                        ${drops.map(d => `
                            <tr>
                                <td>${esc(d.benefit_name || d.name || '—')}</td>
                                <td>${esc(d.game_name || '—')}</td>
                                <td>${d.is_claimed ? '<span style="color:var(--green);">Claimed</span>' :
                                      d.is_complete ? '<span style="color:var(--orange);">Ready</span>' :
                                      'In Progress'}</td>
                                <td title="${esc(d.updated_at || '')}">${relativeTime(d.updated_at)}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;

            // Mobile cards
            html += `<div class="history-card">
                <h3 style="font-size:14px;margin-bottom:10px;color:var(--text-muted);">Drops</h3>
                ${drops.map(d => `
                    <div class="history-card-item">
                        <div class="hc-row"><strong>${esc(d.benefit_name || d.name || '—')}</strong></div>
                        <div class="hc-row"><span class="hc-label">Game</span><span>${esc(d.game_name || '—')}</span></div>
                        <div class="hc-row"><span class="hc-label">Status</span><span>${d.is_claimed ? '<span style="color:var(--green);">Claimed</span>' :
                              d.is_complete ? '<span style="color:var(--orange);">Ready</span>' : 'In Progress'}</span></div>
                        <div class="hc-row"><span class="hc-label">Date</span><span title="${esc(d.updated_at || '')}">${relativeTime(d.updated_at)}</span></div>
                    </div>
                `).join('')}
            </div>`;
        }

        if (watches.length) {
            html += `
                <h3 style="font-size:14px;margin:20px 0 10px;color:var(--text-muted);">Watch Sessions</h3>
                <table class="history-table">
                    <thead><tr>
                        <th>Channel</th><th>Game</th><th>Duration</th><th>Started</th><th>Ended</th>
                    </tr></thead>
                    <tbody>
                        ${watches.map(w => `
                            <tr>
                                <td>${esc(w.channel_name || w.channel_login)}</td>
                                <td>${esc(w.game_name || '—')}</td>
                                <td>${w.minutes_watched || 0} min</td>
                                <td title="${esc(w.started_at || '')}">${relativeTime(w.started_at)}</td>
                                <td>${w.ended_at ? `<span title="${esc(w.ended_at)}">${relativeTime(w.ended_at)}</span>` : '<span style="color:var(--green);">Active</span>'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>`;

            // Mobile cards for watches
            html += `<div class="history-card">
                <h3 style="font-size:14px;margin:20px 0 10px;color:var(--text-muted);">Watch Sessions</h3>
                ${watches.map(w => `
                    <div class="history-card-item">
                        <div class="hc-row"><strong>${esc(w.channel_name || w.channel_login)}</strong></div>
                        <div class="hc-row"><span class="hc-label">Game</span><span>${esc(w.game_name || '—')}</span></div>
                        <div class="hc-row"><span class="hc-label">Duration</span><span>${w.minutes_watched || 0} min</span></div>
                        <div class="hc-row"><span class="hc-label">Started</span><span>${relativeTime(w.started_at)}</span></div>
                        <div class="hc-row"><span class="hc-label">Ended</span><span>${w.ended_at ? relativeTime(w.ended_at) : '<span style="color:var(--green);">Active</span>'}</span></div>
                    </div>
                `).join('')}
            </div>`;
        }

        container.innerHTML = html;
    }

    // ── Settings ──────────────────────────────────────────────────
    async function loadSettings() {
        await checkAuth();

        // Sync theme toggle
        const themeToggle = document.getElementById('settings-theme-toggle');
        if (themeToggle) themeToggle.checked = (document.documentElement.getAttribute('data-theme') === 'light');

        // Sync sound toggle
        const soundToggle = document.getElementById('settings-sound-toggle');
        if (soundToggle) soundToggle.checked = soundEnabled;

        try {
            const cfg = await get('/notifications/config');
            if (cfg.discord?.config?.webhook_url) {
                document.getElementById('discord-webhook-input').value = cfg.discord.config.webhook_url;
            }
            if (cfg.email?.config) {
                const ec = cfg.email.config;
                if (ec.smtp_host) document.getElementById('email-smtp-host').value = ec.smtp_host;
                if (ec.smtp_port) document.getElementById('email-smtp-port').value = ec.smtp_port;
                if (ec.sender_email) document.getElementById('email-sender').value = ec.sender_email;
                if (ec.recipient_email) document.getElementById('email-recipient').value = ec.recipient_email;
                if (ec.has_password) document.getElementById('email-password').placeholder = '(saved)';
            }
        } catch { /* silent */ }
    }

    async function saveDiscord() {
        const url = document.getElementById('discord-webhook-input').value.trim();
        if (!url) { toast('Enter a webhook URL', 'error'); return; }

        try {
            await post('/notifications/discord', { webhook_url: url });
            toast('Discord webhook saved!', 'success');
        } catch (err) {
            toast('Failed: ' + err.message, 'error');
        }
    }

    async function saveEmail() {
        const smtp_host = document.getElementById('email-smtp-host').value.trim();
        const smtp_port = parseInt(document.getElementById('email-smtp-port').value.trim()) || 587;
        const sender_email = document.getElementById('email-sender').value.trim();
        const password = document.getElementById('email-password').value;
        const recipient_email = document.getElementById('email-recipient').value.trim();

        if (!sender_email || !recipient_email) {
            toast('Sender and recipient emails are required', 'error');
            return;
        }

        try {
            await post('/notifications/email', {
                smtp_host, smtp_port, sender_email, password, recipient_email,
            });
            toast('Email settings saved!', 'success');
            document.getElementById('email-password').value = '';
            document.getElementById('email-password').placeholder = '(saved)';
        } catch (err) {
            toast('Failed: ' + err.message, 'error');
        }
    }

    async function testNotify() {
        try {
            const data = await post('/notifications/test');
            toast('Test notification sent!', 'success');
        } catch (err) {
            toast(err.message || 'No channels configured', 'error');
        }
    }

    // ── Utilities ─────────────────────────────────────────────────
    function esc(s) {
        if (!s) return '';
        const el = document.createElement('span');
        el.textContent = String(s);
        return el.innerHTML;
    }

    function formatETA(remainingMinutes) {
        if (!remainingMinutes || remainingMinutes <= 0) return '';
        if (remainingMinutes < 1) return '< 1 min';
        const h = Math.floor(remainingMinutes / 60);
        const m = Math.round(remainingMinutes % 60);
        if (h > 0) return `~${h}h ${m}m`;
        return `~${m}m`;
    }

    function relativeTime(dateStr) {
        if (!dateStr) return '—';
        const date = new Date(dateStr);
        const now = new Date();
        const diff = (now - date) / 1000;

        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        return date.toLocaleDateString();
    }

    function formatTimestamp(ts) {
        const d = new Date(ts * 1000);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return d.toLocaleTimeString();
    }

    function getCampaignStatus(c) {
        const now = new Date();
        const end = c.end_at ? new Date(c.end_at) : null;
        const start = c.start_at ? new Date(c.start_at) : null;

        if (start && now < start) return { label: 'Upcoming', cls: 'upcoming' };
        if (end && (end - now) < 86400000) return { label: 'Ending Soon', cls: 'ending' };
        if (end && now > end) return { label: 'Expired', cls: 'expired' };
        return { label: 'Active', cls: 'active' };
    }

    // ── Activity Log ────────────────────────────────────────────
    async function refreshActivityLog() {
        try {
            const data = await get('/activity/log');
            renderActivityLog(data.events || []);
        } catch { /* silent */ }
    }

    function renderActivityLog(events) {
        const container = document.getElementById('activity-feed');
        const countEl = document.getElementById('activity-count');
        if (!container) return;

        if (countEl) countEl.textContent = events.length;

        if (!events.length) {
            container.innerHTML = '<div class="empty-state"><p>No recent activity</p></div>';
            return;
        }

        const icons = {
            heartbeat: { cls: 'heartbeat', sym: '♥' },
            claim: { cls: 'claim', sym: '✓' },
            switch: { cls: 'switch', sym: '⇄' },
            error: { cls: 'error', sym: '!' },
            info: { cls: 'info', sym: 'ℹ' },
        };

        container.innerHTML = events.slice(0, 30).map(e => {
            const icon = icons[e.type] || icons.info;
            return `
                <div class="activity-item">
                    <div class="activity-icon ${icon.cls}">${icon.sym}</div>
                    <div class="activity-content">
                        <div class="activity-text">${esc(e.message)}</div>
                        <div class="activity-time">${formatTimestamp(e.timestamp)}</div>
                    </div>
                </div>`;
        }).join('');
    }

    // ── Nav Badges ────────────────────────────────────────────────
    async function updateNavBadges(status) {
        if (!status) {
            try { status = await get('/bot/status'); } catch { return; }
        }

        // Watch badge — green dot when actively watching
        const watchBadge = document.getElementById('nav-badge-watch');
        if (watchBadge) {
            if (status.active_watches > 0) {
                watchBadge.classList.add('active');
            } else {
                watchBadge.classList.remove('active');
            }
        }

        // Drops badge — red count when claimable
        const dropsBadge = document.getElementById('nav-badge-drops');
        if (dropsBadge) {
            const count = status.claimable_count || 0;
            if (count > 0) {
                dropsBadge.textContent = count;
                dropsBadge.classList.add('active');
            } else {
                dropsBadge.classList.remove('active');
            }
        }
    }

    // ── Theme ─────────────────────────────────────────────────────
    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);

        const sunIcon = document.getElementById('theme-icon-sun');
        const moonIcon = document.getElementById('theme-icon-moon');
        if (sunIcon && moonIcon) {
            sunIcon.style.display = theme === 'light' ? 'block' : 'none';
            moonIcon.style.display = theme === 'light' ? 'none' : 'block';
        }

        const settingsToggle = document.getElementById('settings-theme-toggle');
        if (settingsToggle) settingsToggle.checked = (theme === 'light');
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        applyTheme(current === 'dark' ? 'light' : 'dark');
    }

    // ── Claim Sound ───────────────────────────────────────────────
    function playClaimSound() {
        if (!soundEnabled) return;
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            // Rising two-tone chime
            [440, 660].forEach((freq, i) => {
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = freq;
                gain.gain.setValueAtTime(0.18, ctx.currentTime + i * 0.12);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.12 + 0.35);
                osc.connect(gain).connect(ctx.destination);
                osc.start(ctx.currentTime + i * 0.12);
                osc.stop(ctx.currentTime + i * 0.12 + 0.35);
            });
        } catch { /* silent */ }
    }

    function toggleSound(enabled) {
        soundEnabled = enabled;
        localStorage.setItem('claimSound', enabled ? 'true' : 'false');
    }

    // ── Boot ──────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', init);

    // ── Public API ────────────────────────────────────────────────
    return {
        navigate,
        checkDrops,
        refreshDrops,
        loadInventory,
        claimDrops,
        claimSingleDrop,
        toggleAutoWatch,
        stopWatch,
        stopSession,
        loadHistory,
        sortHistory,
        startLogin,
        cancelLogin,
        logout,
        addGame,
        removeGame,
        saveDiscord,
        saveEmail,
        testNotify,
        toggleTheme,
        toggleSound,
        startTour,
        skipTour,
    };

})();
