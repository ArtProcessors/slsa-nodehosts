'''
Scheduler

Creates named local events from five-field UNIX CRON schedules. Scheduling,
timezone, daylight-saving and missed-run behaviour are provided by the Nodel
host's Cron API; this recipe only manages configuration and event emission.

Requires a Nodel host with first-class CRON support (museumsvictoria/nodel#411).
'''

import re


param_Schedules = Parameter({
  'title': 'Schedules',
  'desc': 'Each enabled entry emits a named local event on its CRON schedule. Save parameters, then restart or use Apply saved schedules.',
  'group': 'Scheduler',
  'order': 1,
  'schema': {
    'type': 'array',
    'items': {
      'type': 'object',
      'title': 'Schedule',
      'properties': {
        'enabled': {
          'type': 'boolean',
          'title': 'Enabled',
          'default': True,
          'order': 1
        },
        'name': {
          'type': 'string',
          'title': 'Name',
          'desc': 'A unique, readable name used in status and manual test controls.',
          'required': True,
          'order': 2
        },
        'cron': {
          'type': 'string',
          'format': 'cron',
          'title': 'CRON expression',
          'desc': 'Five fields: minute, hour, day of month, month, day of week.',
          'hint': '0 9 * * MON-FRI',
          'required': True,
          'order': 3
        },
        'event': {
          'type': 'string',
          'format': 'event',
          'title': 'Event name',
          'desc': 'The local event to emit. Bind it to actions on this or another node.',
          'hint': 'Museum Open',
          'required': True,
          'order': 4
        },
        'argument': {
          'type': 'string',
          'title': 'Event argument (optional)',
          'desc': 'Leave blank to emit the event without an argument.',
          'order': 5
        },
        'timezone': {
          'type': 'string',
          'title': 'Timezone (optional)',
          'desc': 'An IANA timezone such as Australia/Melbourne. Leave blank to use the host timezone.',
          'hint': 'Australia/Melbourne',
          'order': 6
        },
        'exceptions': {
          'type': 'array',
          'title': 'Exception dates',
          'desc': 'Local dates on which this schedule should not emit.',
          'order': 7,
          'items': {
            'type': 'object',
            'title': 'Exception',
            'properties': {
              'date': {
                'type': 'string',
                'format': 'date',
                'title': 'Date',
                'desc': 'YYYY-MM-DD in the schedule timezone.',
                'required': True,
                'order': 1
              }
            }
          }
        },
        'notes': {
          'type': 'string',
          'format': 'long',
          'title': 'Notes',
          'desc': 'Purpose, owner or operational context for this schedule.',
          'order': 8
        }
      }
    }
  }
})


local_event_SchedulerHealthy = LocalEvent({
  'title': 'Scheduler healthy',
  'group': 'Scheduler status',
  'order': 1,
  'schema': {'type': 'boolean'}
})

local_event_SchedulerStatus = LocalEvent({
  'title': 'Scheduler status',
  'group': 'Scheduler status',
  'order': 2,
  'schema': {'type': 'string'}
})

local_event_NextExecution = LocalEvent({
  'title': 'Next execution',
  'group': 'Scheduler status',
  'order': 3,
  'schema': {'type': 'string'}
})

local_event_LastExecution = LocalEvent({
  'title': 'Last execution',
  'group': 'Scheduler status',
  'order': 4,
  'schema': {'type': 'string'}
})

local_event_LastEvent = LocalEvent({
  'title': 'Last event',
  'group': 'Scheduler status',
  'order': 5,
  'schema': {'type': 'string'}
})

local_event_ValidationErrors = LocalEvent({
  'title': 'Validation errors',
  'group': 'Scheduler status',
  'order': 6,
  'schema': {'type': 'string'}
})

local_event_LastError = LocalEvent({
  'title': 'Last error',
  'group': 'Scheduler status',
  'order': 7,
  'schema': {'type': 'string'}
})


_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_jobs = {}
_schedule_rows = []
_validation_errors = []
_runtime_error = None


def _text(value):
  if value is None:
    return ''
  return str(value).strip()


def _is_mapping(value):
  return value is not None and hasattr(value, 'get')


def _format_instant(value):
  if value is None:
    return 'Not scheduled'
  return value.toString('EEE d MMM yyyy, HH:mm:ss ZZZ')


def _parse_exception_dates(items):
  result = set()
  if items is None:
    return result

  for item in items:
    if _is_mapping(item):
      value = _text(item.get('date'))
    else:
      value = _text(item)

    if not value:
      continue
    if not _DATE_RE.match(value):
      raise ValueError("Exception date '%s' must use YYYY-MM-DD." % value)

    try:
      parsed = date_parse(value)
    except Exception, e:
      raise ValueError("Exception date '%s' is invalid: %s" % (value, e))

    if parsed is None or parsed.toString('yyyy-MM-dd') != value:
      raise ValueError("Exception date '%s' is invalid." % value)
    result.add(value)

  return result


def _normalise_schedule(entry, position, seen_names):
  if not _is_mapping(entry):
    raise ValueError('Schedule %d must be an object.' % position)

  name = _text(entry.get('name'))
  expression = _text(entry.get('cron'))
  event_name = _text(entry.get('event'))
  timezone = _text(entry.get('timezone')) or None
  notes = _text(entry.get('notes'))
  argument = entry.get('argument')
  enabled_value = entry.get('enabled')
  enabled = True if enabled_value is None else bool(enabled_value)

  if not name:
    raise ValueError('Schedule %d has no name.' % position)
  name_key = name.lower()
  if name_key in seen_names:
    raise ValueError("Schedule name '%s' is duplicated." % name)
  seen_names.add(name_key)

  if not expression:
    raise ValueError("Schedule '%s' has no CRON expression." % name)
  validation_error = cron_validate(expression)
  if validation_error:
    raise ValueError("Schedule '%s': %s" % (name, validation_error))

  if not event_name:
    raise ValueError("Schedule '%s' has no event name." % name)

  for reserved in [
      local_event_SchedulerHealthy,
      local_event_SchedulerStatus,
      local_event_NextExecution,
      local_event_LastExecution,
      local_event_LastEvent,
      local_event_ValidationErrors,
      local_event_LastError]:
    if lookup_local_event(event_name) == reserved:
      raise ValueError("Schedule '%s' uses reserved event name '%s'." % (name, event_name))

  try:
    next_execution = cron_next(expression, timezone)
  except Exception, e:
    raise ValueError("Schedule '%s': %s" % (name, e))

  if next_execution is None:
    raise ValueError("Schedule '%s' has no future execution." % name)

  return {
    'enabled': enabled,
    'name': name,
    'cron': expression,
    'event': event_name,
    'argument': argument,
    'timezone': timezone,
    'exceptions': _parse_exception_dates(entry.get('exceptions')),
    'notes': notes
  }


def _record_runtime_error(message):
  global _runtime_error
  _runtime_error = message
  console.error(message)
  local_event_LastError.emitIfDifferent(message)
  local_event_LastError.persistNow()


def _record_operator_error(message):
  console.warn(message)
  local_event_LastError.emitIfDifferent(message)
  local_event_LastError.persistNow()


def _clear_runtime_error():
  global _runtime_error
  _runtime_error = None


class ScheduleJob(object):

  def __init__(self, config, event):
    self.config = config
    self.event = event
    def scheduled_fire():
      self.fire(False)
    self._callback = scheduled_fire
    self.cron = Cron(
      self._callback,
      config['cron'],
      config['timezone'],
      stopped=not config['enabled'])

  def fire(self, manual):
    fired_at = date_now() if manual else self.cron.getLastFired()
    if fired_at is None:
      fired_at = date_now()

    local_date = fired_at.toString('yyyy-MM-dd')
    if not manual and local_date in self.config['exceptions']:
      message = "%s skipped on exception date %s" % (self.config['name'], local_date)
      console.info(message)
      local_event_LastEvent.emit(message)
      _publish_status()
      return

    try:
      argument = self.config['argument']
      if argument is None or argument == '':
        self.event.emit()
      else:
        self.event.emit(argument)
    except Exception, e:
      _record_runtime_error("Schedule '%s' could not emit '%s': %s" % (
        self.config['name'], self.config['event'], e))
      _publish_status()
      return

    _clear_runtime_error()
    source = 'manual' if manual else 'scheduled'
    execution_text = _format_instant(fired_at)
    event_text = "%s emitted '%s' (%s)" % (
      self.config['name'], self.config['event'], source)
    if self.config['argument'] is not None and self.config['argument'] != '':
      event_text = "%s with argument '%s'" % (event_text, self.config['argument'])

    console.info('%s at %s' % (event_text, execution_text))
    local_event_LastEvent.emit(event_text)
    local_event_LastExecution.emit(execution_text)
    local_event_LastEvent.persistNow()
    local_event_LastExecution.persistNow()
    _publish_status()


def _get_or_create_target_event(config):
  event = lookup_local_event(config['event'])
  if event is None:
    event = create_local_event(config['event'], {
      'title': config['event'],
      'desc': "Emitted by Scheduler entry '%s'." % config['name'],
      'group': 'Scheduled events',
      'order': next_seq(),
      'schema': {'type': 'string', 'required': False}
    })
  return event


def _configure_schedules():
  global _jobs, _schedule_rows, _validation_errors

  _jobs = {}
  _schedule_rows = []
  _validation_errors = []

  schedules = param_Schedules or []
  seen_names = set()

  for position, entry in enumerate(schedules):
    display_name = 'Schedule %d' % (position + 1)
    if _is_mapping(entry) and _text(entry.get('name')):
      display_name = _text(entry.get('name'))

    try:
      config = _normalise_schedule(entry, position + 1, seen_names)
      event = _get_or_create_target_event(config)
      job = ScheduleJob(config, event)
      _jobs[config['name'].lower()] = job
      _schedule_rows.append({'name': config['name'], 'job': job, 'error': None})
    except Exception, e:
      message = str(e)
      _validation_errors.append(message)
      _schedule_rows.append({'name': display_name, 'job': None, 'error': message})
      console.error(message)


def _publish_status():
  active_count = 0
  next_execution = None

  for row in _schedule_rows:
    job = row['job']
    if job is None:
      continue

    if job.config['enabled']:
      active_count += 1
      job_next = job.cron.getNextExecution()
    else:
      job_next = None
    if job_next is not None:
      if next_execution is None or job_next.getMillis() < next_execution.getMillis():
        next_execution = job_next

  health_errors = list(_validation_errors)
  if _runtime_error:
    health_errors.append(_runtime_error)
  healthy = len(health_errors) == 0
  total_count = len(_schedule_rows)

  if total_count == 0:
    status = 'No schedules configured'
  else:
    status = '%d enabled / %d configured' % (active_count, total_count)

  if not healthy:
    status = '%s - %d error(s)' % (status, len(health_errors))

  local_event_SchedulerHealthy.emitIfDifferent(healthy)
  local_event_SchedulerStatus.emitIfDifferent(status)
  local_event_NextExecution.emitIfDifferent(_format_instant(next_execution))
  local_event_ValidationErrors.emitIfDifferent(
    '\n'.join(_validation_errors) if _validation_errors else 'No validation errors.')


@local_action({
  'title': 'Refresh schedule status',
  'group': 'Scheduler',
  'order': 1,
  'schema': {'type': 'null'}
})
def RefreshScheduleStatus(arg=None):
  _publish_status()


@local_action({
  'title': 'Run schedule now',
  'desc': 'Manually emits a configured schedule event, ignoring enabled state and exception dates.',
  'group': 'Scheduler',
  'order': 2,
  'schema': {'type': 'string'}
})
def RunScheduleNow(arg):
  name = _text(arg)
  job = _jobs.get(name.lower())
  if job is None:
    _record_operator_error("No valid schedule named '%s'." % name)
    return
  job.fire(True)


@local_action({
  'title': 'Apply saved schedules',
  'desc': 'Restarts this node so saved parameter changes replace the managed CRON jobs.',
  'group': 'Scheduler',
  'order': 3,
  'schema': {'type': 'null'}
})
def ApplySavedSchedules(arg=None):
  console.info('Applying saved schedule parameters by restarting this node.')
  call(lambda: _node.restart(), 0.5)


@after_main
def setup_schedules():
  _configure_schedules()
  local_event_LastExecution.emitIfDifferent(
    local_event_LastExecution.getArg() or 'No executions since this node was created.')
  local_event_LastEvent.emitIfDifferent(
    local_event_LastEvent.getArg() or 'No events emitted yet.')
  local_event_LastError.emitIfDifferent(
    local_event_LastError.getArg() or 'No errors recorded.')
  call(_publish_status, 0.25)


def main():
  console.info('Scheduler starting.')
