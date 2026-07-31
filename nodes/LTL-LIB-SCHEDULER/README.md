# Scheduler

This example node emits named local events from standard five-field UNIX CRON schedules.

## Nodel version requirement

> [!WARNING]
> This recipe requires a pre-release Nodel build. It will not run on a host that lacks the first-class CRON API proposed in [museumsvictoria/nodel#411](https://github.com/museumsvictoria/nodel/issues/411).

Until that work lands upstream, use a host built from [scroix/nodel#45](https://github.com/scroix/nodel/pull/45). The recipe needs the managed `Cron` API plus `cron_validate`, `cron_next` and `REST/cron`.

The recipe deliberately contains no parser or scheduling engine. The nodehost owns validation, timezones, daylight-saving transitions, delayed-run handling and lifecycle cleanup.

## Configure a schedule

Open **Parameters**, then add or edit an entry under **Schedules**:

- **Enabled** registers the job when the node starts.
- **Name** is a unique human-readable label used by status and the **Run schedule now** action.
- **CRON expression** uses `minute hour day-of-month month day-of-week`, for example `30 8 * * MON-FRI`.
- **Event name** becomes a local event that another node can subscribe to as a remote event.
- **Event argument** is optional. A blank value emits the event without an argument.
- **Timezone** is an optional IANA timezone such as `Australia/Melbourne`; blank uses the host timezone.
- **Exception dates** are `YYYY-MM-DD` dates in that schedule's timezone.
- **Notes** capture purpose or operational context.

The supplied `nodeConfig.json` contains two disabled examples. When creating the node from this recipe, include the recipe configuration to pre-populate them. After saving Parameters, use **Apply saved schedules** in Actions (or restart the node). Restarting removes the previous managed jobs and registers the current list.

The optional Mk2 dashboard in `content/frontend.xml` opens on a custom recurring-week calendar. Click a time to create a schedule, select a block to edit it, or drag a single-day block to move its weekday or time. Repeating blocks are edited in the details panel so dragging one visible occurrence cannot accidentally rewrite every occurrence. Overlapping rules are laid out side by side.

The grid is a wall-clock view: each block is positioned in its own rule's timezone, and the timezone is shown on the block. A blank timezone means the Nodel host timezone, not the browser timezone. CRON descriptions, next-run offsets and timezone errors come from the host's `REST/cron` endpoint. Simple weekly rules are converted to five-field CRON; expressions that cannot be represented safely in the grid remain under **Advanced CRON rules**.

**Save and apply** validates an immutable snapshot, checks that Parameters have not changed in another tab, saves the full `Schedules` array, then calls **Apply saved schedules**. Edits made while that operation is in flight remain visibly unsaved for the next save. Nodel's native **Parameters** view remains the complete fallback editor.

Runtime health, validation, next/last execution and last-error information are published as local events for Nodel's native views, API and bindings. **Run schedule now** remains available in Actions for testing a named rule. The recipe deliberately does not replace `content/index.xml`; select `frontend.xml` from **Nav / UI** or open `/frontend.xml` directly.

## Integrating scheduled events

The Scheduler is an event source. It does not select a target node or call remote actions itself. Each receiving node must declare a remote event, bind it to the corresponding Scheduler event, and decide how to handle its argument.

For example, two schedule entries can share the event name `Power`:

| Name | CRON expression | Event name | Event argument |
| --- | --- | --- | --- |
| Weekday opening | `0 9 * * MON-FRI` | `Power` | `On` |
| Weekday closing | `0 17 * * MON-FRI` | `Power` | `Off` |

A receiving recipe can forward that signal to its existing `Power` action:

```python
def remote_event_ScheduledPower(arg):
  '''{"title":"Scheduled Power","group":"Automation","schema":{"type":"string","enum":["On","Off"]}}'''
  action = lookup_local_action('Power')
  if action is None:
    console.warn('Scheduled Power was received, but this node has no Power action.')
    return
  action.call(arg)
```

After adding the receiver, open that node's **Remote bindings** and bind **Scheduled Power** to the Scheduler node's **Power** event. The same pattern works for common string-valued actions such as `Muting`, `Source` and `Preset`. Leave **Event argument** blank only when the receiving action expects no argument.

### Group nodes

For a Group, put the receiver above on the top-level Group and forward it to the ordinary `Power` or `Muting` action. The Group then propagates the requested state through its configured members and continues to aggregate their resultant state as usual.

The current Group recipe does not include this upstream automation receiver by default. Its existing remote events collect member status; they do not call the Group's own actions. Do not configure the Scheduler as a Group member, and do not bind its string argument directly to `Power Extended` or `Muting Extended`, which expect object arguments.

### Operational boundaries

- The connection is one-way. The Scheduler does not inspect the receiving node's state, retry failed actions or confirm that members reached the requested state.
- Occurrences missed while the node or host is stopped are not reconstructed after restart.
- **Run schedule now** emits the real configured event even when the schedule is disabled or today is an exception date. Test bindings with the same care as the corresponding manual action.
- If several schedules emit the same event at the same instant, consumers should not rely on their execution order.

## Runtime behaviour

- Standard five-field UNIX expressions are supported, including named months and weekdays. Sunday may be `0`, `7`, or `SUN`.
- A late occurrence fires once while the host remains running; multiple missed occurrences are coalesced.
- A node or host restart looks forward only and does not replay missed occurrences.
- An occurrence inside a daylight-saving gap is skipped. An overlapping wall-clock occurrence fires once on its first pass.
- Exception dates suppress only scheduled emissions. **Run schedule now** intentionally ignores Enabled and exception dates.

## Migrating from the retired recipes

Move each row from `(retired)/scheduler` or `(retired)/advscheduler` into **Schedules**:

1. Add a unique **Name** and choose **Enabled**.
2. Copy `cron` to **CRON expression**.
3. Rename `signal` to **Event name**.
4. Rename `args` to **Event argument** when present.
5. Rename `except` to **Exception dates** and keep each date in `YYYY-MM-DD` form.
6. Optionally add an IANA **Timezone** and retain the old note under **Notes**.

Do not copy the bundled `apscheduler` directory. External schedulers, `eval()`, Office 365, Exchange, operating-system cron and Windows Task Scheduler are not used by this recipe.

## Tests

The calendar model tests need only Node.js. Browser tests use a disposable Scheduler node, restore its original Parameters after the run, and accept host, node slug and node name through `SCHEDULER_HOST_URL`, `SCHEDULER_NODE_SLUG` and `SCHEDULER_NODE_NAME`.

```sh
cd Scheduler/tests
npm install
npm test
npm run install:browsers
npm run test:e2e
```
