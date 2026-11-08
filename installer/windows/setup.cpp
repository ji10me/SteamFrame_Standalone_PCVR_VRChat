// Alpha installer. Original code is MIT licensed. See the adjacent LICENSE.md; third-party rights prevail.
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <winhttp.h>
#include <shlobj.h>
#include <commctrl.h>
#include <shellapi.h>
#include <string>
#include <vector>
#include <thread>
#include <fstream>
#include <filesystem>
#include <stdexcept>
#include <regex>
#include <chrono>
#include <algorithm>
#include "generated_package.h"
namespace fs=std::filesystem;
enum class Action { Install, Uninstall, Swap };
static HWND window,hostBox,logBox,installButton,removeButton,termsBox,instructionBox,swapBox,swapButton;
static bool busy=false;
static bool uiSmoke=false;
static int uiSmokeResult=0;
static constexpr UINT LogMessage=WM_APP+1,DoneMessage=WM_APP+2,AndroidMessage=WM_APP+3;
std::wstring wide(const std::string& s){if(s.empty())return {};int n=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s.data(),int(s.size()),nullptr,0);if(!n)return L"文字列を読み取れません";std::wstring r(n,0);MultiByteToWideChar(CP_UTF8,0,s.data(),int(s.size()),r.data(),n);return r;}
std::string utf8(const std::wstring& s){int n=WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),nullptr,0,nullptr,nullptr);std::string r(n,0);WideCharToMultiByte(CP_UTF8,0,s.data(),int(s.size()),r.data(),n,nullptr,nullptr);return r;}
void log(const std::wstring& s){PostMessageW(window,LogMessage,0,reinterpret_cast<LPARAM>(new std::wstring(s)));}
[[noreturn]] void fail(const std::string& s){throw std::runtime_error(s);}
std::wstring quote(const std::wstring& s){std::wstring out=L"\"";size_t slashes=0;for(auto c:s){if(c==L'\\'){++slashes;continue;}out.append(slashes*(c==L'\"'?2:1),L'\\');slashes=0;if(c==L'\"')out+=L'\\';out+=c;}out.append(slashes*2,L'\\');return out+L"\"";}
std::string bytes(const fs::path& p){std::ifstream f(p,std::ios::binary);if(!f)fail("File could not be opened");return {std::istreambuf_iterator<char>(f),{}};}
std::string jsonString(const std::string& s,const std::string& key){std::regex pattern("\\\""+key+"\\\"\\s*:\\s*\\\"((?:[^\\\"\\\\]|\\\\.)*)\\\"");std::smatch m;if(!std::regex_search(s,m,pattern))return {};std::string r;auto v=m[1].str();for(size_t i=0;i<v.size();++i){if(v[i]=='\\'&&i+1<v.size()){char c=v[++i];r+=c=='n'?'\n':c=='r'?'\r':c=='t'?'\t':c;}else r+=v[i];}return r;}
std::string http(const std::wstring& host,const std::wstring& route,const std::string* body,DWORD& status){
 HINTERNET session=WinHttpOpen(L"FramePCVRSetup/1.0",WINHTTP_ACCESS_TYPE_NO_PROXY,WINHTTP_NO_PROXY_NAME,WINHTTP_NO_PROXY_BYPASS,0);if(!session)fail("WinHTTP initialization failed");
 WinHttpSetTimeouts(session,4000,4000,10000,45000);HINTERNET connection=WinHttpConnect(session,host.c_str(),32000,0);HINTERNET request=connection?WinHttpOpenRequest(connection,body?L"POST":L"GET",route.c_str(),nullptr,WINHTTP_NO_REFERER,WINHTTP_DEFAULT_ACCEPT_TYPES,0):nullptr;
 auto close=[&]{if(request)WinHttpCloseHandle(request);if(connection)WinHttpCloseHandle(connection);WinHttpCloseHandle(session);};
 if(!request){close();fail("Frame development service is not available");}
 DWORD policy=WINHTTP_OPTION_REDIRECT_POLICY_NEVER;WinHttpSetOption(request,WINHTTP_OPTION_REDIRECT_POLICY,&policy,sizeof(policy));
 const wchar_t* header=body?L"Content-Type: text/plain\r\n":WINHTTP_NO_ADDITIONAL_HEADERS;
 if(!WinHttpSendRequest(request,header,body?DWORD(-1):0,body?const_cast<char*>(body->data()):WINHTTP_NO_REQUEST_DATA,body?DWORD(body->size()):0,body?DWORD(body->size()):0,0)||!WinHttpReceiveResponse(request,nullptr)){close();fail("Frameとの接続が失われました。同じネットワークと開発者モードを確認してください。");}
 DWORD length=sizeof(status);WinHttpQueryHeaders(request,WINHTTP_QUERY_STATUS_CODE|WINHTTP_QUERY_FLAG_NUMBER,WINHTTP_HEADER_NAME_BY_INDEX,&status,&length,WINHTTP_NO_HEADER_INDEX);
 std::string result;char buffer[4096];DWORD got=0;
 while(WinHttpReadData(request,buffer,sizeof(buffer),&got)&&got){result.append(buffer,got);if(result.size()>65536){close();fail("Unexpected development service response");}}
 close();return result;
}
int process(const fs::path& executable,const std::vector<std::wstring>& args,const void* input=nullptr,size_t inputSize=0,bool progress=false,std::string* captured=nullptr){
 std::wstring cmd=quote(executable.wstring());for(auto& a:args)cmd+=L" "+quote(a);
 SECURITY_ATTRIBUTES sa{sizeof(sa),nullptr,TRUE};HANDLE readOut=nullptr,writeOut=nullptr,readIn=nullptr,writeIn=nullptr;
 if(!CreatePipe(&readOut,&writeOut,&sa,0)||!CreatePipe(&readIn,&writeIn,&sa,0))fail("Could not open process pipes");
 SetHandleInformation(readOut,HANDLE_FLAG_INHERIT,0);SetHandleInformation(writeIn,HANDLE_FLAG_INHERIT,0);
 STARTUPINFOW si{};si.cb=sizeof(si);si.dwFlags=STARTF_USESTDHANDLES;si.hStdInput=readIn;si.hStdOutput=writeOut;si.hStdError=writeOut;PROCESS_INFORMATION pi{};
 if(!CreateProcessW(executable.c_str(),cmd.data(),nullptr,nullptr,TRUE,CREATE_NO_WINDOW,nullptr,nullptr,&si,&pi)){CloseHandle(readOut);CloseHandle(writeOut);CloseHandle(readIn);CloseHandle(writeIn);fail("Could not start OpenSSH");}
 CloseHandle(readIn);CloseHandle(writeOut);
 std::thread writer([=]{auto p=static_cast<const char*>(input);size_t remaining=inputSize;while(remaining){DWORD n=0;if(!WriteFile(writeIn,p,DWORD(remaining>65536?65536:remaining),&n,nullptr)||!n)break;p+=n;remaining-=n;}CloseHandle(writeIn);});
 std::string pending;char b[4096];DWORD n=0;size_t lines=0;
 while(ReadFile(readOut,b,sizeof(b),&n,nullptr)&&n){pending.append(b,n);if(captured){captured->append(b,n);if(captured->size()>65536)captured->erase(0,captured->size()-65536);}size_t end;while((end=pending.find('\n'))!=std::string::npos){auto line=pending.substr(0,end);pending.erase(0,end+1);auto message=jsonString(line,"message");if(progress&&lines++<4000)log(wide(message.empty()?line:message));}if(pending.size()>65536)pending.clear();}
 CloseHandle(readOut);writer.join();WaitForSingleObject(pi.hProcess,INFINITE);DWORD exitCode=1;GetExitCodeProcess(pi.hProcess,&exitCode);CloseHandle(pi.hThread);CloseHandle(pi.hProcess);return int(exitCode);
}
fs::path moduleFolder(){wchar_t p[32768];DWORD n=GetModuleFileNameW(nullptr,p,32768);if(!n||n>=32768)fail("Could not locate this app");return fs::path(p).parent_path();}
fs::path localData(){PWSTR raw=nullptr;if(FAILED(SHGetKnownFolderPath(FOLDERID_LocalAppData,KF_FLAG_CREATE,nullptr,&raw)))fail("LocalAppData unavailable");fs::path p=fs::path(raw)/L"FramePCVRSetup";CoTaskMemFree(raw);fs::create_directories(p);return p;}
void extractResource(unsigned id,const fs::path& path){
 auto info=FindResourceW(nullptr,MAKEINTRESOURCEW(id),RT_RCDATA);if(!info)fail("Embedded code resource missing");auto loaded=LoadResource(nullptr,info);auto size=SizeofResource(nullptr,info);auto ptr=static_cast<const char*>(LockResource(loaded));if(!ptr)fail("Embedded resource could not be loaded");
 fs::create_directories(path.parent_path());DWORD attributes=GetFileAttributesW(path.parent_path().c_str());if(attributes!=INVALID_FILE_ATTRIBUTES&&(attributes&FILE_ATTRIBUTE_REPARSE_POINT))fail("App storage must not be a reparse directory");
 if(fs::exists(path)){if(fs::is_symlink(path)||!fs::is_regular_file(path)||fs::file_size(path)!=size||bytes(path)!=std::string(ptr,size))fail("アプリ内の部品と保存済みの部品が一致しません。保存先を確認してください。");return;}
 auto temporary=fs::path(path.wstring()+L".new-"+std::to_wstring(GetCurrentProcessId()));
 HANDLE f=CreateFileW(temporary.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr);if(f==INVALID_HANDLE_VALUE)fail("Could not create private app storage");DWORD written=0;BOOL ok=WriteFile(f,ptr,size,&written,nullptr);FlushFileBuffers(f);CloseHandle(f);
 if(!ok||written!=size){DeleteFileW(temporary.c_str());fail("Could not save embedded component");}
 try{fs::rename(temporary,path);}catch(...){fs::remove(temporary);if(!fs::exists(path)||fs::file_size(path)!=size||bytes(path)!=std::string(ptr,size))throw;}
}
fs::path sshTool(const wchar_t* name){auto dir=localData()/L"openssh-10.4.0.0p1";extractResource(201,dir/L"ssh.exe");extractResource(202,dir/L"ssh-keygen.exe");extractResource(203,dir/L"libcrypto.dll");extractResource(204,dir/L"LICENSE.txt");extractResource(205,dir/L"NOTICE.txt");return dir/name;}
void showLicenseAndSource(HWND owner){auto folder=localData()/(L"licenses-and-source-"+wide(FRAME_PACKAGE_ID));extractResource(102,folder/L"LICENSE.txt");extractResource(103,folder/L"THIRD_PARTY.txt");extractResource(204,folder/L"OpenSSH-LICENSE.txt");extractResource(205,folder/L"OpenSSH-NOTICE.txt");extractResource(101,folder/L"frame-payload-with-sources.zip");ShellExecuteW(owner,L"open",folder.c_str(),nullptr,nullptr,SW_SHOWNORMAL);}
std::vector<std::wstring> sshArgs(const fs::path& data,const std::wstring& host){return {L"-F",L"NUL",L"-i",(data/L"pairing_rsa").wstring(),L"-o",L"IdentitiesOnly=yes",L"-o",L"BatchMode=yes",L"-o",L"StrictHostKeyChecking=accept-new",L"-o",L"UserKnownHostsFile="+(data/L"known_hosts").wstring(),L"-o",L"ConnectTimeout=8",L"-o",L"ServerAliveInterval=15",L"-o",L"ServerAliveCountMax=4",L"-T",L"steamos@"+host};}
bool jsonActive(const std::string& value){return std::regex_search(value,std::regex("\\\"active\\\"\\s*:\\s*true"));}
int adminConsole(const fs::path& ssh,std::vector<std::wstring> args,bool enable){
 args.erase(std::remove(args.begin(),args.end(),L"-T"),args.end());args.insert(args.end()-1,L"-tt");
 auto command=std::wstring(L"sudo /usr/bin/python3 -I -c '")+wide(FRAME_SWAP_BOOTSTRAP)+L"' "+(enable?L"enable":L"disable")+
  LR"(; status=$?; printf '\nPress Enter to close.\n'; read -r line; exit "$status")";
 args.push_back(command);std::wstring cmd=quote(ssh.wstring());for(const auto& a:args)cmd+=L" "+quote(a);
 STARTUPINFOW si{};si.cb=sizeof(si);PROCESS_INFORMATION pi{};
 if(!CreateProcessW(ssh.c_str(),cmd.data(),nullptr,nullptr,FALSE,CREATE_NEW_CONSOLE,nullptr,nullptr,&si,&pi))fail("管理者認証用のウィンドウを開けません。");
 WaitForSingleObject(pi.hProcess,INFINITE);DWORD code=1;GetExitCodeProcess(pi.hProcess,&code);CloseHandle(pi.hThread);CloseHandle(pi.hProcess);return int(code);
}
void swapEnable(const fs::path& ssh,const std::vector<std::wstring>& baseArgs){
 auto args=baseArgs;args.push_back(L"python3 -B \"$HOME/.local/share/frame-pcvr-easy/swap_admin.py\" prepare");std::string output;
 if(process(ssh,args,nullptr,0,true,&output))fail("補助スワップの準備に失敗しました。上の説明を確認してください。");
 if(!jsonActive(output)){
  log(L"別ウィンドウでFrameのsudoパスワードを入力し、完了後Enterを押してください。追加8GiBだけを有効化します。");
  if(adminConsole(ssh,baseArgs,true))fail("PCVR本体は導入済みですが、補助スワップの有効化は未完了です。「補助スワップ8GiB」から再試行できます。");
 }
 args=baseArgs;args.push_back(L"python3 -B \"$HOME/.local/share/frame-pcvr-easy/swap_admin.py\" status");output.clear();
 if(process(ssh,args,nullptr,0,true,&output)||!jsonActive(output))fail("補助スワップの有効化を確認できません。");
 log(L"補助スワップを確認しました。既存の圧縮スワップを優先します。再起動後はこのボタンで再度有効にしてください。");
}
void work(std::wstring host,Action action,bool includeSwap){
 const bool uninstall=action==Action::Uninstall;

 try{
  if(host.empty()||host.size()>253||!std::regex_match(utf8(host),std::regex("[A-Za-z0-9][A-Za-z0-9.-]*")))fail("Frame名またはIPアドレスを確認してください。");
  auto data=localData(),ssh=sshTool(L"ssh.exe"),keygen=sshTool(L"ssh-keygen.exe");
  log(L"Frameを探しています…");DWORD code=0;auto properties=http(host,L"/properties.json",nullptr,code);
  if(code!=200||jsonString(properties,"login")!="steamos")fail("Steam Frameの開発者サービスを確認できません。接続先を確認してください。");
  auto key=data/L"pairing_rsa";
  if(!fs::exists(key)){log(L"このPC専用のペアリングキーを作成しています…");if(process(keygen,{L"-q",L"-t",L"rsa",L"-b",L"3072",L"-N",L"",L"-C",L"SteamFrame_Standalone_PCVR_VRChat",L"-f",key.wstring()}))fail("ペアリングキーを作成できません。");}
  auto args=sshArgs(data,host);auto test=args;test.push_back(L"true");
  if(process(ssh,test)!=0){
   log(L"Frameの「設定 → 開発者 → 新しいホストをペアリング」を開き、SteamFrame_Standalone_PCVR_VRChatからの要求を承認してください。");
   auto pub=bytes(fs::path(key.wstring()+L".pub"));while(!pub.empty()&&(pub.back()=='\n'||pub.back()=='\r'))pub.pop_back();pub+=" 900b919520e4cf601998a71eec318fec";
   auto deadline=std::chrono::steady_clock::now()+std::chrono::minutes(3);bool registered=false;
   while(std::chrono::steady_clock::now()<deadline){auto response=http(host,L"/register",&pub,code);if(code==200){registered=true;break;}if(code!=403||response.find("pairing mode")==std::string::npos)fail("Frameでペアリングが承認されませんでした。開発者モードと承認画面を確認してください。");std::this_thread::sleep_for(std::chrono::seconds(2));}
   if(!registered)fail("ペアリングを待つ時間が終了しました。もう一度実行してください。");
   if(process(ssh,test)!=0)fail("承認後のSSH接続に失敗しました。保存済みの接続先情報を確認してください。");
  }
  if(action==Action::Swap){swapEnable(ssh,args);PostMessageW(window,DoneMessage,1,2);return;}
  const auto pairedArgs=args;
  log(uninstall?L"アンインストールを開始します…":L"承認を確認しました。パッケージを転送してインストールを開始します…");
  HRSRC resource=FindResourceW(nullptr,MAKEINTRESOURCEW(101),RT_RCDATA);if(!resource)fail("Package resource missing");HGLOBAL loaded=LoadResource(nullptr,resource);const void* package=LockResource(loaded);DWORD size=SizeofResource(nullptr,resource);
  const auto command=std::string("python3 -c '")+FRAME_BOOTSTRAP+"' ";
  args.push_back(wide(command+(uninstall?"uninstall":"install")));
  int result=process(ssh,args,package,size,true);
  if(!uninstall&&result==42){
   if(SendMessageW(window,AndroidMessage,0,0)!=IDYES)fail("Android版の削除とPCVR版の導入を中止しました。Android版は削除していません。");
   args.back()=wide(command+"install-replace-android");
   result=process(ssh,args,package,size,true);
  }
  if(uninstall&&result==43){
   log(L"補助スワップを解除します。別ウィンドウでFrameのsudoパスワードを入力してください。");
   if(adminConsole(ssh,pairedArgs,false))fail("補助スワップを解除できないため、アンインストールを停止しました。VRChatを終了して再試行してください。");
   result=process(ssh,args,package,size,true);
  }
  if(result)fail(uninstall?"アンインストールは完了していません。上のメッセージを確認してください。":"インストールは完了していません。上のメッセージを確認してください。");
  if(!uninstall&&includeSwap)swapEnable(ssh,pairedArgs);
  PostMessageW(window,DoneMessage,1,uninstall?1:0);
 }catch(const std::exception& e){log(wide(e.what()));PostMessageW(window,DoneMessage,0,0);}
}
void setBusy(bool value){busy=value;EnableWindow(hostBox,!value);EnableWindow(termsBox,!value);EnableWindow(removeButton,!value);EnableWindow(swapBox,!value);EnableWindow(swapButton,!value);EnableWindow(installButton,!value&&SendMessageW(termsBox,BM_GETCHECK,0,0)==BST_CHECKED);}
bool saveOwnWindowPreview(){
 RECT rect{};GetClientRect(window,&rect);int width=rect.right-rect.left,height=rect.bottom-rect.top;
 if(width<=0||height<=0||width>2048||height>2048)return false;
 HDC source=GetWindowDC(window),memory=CreateCompatibleDC(source);HBITMAP bitmap=CreateCompatibleBitmap(source,width,height);auto old=SelectObject(memory,bitmap);
 SendMessageW(window,WM_PRINT,reinterpret_cast<WPARAM>(memory),PRF_CLIENT|PRF_ERASEBKGND|PRF_CHILDREN);SelectObject(memory,old);
 BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=width;info.bmiHeader.biHeight=-height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
 std::vector<char> pixels(size_t(width)*height*4);int lines=GetDIBits(source,bitmap,0,UINT(height),pixels.data(),&info,DIB_RGB_COLORS);
 DeleteObject(bitmap);DeleteDC(memory);ReleaseDC(window,source);if(lines!=height)return false;
 BITMAPFILEHEADER header{};header.bfType=0x4d42;header.bfOffBits=sizeof(header)+sizeof(info.bmiHeader);header.bfSize=header.bfOffBits+DWORD(pixels.size());
 std::ofstream out(moduleFolder()/L"FrameSetup-preview.bmp",std::ios::binary);out.write(reinterpret_cast<const char*>(&header),sizeof(header));out.write(reinterpret_cast<const char*>(&info.bmiHeader),sizeof(info.bmiHeader));out.write(pixels.data(),std::streamsize(pixels.size()));
 std::ofstream controls(moduleFolder()/L"FrameSetup-controls.txt");for(int id=10;id<=18;++id){auto h=GetDlgItem(window,id);wchar_t text[1024];GetWindowTextW(h,text,1024);controls<<id<<" visible="<<IsWindowVisible(h)<<" "<<utf8(text)<<"\n";}return bool(out)&&bool(controls);
}
LRESULT CALLBACK procedure(HWND w,UINT message,WPARAM a,LPARAM b){
 switch(message){
 case WM_CREATE:{auto h=reinterpret_cast<LPCREATESTRUCTW>(b)->hInstance;HFONT font=CreateFontW(-17,0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Segoe UI");
  auto control=[&](const wchar_t* type,const wchar_t* text,DWORD style,int x,int y,int width,int height,int id){HWND v=CreateWindowExW(type==std::wstring(L"EDIT")?WS_EX_CLIENTEDGE:0,type,text,WS_CHILD|WS_VISIBLE|style,x,y,width,height,w,reinterpret_cast<HMENU>(INT_PTR(id)),h,nullptr);SendMessageW(v,WM_SETFONT,reinterpret_cast<WPARAM>(font),TRUE);return v;};
  control(L"STATIC",L"Steam Frame PCVR for VRChat 0.3.6（アルファ）",0,24,20,610,28,0);
  control(L"STATIC",L"by JITOME ／ DFR + H.264・音声再開修正 + 補助スワップ",0,24,52,610,25,0);
  instructionBox=control(L"STATIC",L"同じネットワークに接続したFrameで、開発者モードを有効にしてください。",0,24,88,610,43,0);
  control(L"STATIC",L"Frame名 / IP",0,24,139,130,25,0);hostBox=control(L"EDIT",L"frame.local",ES_AUTOHSCROLL|WS_TABSTOP,155,135,475,28,10);
  termsBox=control(L"BUTTON",L"MITライセンス・無保証の説明を確認しました",BS_AUTOCHECKBOX|WS_TABSTOP,24,178,610,28,11);
  installButton=control(L"BUTTON",L"インストール",BS_DEFPUSHBUTTON|WS_TABSTOP,24,218,195,40,12);
  removeButton=control(L"BUTTON",L"アンインストール",WS_TABSTOP,235,218,195,40,13);
  control(L"BUTTON",L"利用許諾・ソース",WS_TABSTOP,446,218,184,40,14);
  swapBox=control(L"BUTTON",L"8GiB補助スワップも有効化（Frame再起動まで・管理者認証）",BS_AUTOCHECKBOX|WS_TABSTOP,24,265,610,28,17);
  SendMessageW(swapBox,BM_SETCHECK,BST_CHECKED,0);
  control(L"STATIC",L"Android版がある場合は、削除の確認後にPCVR版を導入します。",0,24,302,610,32,16);
  swapButton=control(L"BUTTON",L"補助スワップ8GiB",WS_TABSTOP,24,339,230,34,18);
  control(L"STATIC",L"導入後・再起動後の有効化に使えます。",0,269,344,365,28,0);
  logBox=control(L"EDIT",L"インストールを押すと、Frameでペアリングの承認を案内します。\r\n",ES_MULTILINE|ES_READONLY|ES_AUTOVSCROLL|WS_VSCROLL,24,385,606,188,15);
  setBusy(false);return 0;}
 case WM_COMMAND:{int id=LOWORD(a);if(id==11){setBusy(false);return 0;}if(id==14){try{showLicenseAndSource(w);}catch(const std::exception& e){log(wide(e.what()));}return 0;}
  if((id==12||id==13||id==18)&&!busy){if(id==12&&SendMessageW(termsBox,BM_GETCHECK,0,0)!=BST_CHECKED)return 0;wchar_t host[512];GetWindowTextW(hostBox,host,512);
   SetWindowTextW(instructionBox,L"Frameで「新しいホストをペアリング」を開き、\r\nSteamFrame_Standalone_PCVR_VRChatを承認してください。");
   bool includeSwap=SendMessageW(swapBox,BM_GETCHECK,0,0)==BST_CHECKED;setBusy(true);std::thread(work,std::wstring(host),id==13?Action::Uninstall:id==18?Action::Swap:Action::Install,includeSwap).detach();return 0;}break;}
 case LogMessage:{auto s=reinterpret_cast<std::wstring*>(b);int n=GetWindowTextLengthW(logBox);if(n>1000000)SetWindowTextW(logBox,L"");SendMessageW(logBox,EM_SETSEL,GetWindowTextLengthW(logBox),-1);auto text=*s+L"\r\n";SendMessageW(logBox,EM_REPLACESEL,FALSE,reinterpret_cast<LPARAM>(text.c_str()));delete s;return 0;}
 case AndroidMessage:return MessageBoxW(w,L"Android版VRChatがインストールされています。\n\nAndroid版をSteamでアンインストールしてから、PCVR版を導入しますか？\nローカルの設定・データを失う可能性があります。必要なデータの保管が済んでから「はい」を選んでください。\n\n「いいえ」で削除せず中止します。APKとWindows版の混在・版不明の場合は自動削除しません。",L"Android版VRChatの削除確認",MB_YESNO|MB_ICONWARNING|MB_DEFBUTTON2);
 case DoneMessage:setBusy(false);if(a)MessageBoxW(w,b==2?L"補助スワップを有効化しました。再起動後の自動有効化は設定していません。":b?L"アンインストールが完了しました。元の起動設定へ戻しました。":L"インストールが完了しました。FrameのSteamからVRChatをVRモードで起動してください。",L"完了",MB_OK|MB_ICONINFORMATION);return 0;
 case WM_TIMER:if(uiSmoke&&a==99){KillTimer(w,99);uiSmokeResult=saveOwnWindowPreview()?0:9;DestroyWindow(w);return 0;}break;
 case WM_CLOSE:if(busy){MessageBoxW(w,L"処理中です。完了またはエラーの表示をお待ちください。",L"セットアップ",MB_OK);return 0;}DestroyWindow(w);return 0;
 case WM_DESTROY:PostQuitMessage(0);return 0;
 }return DefWindowProcW(w,message,a,b);
}
int selfTest(){
 if(!jsonActive("{\"active\": true}")||jsonActive("{\"active\": false}"))return 11;
 std::vector<std::wstring> cases={L"",L"with spaces",L"ends\\",L"quoted\"value",L"日本語の名前"};std::wstring cmd=L"program";
 for(auto& s:cases)cmd+=L" "+quote(s);
 int count=0;auto args=CommandLineToArgvW(cmd.c_str(),&count);if(!args||count!=int(cases.size()+1))return 3;
 for(size_t i=0;i<cases.size();++i)if(args[i+1]!=cases[i]){LocalFree(args);return 4;}LocalFree(args);
 if(jsonString("{\"message\":\"line\\nquote\\\"\"}","message")!="line\nquote\"")return 5;
 auto r=FindResourceW(nullptr,MAKEINTRESOURCEW(101),RT_RCDATA);if(!r||SizeofResource(nullptr,r)<10000)return 6;
 if(!fs::is_regular_file(sshTool(L"ssh.exe")))return 10;
 return 0;
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE,LPWSTR parameters,int show){
 if(!wcscmp(parameters,L"--self-test"))return selfTest();
 if(!wcscmp(parameters,L"--protocol-smoke-test")){try{DWORD code=0;auto body=http(L"127.0.0.1",L"/properties.json",nullptr,code);return selfTest()==0&&code==200&&jsonString(body,"login")=="steamos"?0:7;}catch(...){return 8;}}
 uiSmoke=!wcscmp(parameters,L"--ui-smoke-test");
 SetProcessDPIAware();WNDCLASSW wc{};wc.lpfnWndProc=procedure;wc.hInstance=instance;wc.hCursor=LoadCursorW(nullptr,IDC_ARROW);wc.hbrBackground=reinterpret_cast<HBRUSH>(COLOR_WINDOW+1);wc.lpszClassName=L"FramePCVRSetup";RegisterClassW(&wc);window=CreateWindowExW(0,wc.lpszClassName,L"Steam Frame PCVR for VRChat",WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU|WS_MINIMIZEBOX,CW_USEDEFAULT,CW_USEDEFAULT,680,650,nullptr,nullptr,instance,nullptr);ShowWindow(window,uiSmoke?SW_SHOWNOACTIVATE:show);if(uiSmoke)SetTimer(window,99,1200,nullptr);MSG message;while(GetMessageW(&message,nullptr,0,0)>0){TranslateMessage(&message);DispatchMessageW(&message);}return uiSmokeResult;
}
