; Inno Setup 6 script for ChronicleSetup-<version>.exe (BU127).
;
; Built by packaging\build.ps1, which passes /DAppVersion from APP_VERSION in
; src\config.py after PyInstaller has produced dist\Chronicle\.
;
; The install folder only holds the app. User data, models and the API key
; live elsewhere (the data folder chosen in the setup wizard, and the Windows
; Credential Manager), so upgrades and reinstalls never touch them.

#ifndef AppVersion
  #error Pass /DAppVersion=<version> (packaging\build.ps1 does)
#endif

#define AppName "Chronicle"
#define AppExe "Chronicle.exe"
; Held by the running app: APP_MUTEX in src\main.py.
#define AppMutexName "Chronicle-AppRunning"

[Setup]
; Fixed forever: upgrades find and replace the previous install through it.
AppId={{28B9EE16-A46B-4B67-85AF-48EDE43B2AE2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
VersionInfoVersion={#AppVersion}
; "Just me" (default, no admin, %LOCALAPPDATA%\Programs) or "all users"
; (Program Files, admin prompt). Any folder can still be typed or browsed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DisableDirPage=no
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex={#AppMutexName},Global\{#AppMutexName}
CloseApplications=no
SetupIconFile=..\assets\chronicle.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=ChronicleSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Chronicle\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: VCRedistNeeded

[InstallDelete]
; Files a previous version shipped that this one no longer does.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; The runtime needs admin rights: a "just me" install shows one UAC prompt
; here, and only on machines that lack the runtime.
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; \
  StatusMsg: "Installing the Microsoft Visual C++ runtime..."; \
  Flags: shellexec waituntilterminated; Verb: "runas"; Check: VCRedistNeeded
; First launch: no data folder is known yet, so this opens the setup wizard.
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent runasoriginaluser

[Code]
const
  CRED_TYPE_GENERIC = 1;
  VCRuntimeKey = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';
  { onnxruntime is built with VS 2022 17.10+, whose code can crash on an
    older msvcp140.dll, so anything before 14.40 counts as missing. }
  VCRuntimeMinMinor = 40;

function CredDeleteW(TargetName: String; CredType: DWORD; Flags: DWORD): Boolean;
  external 'CredDeleteW@advapi32.dll stdcall uninstallonly';

function VCRuntimeFound(RootKey: Integer): Boolean;
var
  Installed, Major, Minor: Cardinal;
begin
  Result := RegQueryDWordValue(RootKey, VCRuntimeKey, 'Installed', Installed) and (Installed = 1)
    and RegQueryDWordValue(RootKey, VCRuntimeKey, 'Major', Major)
    and RegQueryDWordValue(RootKey, VCRuntimeKey, 'Minor', Minor)
    and ((Major > 14) or ((Major = 14) and (Minor >= VCRuntimeMinMinor)));
end;

function VCRedistNeeded: Boolean;
begin
  Result := not (VCRuntimeFound(HKLM64) or VCRuntimeFound(HKLM32));
end;

{ --- uninstall: offer to delete user data -------------------------------- }

function PointerFolder: String;
begin
  Result := ExpandConstant('{userappdata}\Chronicle');
end;

{ The string value of "Key" in a flat JSON object, JSON escapes decoded.
  Python's json.dumps escapes every non-ASCII character as \uXXXX. }
function JsonString(const Json, Key: String): String;
var
  P: Integer;
begin
  Result := '';
  P := Pos('"' + Key + '"', Json);
  if P = 0 then
    Exit;
  P := P + Length(Key) + 2;
  while (P <= Length(Json)) and (Json[P] <> '"') do
    P := P + 1;
  P := P + 1;
  while (P <= Length(Json)) and (Json[P] <> '"') do
  begin
    if (Json[P] = '\') and (P < Length(Json)) then
    begin
      P := P + 1;
      if Json[P] = 'u' then
      begin
        Result := Result + Chr(StrToInt('$' + Copy(Json, P + 1, 4)));
        P := P + 4;
      end
      else
        Result := Result + Json[P];
    end
    else
      Result := Result + Json[P];
    P := P + 1;
  end;
end;

{ Where the app keeps its data: the folder saved by the setup wizard in
  %APPDATA%\Chronicle\location.json, else the frozen default (src\paths.py). }
function DataFolder: String;
var
  Json: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(PointerFolder + '\location.json', Json) then
    Result := JsonString(String(Json), 'data_dir');
  if Result = '' then
    Result := ExpandConstant('{localappdata}\Chronicle');
end;

{ Only Chronicle's own files: the data folder may be one the user also keeps
  other things in. The folder itself goes only if nothing else is left. }
procedure DeleteUserData(const Dir: String);
begin
  DelTree(Dir + '\sessions', True, True, True);
  DelTree(Dir + '\models', True, True, True);
  DelTree(Dir + '\logs', True, True, True);
  DelTree(Dir + '\chronicle.db*', False, True, False);
  DelTree(Dir + '\.preferences-*.tmp', False, True, False);
  DeleteFile(Dir + '\preferences.json');
  DeleteFile(Dir + '\setup_state.json');
  RemoveDir(Dir);
  { The API key: keyring's target is the service name, or user@service. }
  CredDeleteW('Chronicle', CRED_TYPE_GENERIC, 0);
  CredDeleteW('openrouter@Chronicle', CRED_TYPE_GENERIC, 0);
  DelTree(PointerFolder, True, True, True);
  DelTree(ExpandConstant('{%TEMP}\chronicle-theme'), True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if (CurUninstallStep <> usPostUninstall) or UninstallSilent then
    Exit;
  Dir := DataFolder;
  if not DirExists(Dir) and not FileExists(PointerFolder + '\location.json') then
    Exit;
  if MsgBox('Chronicle has been removed.' + #13#10#13#10 +
            'Also delete your recordings, transcripts, settings, downloaded models ' +
            'and saved API key?' + #13#10#13#10 + 'Data folder:' + #13#10 + Dir + #13#10#13#10 +
            'Choose No to keep them; installing Chronicle again will use them.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DeleteUserData(Dir);
end;
