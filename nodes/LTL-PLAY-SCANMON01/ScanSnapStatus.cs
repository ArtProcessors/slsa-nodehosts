// Queries ScanSnap Home for the state of the attached scanner and prints a
// machine-readable summary on stdout for the Nodel recipe to parse.
//
// Output contract -- every line this program cares about is prefixed "SS.":
//
//   SS.Installed=1|0          ScanSnap Home present in the registry
//   SS.AppPath=<path>         where PfuSsMon.exe lives, whenever it is installed
//   SS.Running=1|0            PfuSsMon.exe is running
//   SS.ScannerCount=<n>       only on SS.Result=OK
//   SS.FirmVersion=...        \
//   SS.SerialNo=...            |
//   SS.ScannerName=...         > only on SS.Result=OK, passed through from the SDK
//   SS.AcquisitionDate=...     |
//   SS.ManagerVersion=...     /
//   SS.Usb=1|0|?              the scanner is / is not in Windows' device list right now;
//                             ? if the check itself failed. Independent of ScanSnap Home
//   SS.UsbId=<instance id>    \
//   SS.UsbArrived=<time>       > only when Windows has ever seen the device
//   SS.UsbRemoved=<time>      /
//   SS.UsbProblem=<code>      only when it is present but Windows reports a device problem
//   SS.Result=<code>          ALWAYS LAST -- see the Result values below
//
// Usage: ScanSnapStatus.exe [<usb id>] [usb-only]
//
// "usb-only" does the USB check and nothing else, ending with SS.Result=USB_ONLY. It never
// touches ScanSnap Home or the scanner, so it is safe to run every few seconds, mid-scan.
//
// Result values:
//
//   OK                   the SDK answered; SS.ScannerCount is definitive
//   NO_SCANNER           definitive: no scanner connected (or it is in use by a mobile device)
//   BUSY                 a scan is in progress or the ScanSnap Home window is open --
//                        the SDK refuses to answer, so the scanner state is UNKNOWN, not off
//   NOT_INSTALLED        ScanSnap Home is not installed
//   NOT_RUNNING          ScanSnap Home is not running
//   SDK_NOT_FOUND        PfuSsMonSdk.exe is not registered
//   NO_INFO_FILE         the SDK reported success but wrote no info file
//   PARAM_ERROR          the SDK rejected our settings file
//   UNSUPPORTED_VERSION  IFVERSION is wrong for this ScanSnap Home build
//   UNKNOWN:<code>       an SDK exit code we do not recognise
//   ERROR:<message>      this program failed
//
// The process exit code is 0 whenever a SS.Result line was produced -- i.e. it means
// "this shim ran", not "the scanner is on". Anything else means the shim itself failed.
//
// Only three of these results are definitive about the scanner: OK, NO_SCANNER and
// NOT_RUNNING/NOT_INSTALLED (which mean "cannot know"). BUSY in particular MUST NOT be
// read as "no scanner" -- it is what you get while a visitor is actually scanning.
//
// The USB check exists because NO_SCANNER cannot tell "not on USB at all" (switched off, no
// mains, cable) from "on USB, but ScanSnap Home cannot use it". It takes an optional first
// argument, the device ID to look for (default VID_04C5&PID_13BA, the SV600), and looks in
// Windows' own device list, so it answers even while ScanSnap Home is busy or not running.
//
// Compiled on first run by the recipe using csc.exe from .NET Framework 4.

using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32;

namespace ScanSnapStatus
{
    static class Program
    {
        private const string SCANSNAP_EXE = "PfuSsMon.exe";
        private const string SCANSNAP_SDK = "PfuSsMonSdk.exe";
        private const string REGISTRY_APP_PATH = @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{0}";
        private const int INTERFACE_VERSION = 20;  // required for ScanSnap Home 2.8.0 or later
        private const string DEFAULT_USB_ID = "VID_04C5&PID_13BA";  // ScanSnap SV600
        private const string USB_ONLY = "usb-only";

        private static class ExitCodes
        {
            public const int Success = 0;
            public const int ScanInProgress = 1;
            public const int ParameterError = 5;
            public const int NoScanner = 6;
            public const int UnsupportedVersion = 21;
            public const int NotRunning = 25;
        }

        // info-file keys passed through to the recipe; anything else is dumped as a
        // comment line for debugging only
        private static readonly string[] InfoKeys = new string[] {
            "FirmVersion", "SerialNo", "ScannerName", "AcquisitionDate", "ManagerVersion" };

        public static int Main(string[] args)
        {
            try
            {
                CheckUsb(args.Length > 0 && args[0].Trim().Length > 0 ? args[0] : DEFAULT_USB_ID);

                if (args.Length > 1 && args[1] == USB_ONLY)
                    Emit("Result", "USB_ONLY");
                else
                    Run();
            }
            catch (Exception ex)
            {
                Emit("Result", "ERROR:" + ex.Message.Replace("\r", " ").Replace("\n", " "));
            }

            return 0;
        }

        static void Run()
        {
            string appPath = GetAppPath(SCANSNAP_EXE);

            if (string.IsNullOrEmpty(appPath) || !File.Exists(appPath))
            {
                Emit("Installed", "0");
                Emit("Running", "0");
                Emit("Result", "NOT_INSTALLED");
                return;
            }

            Emit("Installed", "1");

            // the recipe needs this to be able to start ScanSnap Home again; reading it
            // here keeps the registry knowledge in one place
            Emit("AppPath", appPath);

            if (!IsScanSnapHomeRunning())
            {
                Emit("Running", "0");
                Emit("Result", "NOT_RUNNING");
                return;
            }

            Emit("Running", "1");

            CheckScannerStatus();
        }

        static void Emit(string key, string value)
        {
            Console.WriteLine("SS." + key + "=" + value);
        }

        static void Comment(string text)
        {
            Console.WriteLine("# " + text);
        }

        // ---- USB: Windows' own view of the scanner, via SetupAPI -------------------------

        private const uint DIGCF_ALLCLASSES = 0x4;     // no DIGCF_PRESENT: absent devices carry the dates
        private const int CR_SUCCESS = 0;              // CM_Get_DevNode_Status: anything else = not present
        private const uint DN_HAS_PROBLEM = 0x400;
        private const uint DEVPROP_TYPE_FILETIME = 0x10;
        private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

        // DEVPKEY_Device_LastArrivalDate (102) and DEVPKEY_Device_LastRemovalDate (103)
        private static readonly Guid DEVICE_DATES = new Guid("83da6326-97a6-4088-9453-a1923f573b29");

        [StructLayout(LayoutKind.Sequential)]
        struct SP_DEVINFO_DATA
        {
            public uint cbSize;
            public Guid ClassGuid;
            public uint DevInst;
            public IntPtr Reserved;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct DEVPROPKEY
        {
            public Guid fmtid;
            public uint pid;
        }

        [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern IntPtr SetupDiGetClassDevsW(IntPtr classGuid, string enumerator, IntPtr hwndParent, uint flags);

        [DllImport("setupapi.dll", SetLastError = true)]
        static extern bool SetupDiEnumDeviceInfo(IntPtr deviceInfoSet, uint index, ref SP_DEVINFO_DATA data);

        [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool SetupDiGetDeviceInstanceIdW(IntPtr deviceInfoSet, ref SP_DEVINFO_DATA data,
                                                       StringBuilder instanceId, int size, out int required);

        [DllImport("setupapi.dll", SetLastError = true)]
        static extern bool SetupDiGetDevicePropertyW(IntPtr deviceInfoSet, ref SP_DEVINFO_DATA data, ref DEVPROPKEY key,
                                                     out uint type, byte[] buffer, uint size, out uint required, uint flags);

        [DllImport("setupapi.dll", SetLastError = true)]
        static extern bool SetupDiDestroyDeviceInfoList(IntPtr deviceInfoSet);

        [DllImport("cfgmgr32.dll")]
        static extern int CM_Get_DevNode_Status(out uint status, out uint problem, uint devInst, uint flags);

        static void CheckUsb(string usbId)
        {
            // must never stop the ScanSnap Home check from running
            try
            {
                FindUsbDevice(usbId);
            }
            catch (Exception ex)
            {
                Comment("USB check failed: " + ex.Message);
                Emit("Usb", "?");
            }
        }

        static void FindUsbDevice(string usbId)
        {
            string wanted = @"USB\" + usbId.Trim().ToUpperInvariant();

            IntPtr set = SetupDiGetClassDevsW(IntPtr.Zero, "USB", IntPtr.Zero, DIGCF_ALLCLASSES);
            if (set == INVALID_HANDLE_VALUE)
            {
                Comment("SetupDiGetClassDevs failed: " + Marshal.GetLastWin32Error());
                Emit("Usb", "?");
                return;
            }

            bool found = false, present = false;
            uint problem = 0;
            string instanceId = null;
            DateTime? arrived = null, removed = null;

            try
            {
                var data = new SP_DEVINFO_DATA();
                data.cbSize = (uint)Marshal.SizeOf(typeof(SP_DEVINFO_DATA));

                for (uint i = 0; SetupDiEnumDeviceInfo(set, i, ref data); i++)
                {
                    var id = new StringBuilder(512);
                    int required;
                    if (!SetupDiGetDeviceInstanceIdW(set, ref data, id, id.Capacity, out required))
                        continue;

                    string candidate = id.ToString();
                    if (!IsWantedDevice(candidate.ToUpperInvariant(), wanted))
                        continue;

                    uint status, candidateProblem;
                    bool candidatePresent = CM_Get_DevNode_Status(out status, out candidateProblem, data.DevInst, 0) == CR_SUCCESS;
                    DateTime? candidateArrived = GetDate(set, ref data, 102);

                    // the same scanner on another port is another entry: prefer the one that is
                    // present, then the one that arrived most recently
                    bool better = !found
                        || (candidatePresent && !present)
                        || (candidatePresent == present && Later(candidateArrived, arrived));

                    if (!better)
                        continue;

                    found = true;
                    present = candidatePresent;
                    problem = candidatePresent && (status & DN_HAS_PROBLEM) != 0 ? candidateProblem : 0;
                    instanceId = candidate;
                    arrived = candidateArrived;
                    removed = GetDate(set, ref data, 103);
                }
            }
            finally
            {
                SetupDiDestroyDeviceInfoList(set);
            }

            if (found)
            {
                Emit("UsbId", instanceId);

                if (arrived.HasValue)
                    Emit("UsbArrived", arrived.Value.ToString("yyyy-MM-ddTHH:mm:sszzz"));

                if (removed.HasValue)
                    Emit("UsbRemoved", removed.Value.ToString("yyyy-MM-ddTHH:mm:sszzz"));

                if (problem != 0)
                    Emit("UsbProblem", problem.ToString());
            }

            Emit("Usb", present ? "1" : "0");
        }

        static bool IsWantedDevice(string instanceId, string wanted)
        {
            // USB\VID_04C5&PID_13BA\6&38F36B53&0&2 -- the device itself, not its interfaces
            // (&MI_xx), which come and go with it
            if (!instanceId.StartsWith(wanted) || instanceId.IndexOf("&MI_") >= 0)
                return false;

            // the ID has to end on a boundary, so PID_13B does not match PID_13BA
            if (instanceId.Length == wanted.Length)
                return true;

            char next = instanceId[wanted.Length];
            return next == '\\' || next == '&';
        }

        static DateTime? GetDate(IntPtr set, ref SP_DEVINFO_DATA data, uint pid)
        {
            var key = new DEVPROPKEY();
            key.fmtid = DEVICE_DATES;
            key.pid = pid;

            byte[] buffer = new byte[8];
            uint type, required;

            if (!SetupDiGetDevicePropertyW(set, ref data, ref key, out type, buffer, (uint)buffer.Length, out required, 0)
                || type != DEVPROP_TYPE_FILETIME)
                return null;

            long fileTime = BitConverter.ToInt64(buffer, 0);
            return fileTime > 0 ? (DateTime?)DateTime.FromFileTime(fileTime) : null;
        }

        static bool Later(DateTime? a, DateTime? b)
        {
            return a.HasValue && (!b.HasValue || a.Value > b.Value);
        }

        // ---- ScanSnap Home ------------------------------------------------------------------

        static bool IsScanSnapHomeRunning()
        {
            // this is the signal the Nodel readiness gate wants: the ScanSnap Home
            // service process itself, not something an app launcher believes it spawned
            string processName = Path.GetFileNameWithoutExtension(SCANSNAP_EXE);
            return Process.GetProcessesByName(processName).Length > 0;
        }

        static string GetAppPath(string exeName)
        {
            try
            {
                string registryPath = string.Format(REGISTRY_APP_PATH, exeName);
                using (RegistryKey key = Registry.LocalMachine.OpenSubKey(registryPath))
                {
                    if (key == null)
                        return null;

                    object value = key.GetValue("");
                    return value != null ? value.ToString() : null;
                }
            }
            catch (Exception ex)
            {
                Comment("registry lookup for " + exeName + " failed: " + ex.Message);
                return null;
            }
        }

        static void CheckScannerStatus()
        {
            string sdkPath = GetAppPath(SCANSNAP_SDK);
            if (string.IsNullOrEmpty(sdkPath) || !File.Exists(sdkPath))
            {
                Emit("Result", "SDK_NOT_FOUND");
                return;
            }

            Comment("SDK at " + sdkPath);

            string settingsPath = Path.Combine(Path.GetTempPath(), "scannerCommand.ini");
            string infoFilePath = Path.Combine(Path.GetTempPath(), "scannerInfo.ini");

            try
            {
                CreateSettingsFile(settingsPath, infoFilePath);
                ExecuteScannerCheck(sdkPath, settingsPath, infoFilePath);
            }
            finally
            {
                CleanupTempFiles(settingsPath, infoFilePath);
            }
        }

        static void CreateSettingsFile(string settingsPath, string infoFilePath)
        {
            string settings = string.Format(
                "[Info]\r\n" +
                "IFVersion={0}\r\n" +
                "FileVersion=1\r\n" +
                "[Command]\r\n" +
                "CommandMode=5\r\n" +
                "[Common]\r\n" +
                "AppName=ImageSettingsForHome\r\n" +
                "Mode=0\r\n" +
                "FileName={1}\r\n",
                INTERFACE_VERSION,
                infoFilePath);

            File.WriteAllText(settingsPath, settings);
        }

        static void ExecuteScannerCheck(string sdkPath, string settingsPath, string infoFilePath)
        {
            var processStartInfo = new ProcessStartInfo
            {
                FileName = sdkPath,
                Arguments = string.Format("\"{0}\"", settingsPath),
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };

            using (var process = Process.Start(processStartInfo))
            {
                process.WaitForExit();
                HandleExitCode(process.ExitCode, infoFilePath);
            }
        }

        static void HandleExitCode(int exitCode, string infoFilePath)
        {
            switch (exitCode)
            {
                case ExitCodes.Success:
                    if (!File.Exists(infoFilePath))
                    {
                        Emit("Result", "NO_INFO_FILE");
                        return;
                    }
                    ParseScannerInfo(File.ReadAllLines(infoFilePath));
                    Emit("Result", "OK");
                    break;

                case ExitCodes.ScanInProgress:
                    // a scan is running, or an operator has the ScanSnap Home window open.
                    // says nothing about whether the scanner is powered
                    Emit("Result", "BUSY");
                    break;

                case ExitCodes.ParameterError:
                    Emit("Result", "PARAM_ERROR");
                    break;

                case ExitCodes.NoScanner:
                    Emit("Result", "NO_SCANNER");
                    break;

                case ExitCodes.UnsupportedVersion:
                    Emit("Result", "UNSUPPORTED_VERSION");
                    break;

                case ExitCodes.NotRunning:
                    Emit("Result", "NOT_RUNNING");
                    break;

                default:
                    Emit("Result", "UNKNOWN:" + exitCode);
                    break;
            }
        }

        static void ParseScannerInfo(string[] lines)
        {
            int scannerCount = 0;

            foreach (string line in lines)
            {
                int separator = line.IndexOf('=');
                if (separator < 1)
                {
                    Comment(line);
                    continue;
                }

                // split on the FIRST '=' only -- values may contain one
                string key = line.Substring(0, separator).Trim();
                string value = line.Substring(separator + 1).Trim();

                if (key == "ScannerCount")
                {
                    // parse as a number; the old "does not end in 0" test called
                    // ScannerCount=10 a disconnected scanner
                    if (!int.TryParse(value, out scannerCount))
                    {
                        Comment("could not read ScannerCount from: " + line);
                        scannerCount = 0;
                    }
                    continue;
                }

                if (IsInfoKey(key))
                {
                    Emit(key, value);
                    continue;
                }

                Comment(line);
            }

            Emit("ScannerCount", scannerCount.ToString());
        }

        static bool IsInfoKey(string key)
        {
            foreach (string known in InfoKeys)
            {
                if (known == key)
                    return true;
            }

            return false;
        }

        static void CleanupTempFiles(params string[] files)
        {
            foreach (string file in files)
            {
                try
                {
                    if (File.Exists(file))
                        File.Delete(file);
                }
                catch (Exception ex)
                {
                    Comment(string.Format("failed to clean up {0}: {1}", file, ex.Message));
                }
            }
        }
    }
}
