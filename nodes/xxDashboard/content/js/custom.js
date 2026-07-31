/*
 * LTL-LIB Dashboard - custom JavaScript.
 *
 * Media Player tab: the mpv playlist UI (drives the mpv engine node through this
 * node's own actions/events, which the Mk 2 recipe creates from content/index.xml
 * and the operator binds to the engine in the Bindings editor).
 *
 * Page-scoped: this tab's markup lives under a single root, <... class="mpv-root">
 * (see the "Media Player" page in content/index.xml). Every DOM read/write is routed
 * through root() so the injected playlist editor stays inside this page and coexists
 * with the sibling <page> tabs of this dashboard. The poller is inert when
 * the root isn't present/visible. Event delegation stays on document because the
 * .mpv-* classes are unique to this UI and document always exists; the leak risk was
 * the document-wide scans (.well) and append-to-body fallback, which are now scoped.
 *
 * Playlists block: pick a saved playlist from the dropdown -> its files show in
 * "Included", the rest in "Available media". Tick/untick to move files between
 * them, set positions, then "Save playlist" persists changes. "+ Add Playlist"
 * starts a new draft. Names are resolved by row INDEX, never embedded into HTML
 * attributes (filenames/names may contain characters that would break markup).
 */
$(function () {
  var POLL_MS = 1500;
  var lastFiles = null, currentFiles = [];
  var lastPresets = null, currentPresets = [];
  var editingName = null;          // currently loaded/selected playlist ('' = none)
  var draftMode = false;           // building a new, not-yet-saved playlist
  var lastReady = null, lastLoop = null, lastPaused = null, lastRunning = null;
  var lastPlaying = null;          // is a video actually loaded/playing (NowPlaying non-empty)?

  // This tab's scoping root. Re-queried each use so it survives Nodel re-rendering
  // the page markup; all DOM lookups/injections below descend from it.
  function root() { return $('.mpv-root'); }

  function esc(s) { return $('<div>').text(s == null ? '' : s).html(); }
  function trim(s) { return (s == null ? '' : String(s)).replace(/^\s+|\s+$/g, ''); }

  function callAction(name, argVal) {
    return $.postJSON('REST/actions/' + encodeURIComponent(name) + '/call',
                      JSON.stringify({ arg: argVal }));
  }

  // Locate a tile within this tab (a <group> renders as <div class="well"> with an
  // <h4> title). Scoped to root() so a like-named tile on another tab can't match.
  function tileByTitle(needle) {
    var found = null;
    root().find('.well').each(function () {
      if (found) return;
      if (($(this).children('h1,h2,h3,h4,h5,h6').first().text() || '').indexOf(needle) >= 0) found = this;
    });
    return found;
  }
  function container(cls, titleNeedle) {
    var tile = titleNeedle ? tileByTitle(titleNeedle) : null;
    var $a = root().find('.' + cls);
    if ($a.length) {
      if (tile && $a.closest('.well')[0] !== tile) $(tile).append($a);
      return $a.first();
    }
    $a = $('<div class="' + cls + '"></div>');
    if (tile) $(tile).append($a); else root().first().append($a);
    return $a;
  }

  // ---- Playlists block structure (built once) ------------------------------

  function buildPlaylistsBlock() {
    var tile = tileByTitle('Playlists');
    if (!tile) return null;
    var $tile = $(tile);
    var $h = $tile.children('h1,h2,h3,h4,h5,h6').first();
    if ($h.length && !$h.find('.mpv-pl-add').length) {
      $h.append('<button class="btn btn-xs btn-success mpv-pl-add">+ Add Playlist</button>');
    }
    if (!$tile.find('.mpv-pl-body').length) {
      $tile.append(
        '<div class="mpv-pl-body">' +
          '<div class="mpv-pl-controls form-inline">' +
            '<select class="mpv-pl-select form-control input-sm"></select> ' +
            '<input type="text" class="mpv-pl-name form-control input-sm" placeholder="Rename playlist">' +
          '</div>' +
          '<div class="mpv-pl-toolbar">' +
            '<button class="btn btn-default btn-sm mpv-tb" data-act="Rescan">Rescan folder</button> ' +
            '<button class="btn btn-default btn-sm mpv-tb" data-act="AutoNumber">Auto-number</button> ' +
            '<button class="btn btn-default btn-sm mpv-tb" data-act="ClearSelection">Clear selection</button> ' +
            '<button class="btn btn-default btn-sm mpv-tb" data-act="ClearOrdering">Clear positions</button> ' +
            '<button class="btn btn-danger btn-sm mpv-pl-delete"><i class="fas fa-trash"></i> Delete playlist</button>' +
          '</div>' +
          '<div class="mpv-pl-included"></div>' +
          '<div class="mpv-pl-available"></div>' +
          '<div class="mpv-pl-savebar"><button class="btn btn-primary mpv-pl-save" disabled>Save playlist</button></div>' +
        '</div>');
    }
    return $tile.find('.mpv-pl-body');
  }

  // ---- File lists (Included / Available media) -----------------------------

  function fileTable(rows, isIncluded) {
    if (!rows.length) {
      return '<div class="text-muted mpv-empty">' +
        (isIncluded ? 'No files in this playlist yet.' : 'No other media in the folder.') + '</div>';
    }
    var body = rows.map(function (pair) {
      var f = pair[0], i = pair[1];
      var pos = parseInt(f.position, 10) || 0;
      var checked = f.included ? 'checked' : '';
      var disabled = f.included ? '' : ' disabled';
      return '<tr class="mpv-row' + (f.included ? ' mpv-on' : '') + '" data-i="' + i + '">' +
        '<td class="mpv-c"><input type="checkbox" class="mpv-inc" ' + checked + '></td>' +
        '<td class="mpv-c"><input type="number" min="0" step="1" class="mpv-pos" value="' + pos + '"' + disabled + '></td>' +
        '<td class="mpv-name">' + esc(f.name) + '</td>' +
        '<td class="mpv-playcell"><button class="btn btn-xs btn-success mpv-play"></button></td>' +
      '</tr>';
    }).join('');
    // Column headers only on the Included table; the two lists stack vertically,
    // so the headers at the top of Included label the Available columns too.
    var head = isIncluded ?
      '<thead><tr>' +
        '<th class="mpv-c">In</th><th class="mpv-c">Playlist position</th>' +
        '<th class="mpv-namecol">Video File</th><th class="mpv-playcell">Play video once</th>' +
      '</tr></thead>' : '';
    return '<table class="table table-condensed mpv-table">' + head +
      '<tbody>' + body + '</tbody></table>';
  }

  // Files in the currently-selected *saved* playlist -- this (not the working
  // tick) decides which list a file sits in. A new draft, or nothing selected,
  // has no saved members yet, so every file starts in "Available media".
  function savedMemberSet() {
    var set = {};
    if (!draftMode && editingName) {
      currentPresets.forEach(function (p) {
        if (p.name === editingName) (p.files || []).forEach(function (n) { set[n] = true; });
      });
    }
    return set;
  }

  function renderLists(files) {
    if (!buildPlaylistsBlock()) return;
    currentFiles = files;
    // Split by saved membership, NOT the working tick: ticking an available
    // file stages it (its position box un-greys in place, see fileTable) but it
    // only crosses into "Included" once "Save playlist" commits the selection.
    var members = savedMemberSet();
    var inc = [], avail = [];
    files.forEach(function (f, i) { (members[f.name] ? inc : avail).push([f, i]); });
    root().find('.mpv-pl-included').html('<h5 class="mpv-listhead">Included</h5>' + fileTable(inc, true));
    root().find('.mpv-pl-available').html('<h5 class="mpv-listhead">Available media</h5>' + fileTable(avail, false));
    updateSaveState();
  }

  function rowFile(el) {
    var f = currentFiles[parseInt($(el).closest('tr').data('i'), 10)];
    return (f && typeof f.name === 'string') ? f.name : null;
  }

  // ---- Dropdown + name + save state ----------------------------------------

  function renderDropdown() {
    if (!buildPlaylistsBlock()) return;
    var $sel = root().find('.mpv-pl-select');
    var opts = ['<option value="">-- select a playlist --</option>'];
    currentPresets.forEach(function (p, i) { opts.push('<option value="' + i + '">' + esc(p.name) + '</option>'); });
    if (draftMode) opts.push('<option value="__new__">(new playlist - unsaved)</option>');
    $sel.html(opts.join(''));
    if (draftMode) {
      $sel.val('__new__');
    } else {
      var idx = -1;
      currentPresets.forEach(function (p, i) { if (p.name === editingName) idx = i; });
      $sel.val(idx >= 0 ? String(idx) : '');
    }
    var $name = root().find('.mpv-pl-name');
    if (!$name.is(':focus')) {
      $name.val('');   // always show the greyed "Rename playlist" placeholder;
                       // typing a name here renames the playlist on Save
    }
    updateSaveState();
  }

  function updateSaveState() {
    var $save = root().find('.mpv-pl-save');
    if (!$save.length) return;
    var name = trim(root().find('.mpv-pl-name').val());
    var enabled;
    if (draftMode) enabled = !!name;                                  // new draft: needs a name
    else if (editingName) enabled = (lastReady === false) ||          // dirty (unsaved changes)
                                    (!!name && name !== editingName);  // ...or a rename typed
    else enabled = false;
    $save.prop('disabled', !enabled);
  }

  // The big Start button is dual-purpose:
  //   - nothing playing -> "Start", fires Play (disabled until a playlist is
  //     ready, exactly as before).
  //   - a video playing -> becomes a Pause/Resume toggle, firing PauseToggle,
  //     labelled by the Paused state and always enabled.
  // NB: keyed off NowPlaying (lastPlaying), NOT MPVRunning -- mpv is kept alive
  // idle, so MPVRunning is true even with nothing loaded.
  // The framework's click handler reads getAction() (i.e. jQuery .data('action'))
  // live at click time, so retargeting via .data('action', ...) is enough; the
  // data-action attribute stays present so the delegated selector still matches.
  function updatePlayButton() {
    var $btn = root().find('.mpv-play-btn');
    if (!$btn.length) return;
    if (lastPlaying === true) {
      var paused = (lastPaused === true);
      $btn.data('action', 'PauseToggle');
      $btn.find('p').text(paused ? 'Resume' : 'Pause');
      $btn.find('.fas').attr('class', paused ? 'fas fa-play' : 'fas fa-pause');
      $btn.removeClass('mpv-disabled').attr('title', '');
    } else {
      $btn.data('action', 'Play');
      $btn.find('p').text('Start');
      $btn.find('.fas').attr('class', 'fas fa-play');
      var ready = (lastReady === true);
      $btn.toggleClass('mpv-disabled', !ready)
          .attr('title', ready ? '' : 'Select or save a playlist first');
    }
  }

  // ---- Handlers ------------------------------------------------------------
  // Delegated on document: the .mpv-* classes are unique to this UI, so these
  // fire only for this tab's rows regardless of when the markup is injected.

  $(document).on('change', '.mpv-inc', function () {
    var file = rowFile(this);
    if (file === null) return;
    var on = $(this).is(':checked');
    callAction('SetSelection', { file: file, included: on });
    // Un-grey/grey this row's position box in place; the row stays in its list
    // (Available/Included) until "Save playlist" commits the change.
    var $row = $(this).closest('tr');
    $row.find('.mpv-pos').prop('disabled', !on);
    $row.toggleClass('mpv-on', on);
    lastFiles = null;
  });
  $(document).on('change', '.mpv-pos', function () {
    var file = rowFile(this);
    if (file === null) return;
    var pos = parseInt($(this).val(), 10);
    if (isNaN(pos) || pos < 0) pos = 0;
    callAction('SetPosition', { file: file, position: pos });
    lastFiles = null;
  });
  $(document).on('click', '.mpv-play', function () {
    var file = rowFile(this);
    if (file !== null) callAction('PlayFile', file);
  });

  $(document).on('click', '.mpv-tb', function () {
    callAction($(this).data('act'), null);
    lastFiles = null;
  });

  $(document).on('change', '.mpv-pl-select', function () {
    var v = $(this).val();
    if (v === '__new__' || v === '') return;
    var p = currentPresets[parseInt(v, 10)];
    if (!p) return;
    draftMode = false;
    callAction('RecallPreset', p.name);
    editingName = null; lastFiles = null;              // force refresh from poll
  });

  $(document).on('input', '.mpv-pl-name', updateSaveState);

  $(document).on('click', '.mpv-pl-add', function () {
    draftMode = true;
    editingName = '';
    callAction('NewPlaylist', null);                   // clear engine selection + editing
    renderDropdown();
    root().find('.mpv-pl-name').val('').focus();
    lastFiles = null;
    updateSaveState();
  });

  $(document).on('click', '.mpv-pl-save', function () {
    if ($(this).prop('disabled')) return;
    var oldName = draftMode ? '' : (editingName || '');
    // Box is empty by default (shows the "Rename playlist" placeholder), so a
    // blank box means "save under the current name"; typed text means rename.
    var name = trim(root().find('.mpv-pl-name').val()) || oldName;
    if (!name) return;
    callAction('SavePreset', name).done(function () {
      if (oldName && oldName !== name) callAction('DeletePreset', oldName);   // rename
      draftMode = false;
      lastPresets = null; editingName = null; lastFiles = null;               // refresh
    });
  });

  $(document).on('click', '.mpv-pl-delete', function () {
    var name = draftMode ? '' : (editingName || '');
    if (!name) { window.alert('Select a saved playlist first.'); return; }
    if (!window.confirm('Delete the playlist "' + name + '"?\n\nThis cannot be undone.')) return;
    callAction('DeletePreset', name);
    draftMode = false;
    lastPresets = null; editingName = null; lastFiles = null;
  });

  // ---- Polling -------------------------------------------------------------

  function refresh() {
    // Inert unless this tab's markup is present and visible (skips REST while a
    // sibling tab is active in a shared dashboard).
    var $r = root();
    if (!$r.length || !$r.is(':visible')) return;
    buildPlaylistsBlock();
    $.getJSON('REST/events/Presets', function (d) {
      var presets = (d && d.arg) ? d.arg : [];
      var json = JSON.stringify(presets);
      if (json === lastPresets) return;
      lastPresets = json; currentPresets = presets;
      renderDropdown();
      renderLists(currentFiles);        // membership may have changed (e.g. after a save)
    });
    $.getJSON('REST/events/EditingPreset', function (d) {
      var name = (d && typeof d.arg === 'string') ? d.arg : '';
      if (name === editingName) return;
      editingName = name;
      if (name) draftMode = false;
      renderDropdown();
      renderLists(currentFiles);        // re-split now the selected playlist changed
    });
    $.getJSON('REST/events/Files', function (d) {
      var files = (d && d.arg) ? d.arg : [];
      var json = JSON.stringify(files);
      if (json === lastFiles) return;
      if (root().find('.mpv-pl-included, .mpv-pl-available').find('input:focus').length) return;  // mid-edit
      lastFiles = json;
      renderLists(files);
    });
    $.getJSON('REST/events/PlayReady', function (d) {
      var ready = !!(d && d.arg === true);
      if (ready === lastReady) return;
      lastReady = ready;
      updatePlayButton();   // ready only gates the button while NOT running
      updateSaveState();
    });
    $.getJSON('REST/events/Loop', function (d) {
      var on = !!(d && d.arg === true);
      if (on === lastLoop) return;
      lastLoop = on;
      var $l = root().find('.mpv-loopbtn');
      $l.toggleClass('mpv-loop-on', on).toggleClass('mpv-loop-off', !on);
      var $s = $l.find('.mpv-loop-state');
      if (!$s.length) { $l.append('<span class="mpv-loop-state"></span>'); $s = $l.find('.mpv-loop-state'); }
      $s.text(on ? 'On' : 'Off');
    });
    $.getJSON('REST/events/Paused', function (d) {
      var paused = !!(d && d.arg === true);
      if (paused === lastPaused) return;
      lastPaused = paused;
      updatePlayButton();   // Start button reflects Pause/Resume while playing
    });
    $.getJSON('REST/events/MPVRunning', function (d) {
      var running = !!(d && d.arg === true);
      if (running === lastRunning) return;
      lastRunning = running;
      container('mpv-status', 'Playback')
        .removeClass('mpv-status-on mpv-status-off')
        .addClass(running ? 'mpv-status-on' : 'mpv-status-off')
        .text(running ? 'mpv running' : 'mpv not running');
    });
    $.getJSON('REST/events/NowPlaying', function (d) {
      var np = (d && d.arg) ? d.arg : null;
      var playing = !!(np && ((np.name && np.name.length) ||
                              (np.files && np.files.length)));
      if (playing === lastPlaying) return;
      lastPlaying = playing;
      updatePlayButton();   // a loaded video flips Start <-> Pause/Resume
    });
  }

  setInterval(refresh, POLL_MS);
  refresh();
});
