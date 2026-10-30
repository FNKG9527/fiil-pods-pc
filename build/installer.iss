; ============================================================================
;  FiilPods PC 探针 — Inno Setup 安装脚本
;  编译： "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
;  产物： dist\installer\FiilPodsPC_Setup_1.3.0.exe
; ============================================================================

#define MyAppName "FiilPods PC 探针"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "FiilPods PC"
#define MyAppExeName "FiilPodsPC.exe"
#define MyAppId "{{8F3C2B1A-6D4E-4A97-9C55-2E7B1D0A4F63}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=1.4.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

; 默认按当前用户安装（不需要管理员权限、不弹 UAC）；
; 管理员运行时可以在向导里切换成「为所有用户安装」。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\FiilPodsPC
DefaultGroupName=FiilPods PC
DisableProgramGroupPage=yes
AllowNoIcons=yes

OutputDir=..\dist\installer
OutputBaseFilename=FiilPodsPC_Setup_{#MyAppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
MinVersion=10.0
; 默认直接进简体中文向导，不弹语言选择框；
; 需要英文界面的用户可用命令行参数 /LANG=english 指定。
ShowLanguageDialog=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\FiilPodsPC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\PROTOCOL_PC.md"; DestDir: "{app}\docs"; Flags: ignoreversion
Source: "使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\FiilPodsPC');
    if DirExists(DataDir) then
    begin
      { 静默卸载（/VERYSILENT）不能弹窗，默认保留用户数据，避免卡住自动化 }
      if UninstallSilent then
        exit;
      if MsgBox('是否同时删除本机保存的耳机连接记录与导出的日志？' + #13#10 + #13#10 +
                DataDir, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
