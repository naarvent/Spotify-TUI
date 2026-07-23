; Inno Setup script for SPT-TUI — per-user, no admin, no UAC.
;
; Build:  ISCC /DAppVersion=0.3.0 installer\spt-tui.iss
; Expects the PyInstaller onedir output at dist\spt\ with librespot.exe already
; copied in beside spt.exe. Produces dist\installer\SPT-TUI-Setup-<version>.exe.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "SPT-TUI"
#define AppExeName "spt.exe"
#define AppPublisher "naarvent"
#define AppURL "https://github.com/naarvent/Spotify-TUI"

[Setup]
; A fixed AppId is what makes a newer installer UPGRADE the existing install in
; place (files replaced, PATH and shortcuts kept) instead of installing a second
; copy. Never change it across versions.
AppId={{B7E9F3A1-5C42-4D8B-9E10-2F6A8C3D4E5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\{#AppName}
; Paths in [Files]/OutputDir are relative to SourceDir, which is relative to this
; .iss file's folder (installer/). '..' makes them relative to the repo root, so
; dist\spt and dist\installer resolve the same whether built locally or in CI.
SourceDir=..
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputBaseFilename=SPT-TUI-Setup-{#AppVersion}
OutputDir=dist\installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add SPT-TUI to your PATH (lets you run ""spt"" in any terminal)"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
; The whole onedir freeze (spt.exe, its dependencies, and librespot.exe which the
; workflow has already copied in).
Source: "dist\spt\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Add the install dir to the *user* PATH so `spt` works in any new terminal.
; Gated on the (default-on) "addtopath" task, so the user can opt out.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Flags: preservestringtype; \
  Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Run]
; Offer to launch the app from the final wizard page (a checkbox the user can
; clear). nowait lets the installer close; skipifsilent keeps silent installs quiet.
Filename: "{app}\{#AppExeName}"; Description: "Launch SPT-TUI now"; \
  Flags: nowait postinstall skipifsilent

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  { True only when the dir is not already present (idempotent re-install). }
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(OrigPath) + ';') = 0;
end;

procedure RemovePath(Param: string);
var
  OrigPath, Padded: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', OrigPath) then exit;
  Padded := ';' + OrigPath + ';';
  P := Pos(';' + Uppercase(Param) + ';', Uppercase(Padded));
  if P = 0 then exit;
  { Remove ';<dir>' (Length(Param)+1 chars) from the padded copy, then trim the
    padding semicolons we added. }
  Delete(Padded, P, Length(Param) + 1);
  if (Length(Padded) > 0) and (Padded[1] = ';') then Delete(Padded, 1, 1);
  if (Length(Padded) > 0) and (Padded[Length(Padded)] = ';') then Delete(Padded, Length(Padded), 1);
  RegWriteExpandStringValue(HKCU, 'Environment', 'Path', Padded);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemovePath(ExpandConstant('{app}'));
end;
