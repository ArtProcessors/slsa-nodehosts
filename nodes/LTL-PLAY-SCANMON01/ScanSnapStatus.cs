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
//   SS.Result=<code>          ALWAYS LAST -- see the Result values below
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
// Compiled on first run by the recipe using csc.exe from .NET Framework 4.

using System;
using System.IO;
using System.Diagnostics;
using Microsoft.Win32;

namespace ScanSnapStatus
{
    static class Program
    {
        private const string SCANSNAP_EXE = "PfuSsMon.exe";
        private const string SCANSNAP_SDK = "PfuSsMonSdk.exe";
        private const string REGISTRY_APP_PATH = @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{0}";
        private const int INTERFACE_VERSION = 20;  // required for ScanSnap Home 2.8.0 or later

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

        public static int Main()
        {
            try
            {
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
