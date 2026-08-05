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
        private const int INTERFACE_VERSION = 20;  // Required for ScanSnap Home 2.8.0 or later

        private static class ExitCodes
        {
            public const int Success = 0;
            public const int ScanInProgress = 1;
            public const int ParameterError = 5;
            public const int NoScanner = 6;
            public const int UnsupportedVersion = 21;
            public const int NotRunning = 25;
        }

        public static int Main()
        {
            try
            {
                Console.WriteLine("Starting ScanSnap scanner check...");
                
                if (!ValidateEnvironment())
                {
                    return 1;
                }

                CheckScannerStatus();
                return 0;
            }
            catch (Exception ex)
            {
                Console.WriteLine("An error occurred: " + ex.Message);
                return 1;
            }
        }

        static bool ValidateEnvironment()
        {
            if (!IsScanSnapHomeInstalled())
            {
                Console.WriteLine("ScanSnap Home is not installed.");
                return false;
            }

            if (!IsScanSnapHomeRunning())
            {
                Console.WriteLine("ScanSnap Home is not running.");
                return false;
            }

            return true;
        }

        static bool IsScanSnapHomeInstalled()
        {
            try
            {
                string registryPath = string.Format(REGISTRY_APP_PATH, SCANSNAP_EXE);
                using (RegistryKey key = Registry.LocalMachine.OpenSubKey(registryPath))
                {
                    if (key == null)
                        return false;

                    object value = key.GetValue("");
                    string defaultValue = value != null ? value.ToString() : null;
                    return !string.IsNullOrEmpty(defaultValue) && File.Exists(defaultValue);
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("Error checking installation: " + ex.Message);
                return false;
            }
        }

        static bool IsScanSnapHomeRunning()
        {
            string processName = Path.GetFileNameWithoutExtension(SCANSNAP_EXE);
            Process[] processes = Process.GetProcessesByName(processName);
            return processes.Length > 0;
        }

        static string GetSdkPath()
        {
            string registryPath = string.Format(REGISTRY_APP_PATH, SCANSNAP_SDK);
            using (RegistryKey key = Registry.LocalMachine.OpenSubKey(registryPath))
            {
                if (key != null)
                {
                    object value = key.GetValue("");
                    if (value != null)
                    {
                        string sdkPath = value.ToString();
                        if (File.Exists(sdkPath))
                        {
                            return sdkPath;
                        }
                    }
                }
            }
            return null;
        }

        static void CheckScannerStatus()
        {
            string sdkPath = GetSdkPath();
            if (string.IsNullOrEmpty(sdkPath))
            {
                Console.WriteLine("Could not find PfuSsMonSdk.exe path in registry");
                return;
            }

            Console.WriteLine("Found SDK at: " + sdkPath);

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
                    if (File.Exists(infoFilePath))
                    {
                        DisplayScannerInfo(infoFilePath);
                    }
                    break;
                case ExitCodes.ScanInProgress:
                    Console.WriteLine("Error: Scanning in progress or ScanSnap Home window is displayed");
                    break;
                case ExitCodes.ParameterError:
                    Console.WriteLine("Error: Parameter error");
                    break;
                case ExitCodes.NoScanner:
                    Console.WriteLine("Error: No scanner connected or scanner is in use with mobile device");
                    break;
                case ExitCodes.UnsupportedVersion:
                    Console.WriteLine("Error: Unsupported interface version specified");
                    break;
                case ExitCodes.NotRunning:
                    Console.WriteLine("Error: ScanSnap Home is not running");
                    break;
                default:
                    Console.WriteLine("Unknown error occurred (Exit code: " + exitCode + ")");
                    break;
            }
        }

        static void DisplayScannerInfo(string infoFilePath)
        {
            Console.WriteLine("\nScanner Information:");
            Console.WriteLine("-------------------");
            string[] lines = File.ReadAllLines(infoFilePath);
            ParseScannerInfo(lines);
        }

        static void ParseScannerInfo(string[] lines)
        {
            bool scannerConnected = false;
            foreach (string line in lines)
            {
                Console.WriteLine(line);
                if (line.StartsWith("ScannerCount=") && !line.EndsWith("0"))
                {
                    scannerConnected = true;
                }
            }

            Console.WriteLine("\nScanner Status: " + (scannerConnected ? "Connected" : "Not Connected"));
        }

        static void CleanupTempFiles(params string[] files)
        {
            foreach (string file in files)
            {
                try
                {
                    if (File.Exists(file))
                    {
                        File.Delete(file);
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine("Warning: Failed to cleanup file {0}: {1}", file, ex.Message);
                }
            }
        }
    }
}
