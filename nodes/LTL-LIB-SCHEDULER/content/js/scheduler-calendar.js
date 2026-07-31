(function (root, factory) {
  'use strict';

  var model = factory();
  if (typeof module === 'object' && module.exports) module.exports = model;
  if (root) root.SchedulerCalendarModel = model;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var CRON_DAY_NAMES = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'];
  var MINUTES_PER_STEP = 15;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function stable(value) {
    if (Array.isArray(value)) {
      return '[' + value.map(stable).join(',') + ']';
    }
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ':' + stable(value[key]);
      }).join(',') + '}';
    }
    return JSON.stringify(value);
  }

  function normaliseSchedule(schedule) {
    var source = schedule || {};
    var has = Object.prototype.hasOwnProperty;
    return {
      enabled: has.call(source, 'enabled') ? source.enabled : true,
      name: has.call(source, 'name') ? source.name : '',
      cron: has.call(source, 'cron') ? source.cron : '',
      event: has.call(source, 'event') ? source.event : '',
      argument: has.call(source, 'argument') ? source.argument : '',
      timezone: has.call(source, 'timezone') ? source.timezone : '',
      exceptions: Array.isArray(source.exceptions) ? clone(source.exceptions) : [],
      notes: has.call(source, 'notes') ? source.notes : ''
    };
  }

  function numberInRange(value, minimum, maximum) {
    if (!/^\d+$/.test(value)) return null;
    var parsed = parseInt(value, 10);
    return parsed >= minimum && parsed <= maximum ? parsed : null;
  }

  function cronDayIndex(value) {
    var upper = String(value).toUpperCase();
    var named = CRON_DAY_NAMES.indexOf(upper);
    if (named >= 0) return named;
    if (!/^\d+$/.test(upper)) return null;
    var numeric = parseInt(upper, 10);
    if (numeric === 0 || numeric === 7) return 6;
    if (numeric >= 1 && numeric <= 6) return numeric - 1;
    return null;
  }

  function expandCronDays(field) {
    if (field === '*') return [0, 1, 2, 3, 4, 5, 6];
    var selected = {};
    var parts = field.split(',');

    for (var i = 0; i < parts.length; i += 1) {
      var part = String(parts[i]).trim();
      if (!part) return null;
      if (part.indexOf('/') >= 0) return null;

      if (part.indexOf('-') >= 0) {
        var range = part.split('-');
        if (range.length !== 2) return null;
        var start = cronDayIndex(range[0]);
        var end = cronDayIndex(range[1]);
        if (start == null || end == null) return null;
        var cursor = start;
        selected[cursor] = true;
        while (cursor !== end) {
          cursor = (cursor + 1) % 7;
          selected[cursor] = true;
        }
      } else {
        var day = cronDayIndex(part);
        if (day == null) return null;
        selected[day] = true;
      }
    }

    var days = [];
    for (var d = 0; d < 7; d += 1) {
      if (selected[d]) days.push(d);
    }
    return days.length ? days : null;
  }

  function parseWeeklyCron(expression) {
    var fields = String(expression || '').trim().split(/\s+/);
    if (fields.length !== 5) return null;

    var minute = numberInRange(fields[0], 0, 59);
    var hour = numberInRange(fields[1], 0, 23);
    if (minute == null || hour == null || fields[2] !== '*' || fields[3] !== '*') return null;

    var days = expandCronDays(fields[4]);
    if (!days) return null;

    return {
      minute: minute,
      hour: hour,
      days: days
    };
  }

  function compactCronDays(days) {
    var selected = {};
    (days || []).forEach(function (day) { selected[day] = true; });
    var ordered = [];
    for (var i = 0; i < 7; i += 1) {
      if (selected[i]) ordered.push(i);
    }
    if (ordered.length === 7) return '*';
    return ordered.map(function (day) { return CRON_DAY_NAMES[day]; }).join(',');
  }

  function buildWeeklyCron(draft) {
    return String(draft.minute) + ' ' + String(draft.hour) + ' * * ' + compactCronDays(draft.days);
  }

  function pad(value) {
    return value < 10 ? '0' + value : String(value);
  }

  function formatTime(hour, minute) {
    return pad(hour) + ':' + pad(minute);
  }

  function parseTime(value) {
    var match = /^(\d{1,2}):(\d{2})$/.exec(String(value || '').trim());
    if (!match) return null;
    var hour = parseInt(match[1], 10);
    var minute = parseInt(match[2], 10);
    if (hour > 23 || minute > 59) return null;
    return {hour: hour, minute: minute};
  }

  function roundMinutes(minutes) {
    var rounded = Math.round(minutes / MINUTES_PER_STEP) * MINUTES_PER_STEP;
    return Math.max(0, Math.min((24 * 60) - MINUTES_PER_STEP, rounded));
  }

  function layoutOccurrences(occurrences, blockHeight) {
    var sorted = (occurrences || []).slice(0).sort(function (left, right) {
      if (left.top !== right.top) return left.top - right.top;
      return left.index - right.index;
    });
    var groups = [];

    sorted.forEach(function (occurrence) {
      var end = occurrence.top + blockHeight;
      var group = groups.length ? groups[groups.length - 1] : null;
      if (!group || occurrence.top >= group.end) {
        group = {end: end, occurrences: []};
        groups.push(group);
      } else if (end > group.end) {
        group.end = end;
      }
      group.occurrences.push(occurrence);
    });

    groups.forEach(function (group) {
      var laneEnds = [];
      group.occurrences.forEach(function (occurrence) {
        var lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] > occurrence.top) lane += 1;
        if (lane === laneEnds.length) laneEnds.push(0);
        laneEnds[lane] = occurrence.top + blockHeight;
        occurrence.lane = lane;
      });
      group.occurrences.forEach(function (occurrence) {
        occurrence.laneCount = laneEnds.length;
      });
    });

    return sorted;
  }

  return {
    DAY_NAMES: DAY_NAMES,
    CRON_DAY_NAMES: CRON_DAY_NAMES,
    clone: clone,
    stable: stable,
    normaliseSchedule: normaliseSchedule,
    expandCronDays: expandCronDays,
    parseWeeklyCron: parseWeeklyCron,
    compactCronDays: compactCronDays,
    buildWeeklyCron: buildWeeklyCron,
    pad: pad,
    formatTime: formatTime,
    parseTime: parseTime,
    roundMinutes: roundMinutes,
    layoutOccurrences: layoutOccurrences
  };
});

(function ($, model) {
  'use strict';

  if (!$ || !model) return;

  var DAY_NAMES = model.DAY_NAMES;
  var CRON_DAY_NAMES = model.CRON_DAY_NAMES;
  var clone = model.clone;
  var stable = model.stable;
  var normaliseSchedule = model.normaliseSchedule;
  var parseWeeklyCron = model.parseWeeklyCron;
  var compactCronDays = model.compactCronDays;
  var buildWeeklyCron = model.buildWeeklyCron;
  var pad = model.pad;
  var formatTime = model.formatTime;
  var parseTime = model.parseTime;
  var roundMinutes = model.roundMinutes;
  var layoutOccurrences = model.layoutOccurrences;
  var HOUR_HEIGHT = 56;
  var BLOCK_HEIGHT = 46;
  var VALIDATION_DELAY = 250;

  var state = {
    originalSchedules: [],
    schedules: [],
    selectedIndex: null,
    draft: null,
    dirty: false,
    saving: false,
    revision: 0,
    editorSession: 0,
    editorCommitToken: 0,
    editorCommitting: false,
    validationToken: 0,
    validationTimer: null,
    hostTimezone: '',
    drag: null,
    toastTimer: null,
    calendarScrollTop: 7 * HOUR_HEIGHT,
    calendarScrollLeft: null,
    calendarPositioned: false
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function editorDraft(schedule) {
    var normalised = normaliseSchedule(schedule);
    var weekly = parseWeeklyCron(normalised.cron);
    return $.extend(true, {}, normalised, {
      mode: weekly ? 'weekly' : 'advanced',
      hour: weekly ? weekly.hour : 9,
      minute: weekly ? weekly.minute : 0,
      days: weekly ? weekly.days : [0],
      exceptionText: $.map(normalised.exceptions, function (item) {
        return item && item.date ? item.date : '';
      }).join('\n')
    });
  }

  function makeNewSchedule(day, minutes) {
    var index = state.schedules.length + 1;
    var rounded = roundMinutes(minutes == null ? 9 * 60 : minutes);
    return editorDraft({
      enabled: true,
      name: 'Schedule ' + index,
      cron: (rounded % 60) + ' ' + Math.floor(rounded / 60) + ' * * ' + CRON_DAY_NAMES[day == null ? 0 : day],
      event: 'Scheduled Event',
      timezone: '',
      exceptions: []
    });
  }

  function setDirty(value) {
    state.dirty = value;
    updateToolbar();
  }

  function markSchedulesChanged() {
    state.revision += 1;
    setDirty(true);
  }

  function startEditorSession() {
    state.editorSession += 1;
    state.editorCommitToken += 1;
    state.editorCommitting = false;
    state.validationToken += 1;
    clearTimeout(state.validationTimer);
    state.validationTimer = null;
  }

  function timezoneLabel(timezone) {
    if (!$.trim(timezone || '')) return 'Host';
    var parts = String(timezone).split('/');
    return parts[parts.length - 1].replace(/_/g, ' ');
  }

  function updateTimezoneNote() {
    var effective = state.hostTimezone || 'the host timezone';
    $('.scheduler-timezone-note').text(
      'Recurring weekly rules · times use each rule\'s timezone · blank uses ' + effective + '.'
    );
  }

  function updateToolbar() {
    var count = state.schedules.length;
    var enabled = 0;
    $.each(state.schedules, function (_, schedule) {
      if (schedule.enabled !== false) enabled += 1;
    });
    $('.scheduler-eyebrow').text(enabled + ' enabled · ' + count + ' configured');
    $('#scheduler-save').prop('disabled', state.saving || !state.dirty)
      .text(state.saving ? 'Saving…' : (state.dirty ? 'Save and apply' : 'Saved'));
  }

  function showToast(message, isError) {
    var toast = $('.scheduler-toast');
    clearTimeout(state.toastTimer);
    toast.text(message).toggleClass('is-error', !!isError).addClass('is-visible');
    state.toastTimer = setTimeout(function () {
      toast.removeClass('is-visible');
    }, isError ? 6500 : 3200);
  }

  function pageHtml() {
    return [
      '<main class="scheduler-app" aria-label="Schedule calendar">',
        '<header class="scheduler-toolbar">',
          '<div class="scheduler-toolbar-copy">',
            '<p class="scheduler-eyebrow">Loading schedules</p>',
            '<h1 class="scheduler-title">Weekly schedule</h1>',
            '<p class="scheduler-subtitle">Click a time to add a rule. Drag single-day blocks; edit repeating rules to change their days or time.</p>',
          '</div>',
          '<div class="scheduler-toolbar-actions">',
            '<button id="scheduler-new" class="scheduler-btn" type="button">New schedule</button>',
            '<button id="scheduler-save" class="scheduler-btn scheduler-btn-primary" type="button" disabled>Saved</button>',
          '</div>',
        '</header>',
        '<div class="scheduler-shell">',
          '<section class="scheduler-calendar-pane" aria-label="Weekly calendar">',
            '<div class="scheduler-calendar-meta">',
              '<div class="scheduler-calendar-meta-copy">',
                '<h2 class="scheduler-week-label">Recurring week</h2>',
                '<p class="scheduler-timezone-note">Times use each rule\'s timezone. Blank uses the host timezone.</p>',
              '</div>',
              '<div class="scheduler-legend" aria-label="Schedule legend">',
                '<span class="scheduler-legend-item"><span class="scheduler-legend-dot"></span>Enabled</span>',
                '<span class="scheduler-legend-item"><span class="scheduler-legend-dot is-disabled"></span>Disabled</span>',
              '</div>',
            '</div>',
            '<div class="scheduler-week-scroll" tabindex="0" aria-label="Scrollable week view">',
              '<div class="scheduler-week"></div>',
            '</div>',
            '<section class="scheduler-advanced" aria-labelledby="scheduler-advanced-heading">',
              '<h2 id="scheduler-advanced-heading" class="scheduler-section-heading">Advanced CRON rules</h2>',
              '<div class="scheduler-advanced-list"></div>',
            '</section>',
          '</section>',
          '<aside class="scheduler-editor" aria-label="Schedule details"></aside>',
          '<button class="scheduler-editor-backdrop" type="button" aria-label="Close schedule details"></button>',
        '</div>',
        '<div class="scheduler-toast" role="status" aria-live="polite"></div>',
      '</main>'
    ].join('');
  }

  function renderWeek() {
    var html = ['<div class="scheduler-time-head"></div>'];
    for (var d = 0; d < 7; d += 1) {
      html.push(
        '<div class="scheduler-day-head">' +
          '<span class="scheduler-day-name">' + DAY_NAMES[d] + '</span>' +
        '</div>'
      );
    }

    html.push('<div class="scheduler-time-axis">');
    for (var hour = 0; hour < 24; hour += 1) {
      html.push('<span class="scheduler-time-mark" style="top:' + (hour * HOUR_HEIGHT) + 'px">' + pad(hour) + ':00</span>');
    }
    html.push('</div>');

    for (var day = 0; day < 7; day += 1) {
      html.push('<div class="scheduler-day-lane" data-day="' + day + '" role="group" tabindex="0" aria-label="' + DAY_NAMES[day] + ' schedules. Press Enter to add a schedule.">');
      var occurrences = [];
      $.each(state.schedules, function (index, schedule) {
        var weekly = parseWeeklyCron(schedule.cron);
        if (!weekly || $.inArray(day, weekly.days) < 0) return;
        occurrences.push({
          index: index,
          schedule: schedule,
          weekly: weekly,
          top: ((weekly.hour * 60 + weekly.minute) / 60) * HOUR_HEIGHT
        });
      });

      $.each(layoutOccurrences(occurrences, BLOCK_HEIGHT), function (_, occurrence) {
        var index = occurrence.index;
        var schedule = occurrence.schedule;
        var weekly = occurrence.weekly;
        var recurring = weekly.days.length > 1;
        var selected = state.selectedIndex === index ? ' is-selected' : '';
        var disabled = schedule.enabled === false ? ' is-disabled' : '';
        var repeats = recurring ? ' is-recurring' : '';
        var collision = occurrence.laneCount > 1 ? ' is-collision' : '';
        var layout = 'top:' + occurrence.top + 'px';
        if (occurrence.laneCount > 1) {
          var laneWidth = 100 / occurrence.laneCount;
          var laneLeft = occurrence.lane * laneWidth;
          layout += ';left:calc(' + laneLeft + '% + 5px);right:auto;width:calc(' + laneWidth + '% - 8px)';
        }
        var effectiveTimezone = $.trim(schedule.timezone || '') || state.hostTimezone || 'Host timezone';
        var recurrenceText = recurring ? ' Repeats on ' + $.map(weekly.days, function (value) { return DAY_NAMES[value]; }).join(', ') + '.' : '';
        var accessibleLabel = 'Edit ' + (schedule.name || 'unnamed schedule') + ' at ' +
          formatTime(weekly.hour, weekly.minute) + ' in ' + effectiveTimezone + '.' + recurrenceText;
        html.push(
          '<button class="scheduler-block' + selected + disabled + repeats + collision + '" type="button" draggable="' + (!recurring) + '" ' +
            'data-index="' + index + '" data-day="' + day + '" style="' + layout + '" ' +
            'aria-label="' + escapeHtml(accessibleLabel) + '" title="' + escapeHtml(accessibleLabel) + '">' +
            '<span class="scheduler-block-time">' + formatTime(weekly.hour, weekly.minute) +
              '<span class="scheduler-block-zone">' + escapeHtml(timezoneLabel(schedule.timezone)) + '</span></span>' +
            '<span class="scheduler-block-name">' + escapeHtml(schedule.name || 'Unnamed schedule') + '</span>' +
          '</button>'
        );
      });
      html.push('</div>');
    }

    $('.scheduler-week').html(html.join(''));
    updateTimezoneNote();
    scrollToWorkingHours();
  }

  function renderAdvancedList() {
    var rows = [];
    $.each(state.schedules, function (index, schedule) {
      if (parseWeeklyCron(schedule.cron)) return;
      rows.push(
        '<div class="scheduler-advanced-row">' +
          '<span class="scheduler-status-dot' + (schedule.enabled === false ? ' is-disabled' : '') + '"></span>' +
          '<span class="scheduler-advanced-name">' + escapeHtml(schedule.name || 'Unnamed schedule') + '</span>' +
          '<code class="scheduler-advanced-cron">' + escapeHtml(schedule.cron || 'No CRON expression') + '</code>' +
          '<button class="scheduler-icon-btn scheduler-edit-advanced" type="button" data-index="' + index + '" aria-label="Edit ' + escapeHtml(schedule.name || 'schedule') + '">›</button>' +
        '</div>'
      );
    });
    $('.scheduler-advanced-list').html(rows.length ? rows.join('') : '<p class="scheduler-empty">Every rule fits the weekly calendar.</p>');
  }

  function renderCalendar() {
    renderWeek();
    renderAdvancedList();
    updateToolbar();
  }

  function scrollToWorkingHours() {
    var scroll = $('.scheduler-week-scroll');
    if (!scroll.length) return;
    var currentDay = (new Date().getDay() + 6) % 7;
    var dayWidth = 116;
    var initialPosition = !state.calendarPositioned;
    if (state.calendarScrollLeft == null) {
      state.calendarScrollLeft = Math.max(0, currentDay * dayWidth - 60);
    }
    var targetLeft = state.calendarScrollLeft;
    var targetTop = initialPosition ? 7 * HOUR_HEIGHT : state.calendarScrollTop;
    function applyPosition() {
      scroll.scrollLeft(targetLeft);
      scroll.scrollTop(targetTop);
    }
    function finishPosition() {
      applyPosition();
      state.calendarPositioned = true;
      scroll.addClass('is-positioned');
    }
    function positionWhenReady(attempt) {
      var element = scroll[0];
      if (!element || !$.contains(document, element)) return;
      if (element.clientHeight > 0 && element.scrollHeight > element.clientHeight) {
        finishPosition();
        return;
      }
      if (attempt < 120) {
        window.requestAnimationFrame(function () { positionWhenReady(attempt + 1); });
      }
    }
    positionWhenReady(0);
    $(window).off('.schedulerInitialScroll').one(
      'load.schedulerInitialScroll pageshow.schedulerInitialScroll',
      function () { window.setTimeout(function () { positionWhenReady(0); }, 0); }
    );
    if (document.readyState === 'complete') {
      window.setTimeout(function () { positionWhenReady(0); }, 0);
    }
  }

  function exceptionValues(text) {
    var values = [];
    var seen = {};
    $.each(String(text || '').split(/[\s,]+/), function (_, value) {
      value = $.trim(value);
      if (value && !seen[value]) {
        values.push({date: value});
        seen[value] = true;
      }
    });
    return values;
  }

  function editorHtml() {
    if (state.selectedIndex == null || !state.draft) {
      return [
        '<div class="scheduler-editor-head">',
          '<div>',
            '<h2 class="scheduler-editor-title">Schedule details</h2>',
            '<p class="scheduler-editor-kicker">Select a block, or click a time in the calendar to create one.</p>',
          '</div>',
        '</div>',
        '<p class="scheduler-empty">No schedule selected.</p>'
      ].join('');
    }

    var draft = state.draft;
    var isWeekly = draft.mode === 'weekly';
    var chips = [];
    for (var day = 0; day < 7; day += 1) {
      chips.push(
        '<button class="scheduler-day-chip' + ($.inArray(day, draft.days) >= 0 ? ' is-selected' : '') + '" ' +
          'type="button" data-day="' + day + '" aria-label="' + DAY_NAMES[day] + '" aria-pressed="' + ($.inArray(day, draft.days) >= 0) + '">' + DAY_NAMES[day].charAt(0) + '</button>'
      );
    }

    return [
      '<div class="scheduler-editor-head">',
        '<div>',
          '<h2 class="scheduler-editor-title">' + (draft.isNew ? 'New schedule' : 'Edit schedule') + '</h2>',
          '<p class="scheduler-editor-kicker">' + (isWeekly ? 'Weekly rule' : 'Advanced CRON rule') + '</p>',
        '</div>',
        '<button class="scheduler-icon-btn scheduler-close-editor" type="button" aria-label="Close schedule details">×</button>',
      '</div>',
      '<form class="scheduler-form" novalidate>',
        '<label class="scheduler-field">',
          '<span class="scheduler-label">Name</span>',
          '<input name="name" type="text" required value="' + escapeHtml(draft.name) + '">',
        '</label>',
        '<label class="scheduler-check">',
          '<input name="enabled" type="checkbox"' + (draft.enabled !== false ? ' checked' : '') + '>',
          '<span>Enabled</span>',
        '</label>',
        (isWeekly ? [
          '<div class="scheduler-inline-fields">',
            '<label class="scheduler-field">',
              '<span class="scheduler-label">Time</span>',
              '<input name="time" type="time" step="900" required value="' + formatTime(draft.hour, draft.minute) + '">',
            '</label>',
            '<label class="scheduler-field">',
              '<span class="scheduler-label">Timezone</span>',
              '<input name="timezone" type="text" value="' + escapeHtml(draft.timezone) + '" placeholder="Host timezone">',
            '</label>',
          '</div>',
          '<fieldset class="scheduler-fieldset">',
            '<legend>Repeats</legend>',
            '<div class="scheduler-day-chips">' + chips.join('') + '</div>',
          '</fieldset>'
        ].join('') : [
          '<label class="scheduler-field">',
            '<span class="scheduler-label">CRON expression</span>',
            '<input name="cron" type="text" required value="' + escapeHtml(draft.cron) + '" placeholder="0 9 * * MON-FRI">',
            '<p class="scheduler-help scheduler-cron-help">Checking this rule with the Nodel host.</p>',
          '</label>',
          '<label class="scheduler-field">',
            '<span class="scheduler-label">Timezone</span>',
            '<input name="timezone" type="text" value="' + escapeHtml(draft.timezone) + '" placeholder="Host timezone">',
          '</label>'
        ].join('')),
        '<label class="scheduler-field">',
          '<span class="scheduler-label">Event name</span>',
          '<input name="event" type="text" required value="' + escapeHtml(draft.event) + '" placeholder="Museum Open">',
        '</label>',
        '<label class="scheduler-field">',
          '<span class="scheduler-label">Event argument</span>',
          '<input name="argument" type="text" value="' + escapeHtml(draft.argument) + '" placeholder="Optional">',
        '</label>',
        '<label class="scheduler-field">',
          '<span class="scheduler-label">Exception dates</span>',
          '<textarea name="exceptions" placeholder="2026-12-25">' + escapeHtml(draft.exceptionText) + '</textarea>',
          '<p class="scheduler-help">One YYYY-MM-DD date per line. Scheduled runs are skipped on these dates.</p>',
        '</label>',
        '<label class="scheduler-field">',
          '<span class="scheduler-label">Notes</span>',
          '<textarea name="notes" placeholder="Purpose or operational context">' + escapeHtml(draft.notes) + '</textarea>',
        '</label>',
        (isWeekly ? [
          '<label class="scheduler-field">',
            '<span class="scheduler-label">Generated CRON</span>',
            '<input name="generatedCron" type="text" readonly value="' + escapeHtml(buildWeeklyCron(draft)) + '">',
            '<p class="scheduler-help scheduler-cron-help">Checking this rule with the Nodel host.</p>',
          '</label>'
        ].join('') : ''),
        '<div class="scheduler-editor-actions">',
          '<button class="scheduler-btn scheduler-btn-danger scheduler-delete" type="button">Delete</button>',
          '<div class="scheduler-editor-actions-right">',
            '<button class="scheduler-btn scheduler-cancel" type="button">Cancel</button>',
            '<button class="scheduler-btn scheduler-btn-primary" type="submit">Keep changes</button>',
          '</div>',
        '</div>',
      '</form>'
    ].join('');
  }

  function renderEditor(openOnCompact) {
    var editor = $('.scheduler-editor');
    editor.html(editorHtml()).scrollTop(0);
    if (state.draft) scheduleDraftValidation(true);
    if (openOnCompact) {
      editor.add('.scheduler-editor-backdrop').addClass('is-open');
    }
  }

  function setEditorCommitting(value) {
    state.editorCommitting = value;
    var form = $('.scheduler-form');
    form.attr('aria-busy', value ? 'true' : 'false');
    form.find('input, textarea, .scheduler-day-chip, .scheduler-delete').prop('disabled', value);
    form.find('[type="submit"]').prop('disabled', value).text(value ? 'Checking…' : 'Keep changes');
  }

  function closeEditor() {
    startEditorSession();
    state.selectedIndex = null;
    state.draft = null;
    $('.scheduler-editor, .scheduler-editor-backdrop').removeClass('is-open');
    renderEditor(false);
    renderCalendar();
  }

  function selectSchedule(index, openOnCompact) {
    if (index < 0 || index >= state.schedules.length) return;
    startEditorSession();
    state.selectedIndex = index;
    state.draft = editorDraft(state.schedules[index]);
    renderCalendar();
    renderEditor(openOnCompact !== false);
  }

  function createSchedule(day, minutes) {
    startEditorSession();
    state.selectedIndex = state.schedules.length;
    state.draft = makeNewSchedule(day, minutes);
    state.draft.isNew = true;
    renderCalendar();
    renderEditor(true);
  }

  function syncDraftFromForm() {
    if (!state.draft) return;
    var form = $('.scheduler-form');
    state.draft.name = form.find('[name="name"]').val();
    state.draft.enabled = form.find('[name="enabled"]').prop('checked');
    state.draft.event = form.find('[name="event"]').val();
    state.draft.argument = form.find('[name="argument"]').val();
    state.draft.timezone = form.find('[name="timezone"]').val();
    state.draft.exceptionText = form.find('[name="exceptions"]').val();
    state.draft.notes = form.find('[name="notes"]').val();

    if (state.draft.mode === 'weekly') {
      var time = parseTime(form.find('[name="time"]').val());
      if (time) {
        state.draft.hour = time.hour;
        state.draft.minute = time.minute;
      }
    } else {
      state.draft.cron = form.find('[name="cron"]').val();
    }
  }

  function draftToSchedule() {
    syncDraftFromForm();
    var draft = state.draft;
    return normaliseSchedule({
      enabled: draft.enabled,
      name: $.trim(draft.name),
      cron: draft.mode === 'weekly' ? buildWeeklyCron(draft) : $.trim(draft.cron),
      event: $.trim(draft.event),
      argument: draft.argument,
      timezone: $.trim(draft.timezone),
      exceptions: exceptionValues(draft.exceptionText),
      notes: draft.notes
    });
  }

  function validateDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    var parts = value.split('-');
    var date = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    return date.getFullYear() === parseInt(parts[0], 10) &&
      date.getMonth() === parseInt(parts[1], 10) - 1 &&
      date.getDate() === parseInt(parts[2], 10);
  }

  function validateEditor() {
    syncDraftFromForm();
    var errors = [];
    var form = $('.scheduler-form');
    form.find('input, textarea').removeClass('has-error');
    form.find('.scheduler-error').remove();

    function fieldError(name, message) {
      var field = form.find('[name="' + name + '"]');
      field.addClass('has-error').after('<p class="scheduler-error">' + escapeHtml(message) + '</p>');
      errors.push(message);
    }

    if (!$.trim(state.draft.name)) fieldError('name', 'Enter a schedule name.');
    if (!$.trim(state.draft.event)) fieldError('event', 'Enter the event this schedule should emit.');
    if (state.draft.mode === 'weekly') {
      if (!parseTime(form.find('[name="time"]').val())) fieldError('time', 'Enter a valid time.');
      if (!state.draft.days.length) errors.push('Choose at least one weekday.');
    } else if (!$.trim(state.draft.cron)) {
      fieldError('cron', 'Enter a CRON expression.');
    }

    $.each(exceptionValues(state.draft.exceptionText), function (_, item) {
      if (!validateDate(item.date)) errors.push('Exception date ' + item.date + ' is invalid.');
    });

    var name = $.trim(state.draft.name).toLowerCase();
    $.each(state.schedules, function (index, schedule) {
      if (index !== state.selectedIndex && $.trim(schedule.name).toLowerCase() === name && name) {
        errors.push('Schedule names must be unique.');
      }
    });

    if (errors.length) {
      showToast(errors[0], true);
      return false;
    }
    return true;
  }

  function cronInfo(expression, timezone) {
    return $.getJSON('REST/cron', {
      expression: expression,
      timezone: timezone || undefined
    });
  }

  function currentDraftCron() {
    if (!state.draft) return null;
    return {
      expression: state.draft.mode === 'weekly' ? buildWeeklyCron(state.draft) : state.draft.cron,
      timezone: $.trim(state.draft.timezone || '')
    };
  }

  function scheduleDraftValidation(immediate) {
    if (!state.draft) return;
    clearTimeout(state.validationTimer);
    state.validationTimer = null;
    var token = ++state.validationToken;
    var session = state.editorSession;
    $('.scheduler-cron-help').removeClass('scheduler-error').addClass('scheduler-help')
      .text('Checking this rule with the Nodel host.');

    if (immediate) {
      validateDraftCron(session, token);
    } else {
      state.validationTimer = setTimeout(function () {
        state.validationTimer = null;
        validateDraftCron(session, token);
      }, VALIDATION_DELAY);
    }
  }

  function validateDraftCron(session, token) {
    var expected = currentDraftCron();
    if (!expected) return;
    cronInfo(expected.expression, expected.timezone).done(function (info) {
      if (!state.draft || state.editorSession !== session || state.validationToken !== token) return;
      var current = currentDraftCron();
      if (!current || current.expression !== expected.expression || current.timezone !== expected.timezone) return;
      var help = $('.scheduler-cron-help');
      if (info.valid) {
        var text = info.description || 'Valid CRON expression';
        if (info.next) {
          text += ' · next ' + moment.parseZone(info.next).format('ddd D MMM, h:mm a Z');
          if (info.timeZone) text += ' (' + info.timeZone + ')';
        }
        help.removeClass('scheduler-error').addClass('scheduler-help').text(text);
      } else {
        help.removeClass('scheduler-help').addClass('scheduler-error').text(info.error || 'Invalid CRON expression');
      }
    }).fail(function () {
      if (!state.draft || state.editorSession !== session || state.validationToken !== token) return;
      $('.scheduler-cron-help').removeClass('scheduler-error').addClass('scheduler-help')
        .text('The host could not validate this rule.');
    });
  }

  function commitEditor() {
    if (state.editorCommitting || !validateEditor()) return;
    var schedule = draftToSchedule();
    var session = state.editorSession;
    var token = ++state.editorCommitToken;
    var baseRevision = state.revision;
    var targetIndex = state.selectedIndex;
    var isNew = !!state.draft.isNew;
    state.validationToken += 1;
    clearTimeout(state.validationTimer);
    setEditorCommitting(true);

    cronInfo(schedule.cron, schedule.timezone).done(function (info) {
      if (state.editorSession !== session || state.editorCommitToken !== token) return;
      if (state.revision !== baseRevision) {
        setEditorCommitting(false);
        showToast('Schedules changed while this rule was being checked. Review it and try again.', true);
        return;
      }
      if (!info.valid) {
        setEditorCommitting(false);
        showToast(info.error || 'The CRON expression is invalid.', true);
        return;
      }
      if (isNew) state.schedules.push(schedule);
      else state.schedules[targetIndex] = schedule;
      markSchedulesChanged();
      closeEditor();
    }).fail(function () {
      if (state.editorSession !== session || state.editorCommitToken !== token) return;
      setEditorCommitting(false);
      showToast('The host could not validate this rule.', true);
    });
  }

  function deleteSelected() {
    if (state.selectedIndex == null) return;
    if (!state.draft.isNew) {
      state.schedules.splice(state.selectedIndex, 1);
      markSchedulesChanged();
    }
    closeEditor();
  }

  function validateAllSchedules(schedules) {
    var deferreds = [];
    var failures = [];
    $.each(schedules, function (_, schedule) {
      var request = cronInfo(schedule.cron, schedule.timezone).done(function (info) {
        if (!info.valid) failures.push((schedule.name || 'Unnamed schedule') + ': ' + (info.error || 'invalid CRON expression'));
      }).fail(function () {
        failures.push((schedule.name || 'Unnamed schedule') + ': the host could not validate this rule');
      });
      deferreds.push(request);
    });

    return $.when.apply($, deferreds).then(function () {
      return failures;
    }, function () {
      return failures.length ? failures : ['The host could not validate the schedules.'];
    });
  }

  function saveAndApply() {
    if (state.saving || !state.dirty) return;
    var saveSnapshot = clone(state.schedules);
    var saveRevision = state.revision;
    state.saving = true;
    updateToolbar();

    validateAllSchedules(saveSnapshot).done(function (failures) {
      if (failures.length) {
        state.saving = false;
        updateToolbar();
        showToast(failures[0], true);
        return;
      }

      $.getJSON('REST/params').done(function (currentParams) {
        var currentSchedules = currentParams.Schedules || [];
        if (stable(currentSchedules) !== stable(state.originalSchedules)) {
          state.saving = false;
          updateToolbar();
          showToast('Schedules changed in another tab. Reload before saving.', true);
          return;
        }

        var payload = $.extend(true, {}, currentParams);
        payload.Schedules = clone(saveSnapshot);
        $.postJSON('REST/params/save', JSON.stringify(payload)).done(function () {
          state.originalSchedules = clone(saveSnapshot);
          $.postJSON('REST/actions/ApplySavedSchedules/call', '{}').done(function () {
            state.saving = false;
            if (state.revision === saveRevision) {
              setDirty(false);
              showToast('Schedules saved and applied.', false);
            } else {
              setDirty(true);
              showToast('Saved and applied. Newer edits are still waiting to be saved.', false);
            }
          }).fail(function (response) {
            state.saving = false;
            setDirty(true);
            showToast('Schedules were saved, but the node could not apply them.', true);
            if (response && response.responseText) console.error(response.responseText);
          });
        }).fail(function (response) {
          state.saving = false;
          updateToolbar();
          showToast('The host could not save the schedules.', true);
          if (response && response.responseText) console.error(response.responseText);
        });
      }).fail(function () {
        state.saving = false;
        updateToolbar();
        showToast('The host could not check for newer schedule changes.', true);
      });
    });
  }

  function beginDrag(event) {
    var block = $(event.currentTarget);
    var index = parseInt(block.data('index'), 10);
    var weekly = parseWeeklyCron(state.schedules[index].cron);
    if (!weekly || weekly.days.length !== 1) {
      event.preventDefault();
      return;
    }
    state.drag = {
      index: index
    };
    block.addClass('is-dragging');
    event.originalEvent.dataTransfer.effectAllowed = 'move';
    event.originalEvent.dataTransfer.setData('text/plain', String(state.drag.index));
  }

  function finishDrag(event) {
    event.preventDefault();
    if (!state.drag) return;
    var lane = $(event.currentTarget);
    var targetDay = parseInt(lane.data('day'), 10);
    var offset = event.originalEvent.clientY - lane[0].getBoundingClientRect().top;
    var minutes = roundMinutes((offset / HOUR_HEIGHT) * 60);
    var schedule = state.schedules[state.drag.index];
    var weekly = parseWeeklyCron(schedule.cron);

    if (weekly && weekly.days.length === 1) {
      var nextCron = String(minutes % 60) + ' ' + String(Math.floor(minutes / 60)) + ' * * ' + compactCronDays([targetDay]);
      if (nextCron === schedule.cron) {
        state.drag = null;
        $('.scheduler-block').removeClass('is-dragging');
        return;
      }
      schedule.cron = nextCron;
      markSchedulesChanged();
      selectSchedule(state.drag.index, false);
      showToast('Schedule moved. Save and apply when ready.', false);
    }
    state.drag = null;
    $('.scheduler-block').removeClass('is-dragging');
  }

  function bindEvents() {
    var page = $('.page[data-section="Overview"]');

    page.on('click', '#scheduler-new', function () {
      createSchedule((new Date().getDay() + 6) % 7, 9 * 60);
    });

    page.on('click', '#scheduler-save', saveAndApply);

    page.on('click', '.scheduler-block, .scheduler-edit-advanced', function (event) {
      event.stopPropagation();
      if (state.drag) return;
      selectSchedule(parseInt($(this).data('index'), 10), true);
    });

    page.on('click', '.scheduler-day-lane', function (event) {
      if ($(event.target).closest('.scheduler-block').length) return;
      var lane = this;
      var minutes = ((event.clientY - lane.getBoundingClientRect().top) / HOUR_HEIGHT) * 60;
      createSchedule(parseInt($(lane).data('day'), 10), minutes);
    });

    page.on('keydown', '.scheduler-day-lane', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        createSchedule(parseInt($(this).data('day'), 10), 9 * 60);
      }
    });

    page.on('click', '.scheduler-close-editor, .scheduler-cancel, .scheduler-editor-backdrop', closeEditor);
    page.on('click', '.scheduler-delete', deleteSelected);

    page.on('click', '.scheduler-day-chip', function () {
      var day = parseInt($(this).data('day'), 10);
      var position = $.inArray(day, state.draft.days);
      if (position >= 0) state.draft.days.splice(position, 1);
      else state.draft.days.push(day);
      state.draft.days.sort();
      $(this).toggleClass('is-selected', $.inArray(day, state.draft.days) >= 0)
        .attr('aria-pressed', $.inArray(day, state.draft.days) >= 0);
      $('.scheduler-field [name="generatedCron"]').val(buildWeeklyCron(state.draft));
      scheduleDraftValidation(false);
    });

    page.on('input change', '.scheduler-form input, .scheduler-form textarea', function () {
      syncDraftFromForm();
      if (state.draft && state.draft.mode === 'weekly') {
        $('.scheduler-field [name="generatedCron"]').val(buildWeeklyCron(state.draft));
      }
      if ($(this).is('[name="time"], [name="cron"], [name="timezone"]')) scheduleDraftValidation(false);
    });

    page.on('submit', '.scheduler-form', function (event) {
      event.preventDefault();
      commitEditor();
    });

    page.on('dragstart', '.scheduler-block', beginDrag);
    page.on('dragend', '.scheduler-block', function () {
      state.drag = null;
      $('.scheduler-block').removeClass('is-dragging');
    });
    page.on('dragover', '.scheduler-day-lane', function (event) {
      if (!state.drag) return;
      event.preventDefault();
      event.originalEvent.dataTransfer.dropEffect = 'move';
    });
    page.on('drop', '.scheduler-day-lane', finishDrag);

    $(document).on('keydown.schedulerCalendar', function (event) {
      if (event.key === 'Escape' && state.draft) closeEditor();
    });

    page.on('scroll', '.scheduler-week-scroll', function () {
      if (!state.calendarPositioned) return;
      state.calendarScrollTop = this.scrollTop;
      state.calendarScrollLeft = this.scrollLeft;
    });

    $(window).on('beforeunload.schedulerCalendar', function () {
      if (state.dirty) return 'You have unsaved schedule changes.';
    });
  }

  function loadSchedules() {
    $.getJSON('REST/params').done(function (params) {
      state.originalSchedules = clone(params.Schedules || []);
      state.schedules = $.map(params.Schedules || [], normaliseSchedule);
      state.revision = 0;
      state.dirty = false;
      renderCalendar();
      renderEditor(false);
      cronInfo('0 0 * * *', '').done(function (info) {
        state.hostTimezone = info.timeZone || info.timezone || '';
        renderCalendar();
      });
    }).fail(function () {
      $('.scheduler-app').html('<div class="scheduler-loading">The host could not load the schedules.</div>');
    });
  }

  $(document).ready(function () {
    var page = $('.page[data-section="Overview"]');
    if (!page.length) return;
    page.html(pageHtml());
    bindEvents();
    loadSchedules();
  });
})(
  typeof window !== 'undefined' ? window.jQuery : null,
  typeof window !== 'undefined' ? window.SchedulerCalendarModel : null
);
