# One-way Scheduler inputs for a Group node.
#
# Bind "Scheduled Power" and "Scheduled Muting" to string-valued Scheduler
# events. The adapter forwards On/Off into the Group's ordinary actions, which
# then use the Group's existing member propagation.


def _call_group_action(action_name, arg):
  if arg != 'On' and arg != 'Off':
    console.warn("Scheduled %s ignored unsupported state '%s'." % (action_name, arg))
    return

  action = lookup_local_action(action_name)
  if action is None:
    console.warn('Scheduled %s was received, but this Group has no %s action.' % (
      action_name, action_name))
    return

  action.call(arg)


def remote_event_ScheduledPower(arg):
  '''{"title":"Scheduled Power","group":"Automation","schema":{"type":"string","enum":["On","Off"]}}'''
  _call_group_action('Power', arg)


def remote_event_ScheduledMuting(arg):
  '''{"title":"Scheduled Muting","group":"Automation","schema":{"type":"string","enum":["On","Off"]}}'''
  _call_group_action('Muting', arg)
