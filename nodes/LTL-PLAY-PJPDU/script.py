'''
For **NETIO PDU devices**, e.g. PowerCable 2PZ

_changelog_

 * r2 from automatic.com.au
'''

param_ipAddress = Parameter({ 'title': 'IP address', 'schema': {'type': 'string', 'hint': '(overrides bindings)' }})

param_Credentials = Parameter({ 'title': 'Credentials', 'schema': {'type': 'object', 'properties': {
  'username': {'type': 'string', 'order': 1},
  'password': {'type': 'string', 'format': 'password', 'order': 2}
}}})

local_event_IPAddress = LocalEvent({'order': next_seq(), 'schema': {'type': 'string'}})

def remote_event_IPAddress(newIP):
  if is_blank(param_ipAddress):
    currentIP = local_event_IPAddress.getArg()
    if currentIP != newIP:
      console.info('IP address change notified! current:%s new:%s' % (currentIP, newIP))
      local_event_IPAddress.emit(newIP)


def main():
  ipAddr = local_event_IPAddress.getArg() if is_blank(param_ipAddress) else param_ipAddress

  if is_blank(ipAddr):
    return console.warn('No IP address to use (yet?)')

  local_event_IPAddress.emit(ipAddr)

  if param_Credentials == None:
    console.warn('Credentials required')
    return
  
def tryInitOutput(output):
    # Output status : 0 - Power OFF, 1 - Power ON
    # populate UI related : action and event

    outputName = output['Name']
    outputId = output['ID']

    leState = lookup_local_event('Output %s' % outputId)
    if leState != None:
      return

    lePowerStatus = create_local_event('Output %s' % outputId, { 'title': '"%s"' % outputName, 'group': 'Outputs', 'order': next_seq(), 'schema': { 'type': 'string', 'enum': [ 'Off', 'On' ] }} )

    lePowerStatus.emit('Off' if output['State'] == 0 else 'On')

    def handler(arg):
      setValue(outputId, outputName, arg)

    laPowerStatus = create_local_action('Output %s' % outputId, handler, { 'title': '"%s"' % outputName, 'group': 'Outputs', 'order': next_seq(), 'schema': {'type': 'string', 'enum': [ 'Off', 'On', 'Short Off', 'Short On', 'Toggle']}})      

def try_init():
  # GET /netio.json
  url = 'http://%s/netio.json' % local_event_IPAddress.getArg()
  resp = get_url(url, username=param_Credentials['username'], password=param_Credentials['password'])
  resp = json_decode(resp)
  outputs = resp['Outputs']

  # check outputs
  for output in outputs:
    tryInitOutput(output)

timer_init = Timer(try_init, 60, 5)

def actionValueToKey(value):
  if value == 'Off':       return 0
  if value == 'On':        return 1
  if value == 'Short Off': return 2
  if value == 'Short On':  return 3
  if value == 'Toggle':    return 4
  # 5: no change
  # 6: ignored
  raise Exception('%s: not supported' % value)

def setValue(outputId, name, value):
  console.log('[setValue] called')
  console.log('[setValue] outputId: %d, name: %s, value: %s' % (outputId, name, value))

  url = 'http://%s/netio.json' % local_event_IPAddress.getArg()
  resp = get_url(url, method='POST', username=param_Credentials['username'], password=param_Credentials['password'], 
                 post=json_encode({ 'Outputs': [{"ID": outputId, "Action": actionValueToKey(value)}] }))
  
  resp = json_decode(resp)
  outputs = resp['Outputs']

  for output in outputs:
    if output['ID'] == outputId:
      console.log(output)
      e = lookup_local_event('Output %s' % outputId)
      if e != None:
        e.emit('Off' if output['State'] == 0 else 'On')
        
      return

local_event_Status = LocalEvent({ 'group': 'Status', 'order': 1, 'schema': { 'type': 'object', 'properties': {
                                    'level': { 'type': 'integer', 'order': 1 },
                                    'message': {'type': 'string', 'order': 2 }}}})

local_event_LastLanding = LocalEvent({ 'group': 'Status', 'order': 2, 'schema': { 'type': 'string' }})

def statusCheck():
  now = date_now()
  try:
    url = 'http://%s/netio.json' % local_event_IPAddress.getArg()

    resp = get_url(url, username=param_Credentials['username'], password=param_Credentials['password'])
    resp = json_decode(resp)
    outputs = resp['Outputs']

    # check outputs
    for output in outputs:
      lookup_local_event('Output %s' % output['ID']).emit('Off' if output['State'] == 0 else 'On')

    local_event_Status.emit({'level': 0, 'message': 'OK'})
    local_event_LastLanding.emit(str(now))
    
  except:
    console.error('Landing error')
    prevChecked = local_event_LastLanding.getArg()
    message = 'NG'
    if is_blank(prevChecked):
      message = 'Never "landed"'
    else:
      prevChecked = date_parse(prevChecked)
      roughDiff = (now.getMillis() - prevChecked.getMillis()) / 1000 / 60
      if roughDiff < 60:
        message = 'Last landing was approx. %s mins ago' % roughDiff
      elif roughDiff < (60 * 24):
        message = 'No landing since %s' % prevChecked.toString('h:mm:ss a')
      else:
        message = 'No landing since %s' % prevChecked.toString('h:mm:ss a, E d-MMM')

    local_event_Status.emit({'level': 2, 'message': message})    

Timer(lambda: statusCheck(), 60, 15)