#define MyAppName "Dendritron Foundry Lite"
#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

[Setup]
AppId={{C91A687D-2230-41F2-A4C8-46B37D4569C8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=MMV Firm
DefaultDirName={localappdata}\Programs\Dendritron Foundry Lite
DefaultGroupName=Dendritron Foundry Lite
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#SourceRoot}\release\windows
OutputBaseFilename=Dendritron-Foundry-Lite-{#MyAppVersion}-Setup
SetupIconFile={#SourceRoot}\packaging\windows\assets\foundry-lite.ico
UninstallDisplayIcon={app}\FoundryLite.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
CloseApplicationsFilter=FoundryLite.exe
RestartApplications=no
SetupMutex=DendritronFoundryLiteSetup-62B88771-2D3F-4F58-877B-021FD5F406E4
ChangesEnvironment=no
MinVersion=10.0.17763
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany=MMV Firm
VersionInfoDescription=Dendritron Foundry Lite installer
VersionInfoProductName=Dendritron Foundry Lite

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start Foundry Lite when I sign in to Windows"; GroupDescription: "Background operation:"; Flags: checkedonce

[Files]
Source: "{#SourceRoot}\dist\FoundryLite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Dendritron Foundry Lite"; Filename: "{app}\FoundryLite.exe"
Name: "{group}\Stop Foundry Lite"; Filename: "{app}\FoundryLite.exe"; Parameters: "--stop"
Name: "{group}\Uninstall Dendritron Foundry Lite"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Dendritron Foundry Lite"; Filename: "{app}\FoundryLite.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Dendritron Foundry Lite"; ValueData: """{app}\FoundryLite.exe"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\FoundryLite.exe"; Description: "Launch Dendritron Foundry Lite"; Flags: nowait postinstall skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ExistingApp: String;
begin
  Result := '';
  ExistingApp := ExpandConstant('{app}\FoundryLite.exe');
  if FileExists(ExistingApp) then
  begin
    Exec(ExistingApp, '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1200);
  end;
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
  ExistingApp: String;
begin
  ExistingApp := ExpandConstant('{app}\FoundryLite.exe');
  if FileExists(ExistingApp) then
  begin
    Exec(ExistingApp, '--stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1200);
  end;
  RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Dendritron Foundry Lite');
  Result := True;
end;
