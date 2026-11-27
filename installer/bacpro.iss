; ===========================================================================
;  bacpro - Inno Setup script (optional Windows installer)
;
;  Build:
;     1. Build the executable first:  build_windows.bat   -> dist\bacpro.exe
;     2. Install Inno Setup 6:       https://jrsoftware.org/isdl.php
;     3. iscc installer\bacpro.iss
;
;  Result: installer\Output\bacpro-setup.exe
;
;  The installer is per-user by default (no admin prompt), which is what you
;  want on a locked-down work laptop. Change PrivilegesRequired to "admin"
;  and DefaultDirName to {autopf}\bacpro for a machine-wide install.
; ===========================================================================

#define AppName        "bacpro"
#define AppVersion     "1.0.0"
#define AppPublisher   "bacpro"
#define AppExe         "bacpro.exe"
#define AppId          "{{8F2A6C41-5B7E-4E2A-9C1D-3B7A0F5E6D21}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=bacpro-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
; The app only listens on 127.0.0.1, so no firewall rule is needed.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "quicklaunchicon"; Description: "Create a &Quick Launch shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\BUILD.md";       DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";              Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";        Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: quicklaunchicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Logs and the throwaway browser profile live outside {app}; leave them alone so
; a reinstall keeps the history.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
// Close a running copy so the file copy cannot fail silently.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec('taskkill.exe', '/IM bacpro.exe /F', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
end;
