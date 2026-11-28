; Nyenz Agent Windows installer (Inno Setup 6; the files, folders and data folder keep the name FanOut on purpose). Built by CI after PyInstaller has made dist\FanOut.
;   iscc /DAppVersion=0.2.0 packaging\fanout.iss      ->  dist\FanOut-Setup-0.2.0.exe
;
; - Installs for the current user only: no admin password, no UAC prompt.
; - Running a newer installer over an older one upgrades in place (same AppId). Settings, logins and
;   results live in %APPDATA%\FanOut, outside the program folder, so they are never touched.
; - The in-app updater runs this silently with /relaunch=1, which reopens FanOut when it is done.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{D2FA2C88-CEE7-4B59-8246-7428311F35C0}
AppName=Nyenz Agent
AppVersion={#AppVersion}
AppVerName=Nyenz Agent {#AppVersion}
AppPublisher=Nyenz
AppPublisherURL=https://github.com/nyenz/agent
AppSupportURL=https://github.com/nyenz/agent/issues
AppUpdatesURL=https://github.com/nyenz/agent/releases
DefaultDirName={localappdata}\Programs\FanOut
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=FanOut-Setup-{#AppVersion}
SetupIconFile=icons\fanout.ico
UninstallDisplayIcon={app}\FanOut.exe
UninstallDisplayName=Nyenz Agent
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force
CloseApplicationsFilter=FanOut.exe
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Nyenz Agent

[Tasks]
Name: "desktopicon"; Description: "Put a Nyenz Agent shortcut on my Desktop"; GroupDescription: "Shortcuts:"

[InstallDelete]
; files from an older version that the new one no longer has must not linger
Type: filesandordirs; Name: "{app}\_internal"
; the shortcuts from before the app was renamed to Nyenz Agent
Type: files; Name: "{autoprograms}\FanOut.lnk"
Type: files; Name: "{autodesktop}\FanOut.lnk"

[Files]
Source: "..\dist\FanOut\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Nyenz Agent"; Filename: "{app}\FanOut.exe"; Comment: "Send different prompts to several chat sites at once"
Name: "{autodesktop}\Nyenz Agent"; Filename: "{app}\FanOut.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\FanOut.exe"; Description: "Open Nyenz Agent now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\FanOut.exe"; Flags: nowait; Check: RelaunchAfterUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function RelaunchAfterUpdate: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;
