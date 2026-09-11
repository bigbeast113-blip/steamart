'use strict';

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  data: null,
  games: [],
  artKinds: [],
  browsePath: '',
  scanned: [],
  selection: new Map(),
  picker: { appid: null, gameId: null, kind: 'capsule' },
  cacheBust: Date.now(),
  busy: false,
};

// ---------------------------------------------------------------- network

async function api(path, options) {
  const response = await fetch('/api/' + path, Object.assign({
    headers: { 'Content-Type': 'application/json' },
  }, options));
  const text = await response.text();
  let payload = {};
  if (text) {
    try { payload = JSON.parse(text); }
    catch (e) { throw new Error('Unexpected response from SteamArt'); }
  }
  if (!response.ok) throw new Error(payload.error || ('HTTP ' + response.status));
  return payload;
}

const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

let toastTimer = null;
function toast(message, kind) {
  const el = $('#toast');
  el.textContent = message;
  el.className = 'toast ' + (kind || '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), kind === 'err' ? 7000 : 3500);
}

// ------------------------------------------------------------------ boot

async function load() {
  try {
    const data = await api('state');
    state.data = data;
    state.games = data.games || [];
    state.artKinds = data.art_kinds || [];
    render();
  } catch (err) {
    toast(err.message, 'err');
  }
}

function render() {
  renderHeader();
  renderBanners();
  renderLibrary();
  renderSettings();
}

// ---------------------------------------------------------------- header

function renderHeader() {
  const data = state.data;
  $('#steamPath').textContent = data.steam_root || 'Steam not found';

  const select = $('#userSelect');
  select.innerHTML = '';
  (data.users || []).forEach((user) => {
    const option = document.createElement('option');
    option.value = user.id32;
    option.textContent = user.name + ' (' + user.shortcut_count + ')';
    if (data.user && user.id32 === data.user.id32) option.selected = true;
    select.appendChild(option);
  });
  if (!data.users || !data.users.length) {
    select.innerHTML = '<option>no profiles</option>';
  }
}

function renderBanners() {
  const data = state.data;
  const host = $('#banners');
  host.innerHTML = '';

  const add = (kind, html, actionLabel, action) => {
    const el = document.createElement('div');
    el.className = 'banner ' + kind;
    el.innerHTML = '<span>' + html + '</span>';
    if (actionLabel) {
      const button = document.createElement('button');
      button.className = 'btn tiny';
      button.textContent = actionLabel;
      button.onclick = action;
      el.appendChild(button);
    }
    host.appendChild(el);
  };

  if (!data.steam_root) {
    add('bad', 'Could not find your Steam folder. Set it in Settings.',
      'Settings', () => showView('settings'));
  }
  if (!data.config.api_key_set) {
    add('info', 'Add a free SteamGridDB API key to start pulling artwork.',
      'Add key', () => showView('settings'));
  }
  if (data.steam_running) {
    add('warn', 'Steam is running. It rewrites its config when it closes, which can '
      + 'undo imports made now &mdash; close Steam, then hit Refresh.',
      'Refresh', load);
  }
  if (data.warning) add('warn', data.warning);
}

// --------------------------------------------------------------- library

function artUrl(appid, kind) {
  return '/api/art/' + appid + '/' + kind + '?v=' + state.cacheBust;
}

function renderLibrary() {
  const grid = $('#gameGrid');
  const term = $('#filter').value.trim().toLowerCase();
  const games = term
    ? state.games.filter((g) => g.name.toLowerCase().includes(term))
    : state.games;

  grid.innerHTML = '';
  $('#libraryEmpty').classList.toggle('hidden', state.games.length > 0);

  const missing = state.games.reduce(
    (total, game) => total + state.artKinds.filter((k) => !game.art[k.key]).length, 0);
  $('#libraryCount').textContent = state.games.length
    ? state.games.length + ' games, ' + missing + ' artwork slots empty'
    : '';
  $('#autoAllBtn').disabled = !state.games.length;

  games.forEach((game) => grid.appendChild(gameCard(game)));
}

function gameCard(game) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.appid = game.appid;

  const capsule = document.createElement('div');
  capsule.className = 'capsule';
  if (game.art.capsule) {
    const img = document.createElement('img');
    img.src = artUrl(game.appid, 'capsule');
    img.alt = game.name;
    img.loading = 'lazy';
    capsule.appendChild(img);
  } else {
    capsule.textContent = 'no artwork yet';
  }
  card.appendChild(capsule);

  const body = document.createElement('div');
  body.className = 'card-body';

  const name = document.createElement('div');
  name.className = 'card-name';
  name.textContent = game.name;
  name.title = game.name + '\n' + game.exe;
  body.appendChild(name);

  const dots = document.createElement('div');
  dots.className = 'dots';
  state.artKinds.forEach((kind) => {
    const dot = document.createElement('div');
    dot.className = 'dot' + (game.art[kind.key] ? ' on' : '');
    dot.title = kind.label + (game.art[kind.key] ? ': installed' : ': missing');
    dots.appendChild(dot);
  });
  body.appendChild(dots);

  const meta = document.createElement('div');
  meta.className = 'card-meta';
  meta.textContent = game.compat_tool ? game.compat_tool : (game.is_windows_exe ? 'no Proton set' : '');
  body.appendChild(meta);

  const status = document.createElement('div');
  status.className = 'card-status';
  body.appendChild(status);

  const actions = document.createElement('div');
  actions.className = 'card-actions';
  actions.appendChild(button('Get art', 'primary tiny', () => autoArt(game, card)));
  actions.appendChild(button('Pick', 'ghost tiny', () => openPicker(game)));
  actions.appendChild(button('⋯', 'ghost tiny', (event) => gameMenu(game, event)));
  body.appendChild(actions);

  card.appendChild(body);
  return card;
}

function button(label, className, onClick) {
  const el = document.createElement('button');
  el.className = 'btn ' + className;
  el.textContent = label;
  el.onclick = onClick;
  return el;
}

function cardStatus(card, message, kind) {
  if (!card) return;
  const el = card.querySelector('.card-status');
  if (el) {
    el.textContent = message || '';
    el.className = 'card-status ' + (kind || '');
  }
}

const norm = (text) => (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '');

function summarize(result) {
  const applied = Object.keys(result.applied || {});
  const errors = Object.keys(result.errors || {});
  if (errors.includes('_')) return { text: result.errors._, kind: 'err' };
  if (applied.length) {
    // Show what it matched when the title differs from the shortcut name, so a
    // filename like hordesoffate.exe does not quietly pick the wrong game.
    const matched = result.game && result.game.name;
    const guess = result.game && result.game.confidence < 0.999;
    const prefix = (matched && norm(matched) !== norm(result.name))
      ? '→ ' + matched + (guess ? ' (best guess)' : '') + ': '
      : '';
    return { text: prefix + 'added ' + applied.join(', '), kind: 'ok' };
  }
  if (errors.length) return { text: result.errors[errors[0]], kind: 'err' };
  const skipped = result.skipped || {};
  const nothing = Object.values(skipped).some((r) => r.includes('nothing available'));
  return { text: nothing ? 'nothing new on SteamGridDB' : 'already complete', kind: '' };
}

async function autoArt(game, card) {
  if (state.busy) return;
  if (!state.data.config.api_key_set) {
    toast('Add a SteamGridDB API key in Settings first.', 'err');
    return;
  }
  if (card) card.classList.add('busy');
  cardStatus(card, 'searching…');
  try {
    const result = await post('auto-art', {
      appid: game.appid,
      name: game.name,
      overwrite: $('#overwriteAll').checked,
    });
    const summary = summarize(result);
    cardStatus(card, summary.text, summary.kind);
    await refreshGames();
  } catch (err) {
    cardStatus(card, err.message, 'err');
  } finally {
    if (card) card.classList.remove('busy');
  }
}

async function autoAll() {
  if (state.busy) return;
  if (!state.data.config.api_key_set) {
    toast('Add a SteamGridDB API key in Settings first.', 'err');
    showView('settings');
    return;
  }
  const overwrite = $('#overwriteAll').checked;
  const todo = state.games.filter((game) =>
    overwrite || state.artKinds.some((kind) => !game.art[kind.key]));
  if (!todo.length) {
    toast('Every game already has all five artwork slots filled.', 'ok');
    return;
  }

  state.busy = true;
  $('#autoAllBtn').disabled = true;
  $('#progress').classList.remove('hidden');

  let done = 0;
  let filled = 0;
  const failures = [];
  for (const game of todo) {
    $('#progressText').textContent =
      'Fetching artwork for ' + game.name + ' (' + (done + 1) + ' of ' + todo.length + ')';
    $('#progressFill').style.width = ((done / todo.length) * 100) + '%';
    const card = $('.card[data-appid="' + game.appid + '"]');
    if (card) card.classList.add('busy');
    try {
      const result = await post('auto-art', {
        appid: game.appid, name: game.name, overwrite,
      });
      const applied = Object.keys(result.applied || {}).length;
      filled += applied;
      const summary = summarize(result);
      cardStatus(card, summary.text, summary.kind);
      if (summary.kind === 'err') failures.push(game.name);
    } catch (err) {
      cardStatus(card, err.message, 'err');
      failures.push(game.name);
    }
    if (card) card.classList.remove('busy');
    done += 1;
  }

  $('#progressFill').style.width = '100%';
  $('#progressText').textContent = 'Done — ' + filled + ' images added'
    + (failures.length ? ', ' + failures.length + ' games had no match' : '');
  state.busy = false;
  $('#autoAllBtn').disabled = false;
  await refreshGames();
  toast(filled
    ? filled + ' images added. Restart Steam to see them.'
    : 'No new artwork found.', filled ? 'ok' : '');
  setTimeout(() => $('#progress').classList.add('hidden'), 6000);
}

async function refreshGames() {
  const data = await api('games');
  state.games = data.games || [];
  state.cacheBust = Date.now();
  renderLibrary();
}

function gameMenu(game, event) {
  event.stopPropagation();
  const choice = window.prompt(
    game.name + '\n\n'
    + '1  Rename\n'
    + '2  Clear artwork\n'
    + '3  Set compatibility tool\n'
    + '4  Remove from Steam\n\n'
    + 'Type a number:', '');
  if (!choice) return;
  if (choice.trim() === '1') renameGame(game);
  else if (choice.trim() === '2') clearArt(game);
  else if (choice.trim() === '3') setCompat(game);
  else if (choice.trim() === '4') removeGame(game);
}

async function renameGame(game) {
  const name = window.prompt('New name for this game:', game.name);
  if (!name || name === game.name) return;
  try {
    await post('rename', { appid: game.appid, name: name });
    toast('Renamed. Artwork moved with it.', 'ok');
    await refreshGames();
  } catch (err) { toast(err.message, 'err'); }
}

async function clearArt(game) {
  if (!window.confirm('Delete all artwork for ' + game.name + '?')) return;
  try {
    await post('clear-art', { appid: game.appid });
    await refreshGames();
    toast('Artwork cleared.', 'ok');
  } catch (err) { toast(err.message, 'err'); }
}

async function setCompat(game) {
  const tools = (state.data.compat_tools || []).map((t) => t.name);
  const tool = window.prompt(
    'Compatibility tool for ' + game.name + '\n\nAvailable: ' + tools.join(', ')
    + '\n\nLeave blank to clear it.', game.compat_tool || tools[0] || '');
  if (tool === null) return;
  try {
    await post('compat', { appid: game.appid, tool: tool.trim() });
    await refreshGames();
    toast(tool.trim() ? 'Set to ' + tool.trim() : 'Compatibility tool cleared.', 'ok');
  } catch (err) { toast(err.message, 'err'); }
}

async function removeGame(game) {
  if (!window.confirm('Remove ' + game.name + ' from Steam and delete its artwork?')) return;
  try {
    await post('remove', { appid: game.appid, delete_art: true });
    await refreshGames();
    toast('Removed ' + game.name, 'ok');
  } catch (err) { toast(err.message, 'err'); }
}

// ---------------------------------------------------------- art picker

async function openPicker(game) {
  if (!state.data.config.api_key_set) {
    toast('Add a SteamGridDB API key in Settings first.', 'err');
    return;
  }
  state.picker = { appid: game.appid, gameId: null, kind: 'capsule', exe: game.exe };
  $('#pickerTitle').textContent = 'Artwork for ' + game.name;
  $('#pickerSearch').value = game.name;
  $('#pickerAssets').innerHTML = '';
  $('#pickerGames').innerHTML = '<span class="muted">searching…</span>';
  renderKindChips();
  $('#picker').classList.remove('hidden');
  await searchGames();
}

function renderKindChips() {
  const host = $('#pickerKinds');
  host.innerHTML = '';
  state.artKinds.forEach((kind) => {
    const chip = document.createElement('button');
    chip.className = 'chip' + (kind.key === state.picker.kind ? ' active' : '');
    chip.innerHTML = kind.label + '<small>' + kind.hint + '</small>';
    chip.onclick = () => {
      state.picker.kind = kind.key;
      renderKindChips();
      loadAssets();
    };
    host.appendChild(chip);
  });
}

async function searchGames() {
  const term = $('#pickerSearch').value.trim();
  const host = $('#pickerGames');
  host.innerHTML = '<span class="muted">searching…</span>';
  try {
    const data = await api('search?q=' + encodeURIComponent(term)
      + (state.picker.exe ? '&exe=' + encodeURIComponent(state.picker.exe) : ''));
    const results = (data.results || []).slice(0, 10);
    host.innerHTML = '';
    if (!results.length) {
      host.innerHTML = '<span class="muted">No matches on SteamGridDB. Try a shorter name.</span>';
      return;
    }
    results.forEach((game, index) => {
      const chip = document.createElement('button');
      chip.className = 'chip' + (index === 0 ? ' active' : '');
      chip.textContent = game.name;
      chip.onclick = () => {
        $$('#pickerGames .chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        state.picker.gameId = game.id;
        loadAssets();
      };
      host.appendChild(chip);
    });
    state.picker.gameId = results[0].id;
    loadAssets();
  } catch (err) {
    host.innerHTML = '<span class="muted">' + err.message + '</span>';
  }
}

async function loadAssets() {
  const host = $('#pickerAssets');
  if (!state.picker.gameId) { host.innerHTML = ''; return; }
  host.innerHTML = '<span class="muted">loading artwork…</span>';
  try {
    const data = await api('candidates?game_id=' + state.picker.gameId
      + '&kind=' + state.picker.kind);
    const assets = data.assets || [];
    host.innerHTML = '';
    if (!assets.length) {
      host.innerHTML = '<span class="muted">Nothing uploaded for this slot yet.</span>';
      return;
    }
    assets.forEach((asset, index) => {
      const card = document.createElement('div');
      card.className = 'asset';
      const img = document.createElement('img');
      img.src = asset.thumb || asset.url;
      img.loading = 'lazy';
      card.appendChild(img);
      if (index === 0) {
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.textContent = 'best match';
        card.appendChild(tag);
      }
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = (asset.width && asset.height)
        ? asset.width + '×' + asset.height + (asset.style ? ' · ' + asset.style : '')
        : (asset.style || '');
      card.appendChild(meta);
      card.onclick = () => applyAsset(asset, card);
      host.appendChild(card);
    });
  } catch (err) {
    host.innerHTML = '<span class="muted">' + err.message + '</span>';
  }
}

async function applyAsset(asset, card) {
  card.style.opacity = '.4';
  try {
    await post('apply', {
      appid: state.picker.appid,
      kind: state.picker.kind,
      url: asset.url,
    });
    toast('Applied. Restart Steam to see it.', 'ok');
    await refreshGames();
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    card.style.opacity = '1';
  }
}

// ----------------------------------------------------------- add games

async function loadPlaces() {
  try {
    const data = await api('places');
    const host = $('#placesList');
    host.innerHTML = '';
    (data.places || []).forEach((place) => {
      const item = document.createElement('li');
      item.textContent = place.label;
      item.title = place.path;
      item.onclick = () => browse(place.path);
      host.appendChild(item);
    });
    if (data.places && data.places.length && !state.browsePath) {
      browse(data.places[0].path);
    }
  } catch (err) { toast(err.message, 'err'); }
}

async function browse(path) {
  const host = $('#browseList');
  host.innerHTML = '<div class="browse-row"><span class="muted">loading…</span></div>';
  try {
    const data = await api('browse?path=' + encodeURIComponent(path));
    state.browsePath = data.path;
    state.scanned = [];
    $('#pathInput').value = data.path;
    $('#upBtn').disabled = !data.parent;
    $('#upBtn').onclick = () => data.parent && browse(data.parent);

    host.innerHTML = '';
    if (data.folders.length) {
      host.appendChild(sectionHead('Folders'));
      data.folders.forEach((folder) => {
        const row = document.createElement('div');
        row.className = 'browse-row folder';
        row.innerHTML = '<span class="icon">▸</span>';
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = folder.name;
        row.appendChild(name);
        row.onclick = () => browse(folder.path);
        host.appendChild(row);
      });
    }
    if (data.files.length) {
      host.appendChild(sectionHead('Games in this folder'));
      data.files.forEach((file) => host.appendChild(gameRow({
        path: file.path,
        name: guessName(file.name),
        size: file.size,
        already_added: state.games.some((g) => sameFile(g.exe, file.path)),
      })));
    }
    if (!data.folders.length && !data.files.length) {
      host.innerHTML = '<div class="browse-row"><span class="muted">Empty folder.</span></div>';
    }
  } catch (err) {
    host.innerHTML = '<div class="browse-row"><span class="muted">' + err.message + '</span></div>';
  }
}

function sameFile(a, b) {
  return (a || '').replace(/\\/g, '/').toLowerCase() === (b || '').replace(/\\/g, '/').toLowerCase();
}

function guessName(filename) {
  return filename.replace(/\.[^.]+$/, '').replace(/[_.-]+/g, ' ').trim();
}

function sectionHead(label) {
  const el = document.createElement('div');
  el.className = 'section-head';
  el.textContent = label;
  return el;
}

function gameRow(item) {
  const row = document.createElement('div');
  row.className = 'browse-row' + (item.already_added ? ' added' : '');

  const check = document.createElement('input');
  check.type = 'checkbox';
  check.disabled = !!item.already_added;
  check.checked = state.selection.has(item.path);

  const name = document.createElement('span');
  name.className = 'name';
  name.innerHTML = '';
  const label = document.createElement('div');
  label.textContent = item.name;
  const sub = document.createElement('div');
  sub.className = 'sub';
  sub.textContent = item.already_added ? 'already in your library' : item.path;
  name.appendChild(label);
  name.appendChild(sub);

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.value = item.name;
  nameInput.title = 'Name shown in Steam and used to search for artwork';
  nameInput.disabled = !!item.already_added;
  nameInput.oninput = () => {
    if (state.selection.has(item.path)) {
      state.selection.set(item.path, { path: item.path, name: nameInput.value });
    }
  };

  check.onchange = () => {
    if (check.checked) state.selection.set(item.path, { path: item.path, name: nameInput.value });
    else state.selection.delete(item.path);
    updateSelectionCount();
  };

  row.appendChild(check);
  row.appendChild(name);
  row.appendChild(nameInput);
  return row;
}

function updateSelectionCount() {
  const count = state.selection.size;
  $('#selectionCount').textContent = count
    ? count + ' selected'
    : 'nothing selected';
  $('#addSelectedBtn').disabled = count === 0;
}

async function scanFolder() {
  const host = $('#browseList');
  const path = $('#pathInput').value.trim();
  if (!path) return;
  host.innerHTML = '<div class="browse-row"><span class="muted">scanning…</span></div>';
  try {
    const data = await post('scan', { path: path, depth: 3 });
    state.scanned = data.found || [];
    host.innerHTML = '';
    host.appendChild(sectionHead(state.scanned.length + ' games found under this folder'));
    if (!state.scanned.length) {
      host.appendChild(sectionHead('Nothing that looks like a game. Try a folder closer to your games.'));
      return;
    }
    state.scanned.forEach((item) => host.appendChild(gameRow(item)));
  } catch (err) {
    host.innerHTML = '<div class="browse-row"><span class="muted">' + err.message + '</span></div>';
  }
}

function selectAllFound() {
  if (!state.scanned.length) {
    toast('Scan a folder first.', '');
    return;
  }
  state.scanned.filter((item) => !item.already_added)
    .forEach((item) => state.selection.set(item.path, { path: item.path, name: item.name }));
  $$('#browseList input[type="checkbox"]').forEach((box) => {
    if (!box.disabled) box.checked = true;
  });
  updateSelectionCount();
}

async function addSelected() {
  const games = Array.from(state.selection.values());
  if (!games.length) return;
  const fetchArt = $('#optArt').checked;

  $('#addSelectedBtn').disabled = true;
  $('#progress').classList.remove('hidden');
  $('#progressFill').style.width = '10%';
  $('#progressText').textContent = 'Adding ' + games.length + ' games'
    + (fetchArt ? ' and fetching artwork…' : '…');
  showView('library');

  try {
    const result = await post('add', {
      games: games,
      set_compat: $('#optCompat').checked,
      fetch_art: fetchArt,
    });
    $('#progressFill').style.width = '100%';
    const images = (result.art || []).reduce(
      (total, entry) => total + Object.keys(entry.applied || {}).length, 0);
    const parts = [result.added.length + ' games added'];
    if (fetchArt) parts.push(images + ' images downloaded');
    if (result.skipped.length) parts.push(result.skipped.length + ' skipped');
    $('#progressText').textContent = parts.join(', ') + '. Restart Steam to see them.';
    toast(parts.join(', '), 'ok');

    (result.compat_errors || []).forEach((message) => toast(message, 'err'));
    state.selection.clear();
    updateSelectionCount();
    await refreshGames();
    setTimeout(() => $('#progress').classList.add('hidden'), 8000);
  } catch (err) {
    $('#progress').classList.add('hidden');
    toast(err.message, 'err');
  } finally {
    $('#addSelectedBtn').disabled = state.selection.size === 0;
  }
}

// ----------------------------------------------------------- settings

function renderSettings() {
  const config = state.data.config || {};
  $('#apiKeyInput').placeholder = config.api_key_set
    ? 'saved (' + config.api_key + ') — paste a new key to replace it'
    : 'paste your SteamGridDB key';
  $('#steamRootInput').value = config.steam_root || '';
  $('#setCompat').checked = !!config.set_compat_for_exe;
  $('#autoArtImport').checked = !!config.auto_art_on_import;
  $('#overwriteArt').checked = !!config.overwrite_existing_art;
  $('#allowNsfw').checked = !!config.allow_nsfw;
  $('#allowHumor').checked = !!config.allow_humor;
  $('#optCompat').checked = !!config.set_compat_for_exe;
  $('#optArt').checked = !!config.auto_art_on_import;
  $('#overwriteAll').checked = !!config.overwrite_existing_art;

  const select = $('#compatSelect');
  select.innerHTML = '';
  const tools = state.data.compat_tools || [];
  if (!tools.length) {
    select.innerHTML = '<option value="">none detected</option>';
  }
  tools.forEach((tool) => {
    const option = document.createElement('option');
    option.value = tool.name;
    option.textContent = tool.label + ' (' + tool.name + ')';
    if (tool.name === config.compat_tool) option.selected = true;
    select.appendChild(option);
  });
}

async function saveKey() {
  const key = $('#apiKeyInput').value.trim();
  const status = $('#keyStatus');
  if (!key) { status.textContent = 'Paste a key first.'; status.className = 'status err'; return; }
  status.textContent = 'Checking…';
  status.className = 'status';
  try {
    await post('check-key', { api_key: key });
    status.textContent = 'Key works. Artwork lookups are ready.';
    status.className = 'status ok';
    $('#apiKeyInput').value = '';
    await load();
  } catch (err) {
    status.textContent = err.message;
    status.className = 'status err';
  }
}

async function saveSettings() {
  const status = $('#settingsStatus');
  try {
    await post('settings', {
      steam_root: $('#steamRootInput').value.trim(),
      compat_tool: $('#compatSelect').value,
      set_compat_for_exe: $('#setCompat').checked,
      auto_art_on_import: $('#autoArtImport').checked,
      overwrite_existing_art: $('#overwriteArt').checked,
      allow_nsfw: $('#allowNsfw').checked,
      allow_humor: $('#allowHumor').checked,
    });
    status.textContent = 'Saved.';
    status.className = 'status ok';
    await load();
  } catch (err) {
    status.textContent = err.message;
    status.className = 'status err';
  }
}

// --------------------------------------------------------------- wiring

const VIEWS = ['library', 'add', 'settings'];

function showView(name) {
  if (!VIEWS.includes(name)) name = 'library';
  $$('.tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.view === name));
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === 'view-' + name));
  if (location.hash.slice(1) !== name) location.hash = name;
  if (name === 'add' && !state.browsePath) loadPlaces();
}

$$('.tab').forEach((tab) => { tab.onclick = () => showView(tab.dataset.view); });
window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
$('#refreshBtn').onclick = () => { state.cacheBust = Date.now(); load(); };
$('#filter').oninput = renderLibrary;
$('#autoAllBtn').onclick = autoAll;
$('#userSelect').onchange = async (event) => {
  await post('settings', { user_id32: event.target.value });
  state.browsePath = '';
  await load();
};
$('#saveKeyBtn').onclick = saveKey;
$('#saveSettingsBtn').onclick = saveSettings;
$('#apiKeyInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') saveKey(); });

$('#goBtn').onclick = () => browse($('#pathInput').value.trim());
$('#pathInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') browse(e.target.value.trim()); });
$('#scanBtn').onclick = scanFolder;
$('#selectAllBtn').onclick = selectAllFound;
$('#addSelectedBtn').onclick = addSelected;

$('#pickerClose').onclick = () => $('#picker').classList.add('hidden');
$('#pickerSearchBtn').onclick = searchGames;
$('#pickerSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') searchGames(); });
$('#picker').addEventListener('click', (e) => {
  if (e.target.id === 'picker') $('#picker').classList.add('hidden');
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $('#picker').classList.add('hidden');
});

showView(location.hash.slice(1) || 'library');
load();
