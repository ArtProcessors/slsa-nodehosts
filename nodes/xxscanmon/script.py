'''
A Windows agent for monitoring ScanSnap status.

_(Revision 1)_
'''

# <!-- scanner info

local_event_FirmVersion = LocalEvent({ 'group': 'Scanner Info', 'order': next_seq(), 'schema': { 'type': 'string' }})
local_event_SerialNo = LocalEvent({ 'group': 'Scanner Info', 'order': next_seq(), 'schema': { 'type': 'string' }})
local_event_ScannerName = LocalEvent({ 'group': 'Scanner Info', 'order': next_seq(), 'schema': { 'type': 'string' }})
local_event_AcquisitionDate = LocalEvent({ 'group': 'Scanner Info', 'order': next_seq(), 'schema': { 'type': 'string' }})
local_event_ManagerVersion = LocalEvent({ 'group': 'Scanner Info', 'order': next_seq(), 'schema': { 'type': 'string' }})
local_event_ScannerConnected = LocalEvent({ 'group': 'Scanner', 'order': next_seq(), 'schema': { 'type': 'boolean' }})

# --->

# <!-- ScanSnapStatus wrapper

def extract_and_print_scanner_info(result):
    try:
        stdout = result.stdout
    except AttributeError:
        console.warn('No stdout found in result')
        return
    
    lines = stdout.splitlines()
    
    log(2, 'Scanner Information:')
    log(2, '-------------------')
    
    scanner_connected = False
    
    for line in lines:
        log(2, line)
        if line.startswith('FirmVersion='):
            firm_version = line.split('=')[1]
            local_event_FirmVersion.emit(firm_version)
        elif line.startswith('SerialNo='):
            serial_no = line.split('=')[1]
            local_event_SerialNo.emit(serial_no)
        elif line.startswith('ScannerName='):
            scanner_name = line.split('=')[1]
            local_event_ScannerName.emit(scanner_name)
        elif line.startswith('AcquisitionDate='):
            acquisition_date = line.split('=')[1]
            local_event_AcquisitionDate.emit(acquisition_date)
        elif line.startswith('ManagerVersion='):
            manager_version = line.split('=')[1]
            local_event_ManagerVersion.emit(manager_version)
        elif line == 'Scanner Status: Connected':
            scanner_connected = True
    
    local_event_ScannerConnected.emit(scanner_connected)
    
    log(1, ('Scanner Status: Connected' if scanner_connected else 'Scanner Status: Not Connected'))

@local_action({ 'title': 'Request Status', 'group': 'Request Status', 'order': next_seq()})
def request_status(arg):
    log(1, ('Requesting ScanSnap status'))

    quick_process(['ScanSnapStatus.exe'], finished=extract_and_print_scanner_info)

Timer(lambda: lookup_local_action('Request Status').call(), 150, 10, stopped=False) # check status every 2.5 mins (10s first time)


# -->


# <!- status

local_event_Status = LocalEvent({ 'group': 'Status', 'order': next_seq(), 'schema': { 'type': 'object', 'properties': {
                                      'level':   {'type': 'integer', 'order': 1 },
                                      'message': {'type': 'string', 'order': 2 }}}})


# -- >

# compile to code on first run

import os

# 32bit path is C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe
# 64bit      is C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe

COMPILER_PATH = r'%s\Microsoft.NET\Framework%s\v4.0.30319\csc.exe' % (os.environ['WINDIR'], 
                                    '64' if '64' in os.environ['PROCESSOR_ARCHITECTURE'] else '')

@after_main
def performCompilation():
  if not os.path.exists('ScanSnapStatus.exe'):
    log(1, 'ScanSnapStatus.exe not found, starting compilation process...')
    log(1, 'Using compiler path %s' % COMPILER_PATH)
    
    quick_process([COMPILER_PATH, 'ScanSnapStatus.cs'], finished=compileComplete)
  else:
    log(1, 'ScanSnapStatus.exe already exists, skipping compilation.')

def compileComplete(arg):
  if arg.code != 0:
    console.error('BAD COMPILATION RESULT (code was %s)' % arg.code)
    console.error(arg.stdout)
    return
  
  console.info("Successfully compiled ScanSnapStatus.cs")
  
# -- >


# <!-- logging

local_event_LogLevel = LocalEvent({'group': 'Debug', 'order': 10000+next_seq(), 'desc': 'Use this to ramp up the logging (with indentation)',  
                                   'schema': {'type': 'integer'}})

def log(level, msg):
    if local_event_LogLevel.getArg() >= level:
        console.log('[log]' + ('.' * level) + ' ' + msg)

def warn(level, msg):
    if local_event_LogLevel.getArg() >= level:
        console.warn('[warn]' + ('.' * level) + ' ' + msg)

# --!>
