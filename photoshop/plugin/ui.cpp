#include "ui.h"
#include "resources.h"
#include <commctrl.h>
#include <commdlg.h>
#include <dwmapi.h>
#include <windowsx.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cwchar>
#include <stdexcept>
#include <thread>

namespace digital8 {
namespace {
constexpr UINT preview_done=WM_APP+1,preview_progress=WM_APP+2;
const COLORREF background=RGB(46,46,46), field=RGB(32,32,32), foreground=RGB(224,224,224);
struct Spec { int id,page,row; const wchar_t* label; double minimum,maximum,step; };
const Spec specs[]={
 {IDC_PIXEL,0,0,L"仮想ピクセル",1,64,1},{IDC_THRESHOLD,0,1,L"主線しきい値",0,120,1},
 {IDC_EDGE,0,2,L"輪郭抽出",0,1,.01},{IDC_GROWTH,0,3,L"線の太さ",0,16,.25},
 {IDC_TRACE_STRENGTH,0,4,L"色トレスの明るさ",.1,.95,.01},{IDC_ALPHA_THRESHOLD,0,5,L"透明度のしきい値",0,255,1},
 {IDC_TOLERANCE,1,0,L"面をまとめる色差",0,128,1},{IDC_RADIUS,1,1,L"周囲をならす半径",0,8,1},
 {IDC_AREA,1,2,L"小さい面の整理",0,4096,1},{IDC_COLOURS,1,3,L"アニメ塗りの色数",0,256,1},
 {IDC_GROUPS,1,4,L"88/98の共通色区分数",2,64,1},
 {IDC_BRIGHTNESS,3,0,L"明るさ",0,10,.01},{IDC_SATURATION,3,1,L"彩度",0,10,.01},
 {IDC_CONTRAST,3,2,L"コントラスト",0,10,.01},{IDC_RED,3,3,L"赤",0,10,.01},
 {IDC_GREEN,3,4,L"緑",0,10,.01},{IDC_BLUE,3,5,L"青",0,10,.01}
};
struct Row { const Spec* spec; HWND label,slider,edit,spin; };
struct State {
 Settings settings; const Image* source=nullptr;
 Image result; std::thread worker; std::atomic<bool> cancel{false};
 bool busy=false,close_after=false,filling=false; int page=0,selected_colour=0;
 std::string error; COLORREF custom_colours[16]{}; std::vector<Row> rows;
 HBRUSH bg=CreateSolidBrush(background),edit_bg=CreateSolidBrush(field); HWND viewer=nullptr; HMODULE module=nullptr;
};
std::wstring wide(const char* text){int n=MultiByteToWideChar(CP_UTF8,0,text,-1,nullptr,0);std::wstring out(n,L'\0');MultiByteToWideChar(CP_UTF8,0,text,-1,out.data(),n);out.pop_back();return out;}
void number(HWND d,int id,double n){wchar_t b[64];swprintf_s(b,L"%.4g",n);SetDlgItemTextW(d,id,b);}
double value(HWND d,int id){wchar_t b[64],*end=nullptr;GetDlgItemTextW(d,id,b,64);double n=std::wcstod(b,&end);if(end==b||*end||!std::isfinite(n))throw std::runtime_error("Enter a valid number in every field.");return n;}
int integer(HWND d,int id){double n=value(d,id);if(std::floor(n)!=n||n<0||n>100000)throw std::runtime_error("An integer setting contains a fractional or invalid value.");return int(n);}
void combo(HWND d,int id,std::initializer_list<const wchar_t*> values){for(auto text:values)SendDlgItemMessageW(d,id,CB_ADDSTRING,0,reinterpret_cast<LPARAM>(text));}
int choice(HWND d,int id){return int(SendDlgItemMessageW(d,id,CB_GETCURSEL,0,0));}
void select(HWND d,int id,int index){SendDlgItemMessageW(d,id,CB_SETCURSEL,index,0);}
void rectangle(HDC dc,RECT r,COLORREF colour){HBRUSH b=CreateSolidBrush(colour);FillRect(dc,&r,b);DeleteObject(b);}
RECT units(HWND d,int x,int y,int w,int h){RECT r{x,y,x+w,y+h};MapDialogRect(d,&r);return r;}
HWND control(HWND d,const wchar_t* cls,const wchar_t* text,int id,DWORD style,int x,int y,int w,int h){
 auto r=units(d,x,y,w,h);HWND c=CreateWindowExW(0,cls,text,WS_CHILD|style,r.left,r.top,r.right-r.left,r.bottom-r.top,d,reinterpret_cast<HMENU>(INT_PTR(id)),nullptr,nullptr);
 SendMessageW(c,WM_SETFONT,SendMessageW(d,WM_GETFONT,0,0),TRUE);return c;
}
void sync_slider(HWND d,Row& row){try{double n=value(d,row.spec->id);int position=int(std::lround((n-row.spec->minimum)/row.spec->step));SendMessageW(row.slider,TBM_SETPOS,TRUE,position);}catch(...){}}
void rows(HWND d,State& st){for(auto& spec:specs){
 int y=128+spec.row*17;Row row{&spec};
 row.label=control(d,L"STATIC",spec.label,6000+spec.id,SS_LEFT,18,y+2,104,13);
 row.slider=control(d,TRACKBAR_CLASSW,L"",2000+spec.id,TBS_NOTICKS|WS_TABSTOP,124,y,147,15);
 row.edit=control(d,L"EDIT",L"",spec.id,WS_TABSTOP|WS_BORDER|ES_AUTOHSCROLL,280,y,55,15);
 row.spin=control(d,UPDOWN_CLASSW,L"",4000+spec.id,UDS_ARROWKEYS|UDS_HOTTRACK,335,y,13,15);
 SendMessageW(row.spin,UDM_SETBUDDY,reinterpret_cast<WPARAM>(row.edit),0);
 SendMessageW(row.spin,UDM_SETRANGE32,static_cast<WPARAM>(-100000),100000);
 SendMessageW(row.slider,TBM_SETRANGEMIN,FALSE,0);SendMessageW(row.slider,TBM_SETRANGEMAX,FALSE,int(std::lround((spec.maximum-spec.minimum)/spec.step)));
 st.rows.push_back(row);
}}
void page(HWND d,State& st){
 for(auto& row:st.rows){bool on=row.spec->page==st.page;bool enabled=!st.busy;
  if(row.spec->id==IDC_COLOURS)enabled&=st.settings.machine==2;
  if(row.spec->id==IDC_GROUPS)enabled&=st.settings.machine!=2;
  for(HWND c:{row.label,row.slider,row.edit,row.spin}){ShowWindow(c,on?SW_SHOW:SW_HIDE);EnableWindow(c,enabled);}
 }
 for(int id:{IDC_TALL,IDC_TRACE,IDC_ALPHA,IDC_WHITE}){ShowWindow(GetDlgItem(d,id),st.page==0?SW_SHOW:SW_HIDE);EnableWindow(GetDlgItem(d,id),!st.busy);}
 for(int id:{IDC_HALF,IDC_QUARTER,IDC_EIGHTH,IDC_THREE112,IDC_THREE111,1400,1401,1402,1403,1404}){
  ShowWindow(GetDlgItem(d,id),st.page==2?SW_SHOW:SW_HIDE);EnableWindow(GetDlgItem(d,id),!st.busy&&st.settings.machine!=2);
 }
 for(int id:{IDC_MACHINE,IDC_PALETTE,IDC_EDIT_COLOUR,IDC_PALETTE_GRID,IDC_PREVIEW,IDOK,IDC_PAGE_COMMON,IDC_PAGE_FILL,IDC_PAGE_PATTERN,IDC_PAGE_COLOUR})EnableWindow(GetDlgItem(d,id),!st.busy);
 for(int id=IDC_PAGE_COMMON;id<=IDC_PAGE_COLOUR;id++)InvalidateRect(GetDlgItem(d,id),nullptr,TRUE);
}
void palette_info(HWND d,State& st){
 st.selected_colour=std::clamp(st.selected_colour,0,st.settings.colour_count-1);auto c=st.settings.colours[st.selected_colour];wchar_t label[180];
 swprintf_s(label,L"%d色  /  %d番   RGB %d, %d, %d   #%02X%02X%02X",st.settings.colour_count,st.selected_colour+1,c.r,c.g,c.b,c.r,c.g,c.b);
 SetDlgItemTextW(d,IDC_PALETTE_INFO,label);InvalidateRect(GetDlgItem(d,IDC_PALETTE_GRID),nullptr,TRUE);
}
void fill(HWND d,State& st){const auto& s=st.settings;st.filling=true;
 select(d,IDC_MACHINE,s.machine);SendDlgItemMessageW(d,IDC_PALETTE,CB_RESETCONTENT,0,0);int selected=0,pos=0;
 for(size_t i=0;i<palettes().size();i++)if(palettes()[i].machine==s.machine){auto label=wide(palettes()[i].label);int item=int(SendDlgItemMessageW(d,IDC_PALETTE,CB_ADDSTRING,0,reinterpret_cast<LPARAM>(label.c_str())));SendDlgItemMessageW(d,IDC_PALETTE,CB_SETITEMDATA,item,i);if(int(i)==s.palette_index)selected=pos;pos++;}
 select(d,IDC_PALETTE,selected);
 for(auto p:std::initializer_list<std::pair<int,double>>{{IDC_PIXEL,s.pixel_size},{IDC_THRESHOLD,s.threshold},{IDC_EDGE,s.edge},{IDC_TOLERANCE,s.tolerance},{IDC_RADIUS,s.radius},{IDC_AREA,s.min_area},{IDC_COLOURS,s.max_colours},{IDC_GROUPS,s.groups},{IDC_GROWTH,s.growth},{IDC_TRACE_STRENGTH,s.trace_strength},{IDC_ALPHA_THRESHOLD,s.alpha_threshold},{IDC_BRIGHTNESS,s.brightness},{IDC_SATURATION,s.saturation},{IDC_CONTRAST,s.contrast},{IDC_RED,s.red},{IDC_GREEN,s.green},{IDC_BLUE,s.blue}})number(d,p.first,p.second);
 for(auto p:std::initializer_list<std::pair<int,int>>{{IDC_TALL,s.tall_dots},{IDC_TRACE,s.trace},{IDC_WHITE,s.white_transparent},{IDC_ALPHA,s.alpha_mode}})CheckDlgButton(d,p.first,p.second?BST_CHECKED:BST_UNCHECKED);
 select(d,IDC_HALF,s.half);select(d,IDC_QUARTER,s.quarter);select(d,IDC_EIGHTH,s.eighth);select(d,IDC_THREE112,s.three112);select(d,IDC_THREE111,s.three111);
 for(auto& row:st.rows)sync_slider(d,row);palette_info(d,st);page(d,st);st.filling=false;
}
Settings read(HWND d,const Settings& previous){Settings s=previous;
 s.pixel_size=integer(d,IDC_PIXEL);s.threshold=integer(d,IDC_THRESHOLD);s.radius=integer(d,IDC_RADIUS);s.min_area=integer(d,IDC_AREA);s.max_colours=integer(d,IDC_COLOURS);s.groups=integer(d,IDC_GROUPS);s.alpha_threshold=integer(d,IDC_ALPHA_THRESHOLD);
 s.edge=float(value(d,IDC_EDGE));s.tolerance=float(value(d,IDC_TOLERANCE));s.growth=float(value(d,IDC_GROWTH));s.trace_strength=float(value(d,IDC_TRACE_STRENGTH));s.brightness=float(value(d,IDC_BRIGHTNESS));s.saturation=float(value(d,IDC_SATURATION));s.contrast=float(value(d,IDC_CONTRAST));s.red=float(value(d,IDC_RED));s.green=float(value(d,IDC_GREEN));s.blue=float(value(d,IDC_BLUE));
 s.tall_dots=IsDlgButtonChecked(d,IDC_TALL)==BST_CHECKED;s.trace=IsDlgButtonChecked(d,IDC_TRACE)==BST_CHECKED;s.white_transparent=IsDlgButtonChecked(d,IDC_WHITE)==BST_CHECKED;s.alpha_mode=IsDlgButtonChecked(d,IDC_ALPHA)==BST_CHECKED;s.half=choice(d,IDC_HALF);s.quarter=choice(d,IDC_QUARTER);s.eighth=choice(d,IDC_EIGHTH);s.three112=choice(d,IDC_THREE112);s.three111=choice(d,IDC_THREE111);validate(s);return s;
}
void paint_image(HDC dc,RECT rect,const Image& image){
 rectangle(dc,rect,RGB(25,25,25));if(image.rgba.empty())return;
 int width=rect.right-rect.left,height=rect.bottom-rect.top;double scale=std::min(double(width)/image.width,double(height)/image.height);
 int w=std::max(1,int(image.width*scale)),h=std::max(1,int(image.height*scale));std::vector<uint8_t> bgra(size_t(w)*h*4);
 for(int y=0;y<h;y++)for(int x=0;x<w;x++){size_t p=(size_t(y)*w+x)*4,q=(size_t(std::min(image.height-1,y*image.height/h))*image.width+std::min(image.width-1,x*image.width/w))*4;int a=image.rgba[q+3],bg=(x/12+y/12)%2?75:95;for(int c=0;c<3;c++)bgra[p+c]=uint8_t((image.rgba[q+2-c]*a+bg*(255-a)+127)/255);}
 BITMAPINFO info{};info.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);info.bmiHeader.biWidth=w;info.bmiHeader.biHeight=-h;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;info.bmiHeader.biCompression=BI_RGB;
 SetDIBitsToDevice(dc,rect.left+(width-w)/2,rect.top+(height-h)/2,w,h,0,0,0,h,bgra.data(),&info,DIB_RGB_COLORS);
}
LRESULT CALLBACK viewer(HWND window,UINT message,WPARAM wp,LPARAM lp){
 auto* st=reinterpret_cast<State*>(GetWindowLongPtrW(window,GWLP_USERDATA));
 if(message==WM_NCCREATE){st=static_cast<State*>(reinterpret_cast<CREATESTRUCTW*>(lp)->lpCreateParams);SetWindowLongPtrW(window,GWLP_USERDATA,reinterpret_cast<LONG_PTR>(st));}
 if(st&&message==WM_PAINT){PAINTSTRUCT ps{};HDC dc=BeginPaint(window,&ps);RECT r{};GetClientRect(window,&r);bool original=GetKeyState(VK_SPACE)&0x8000;paint_image(dc,r,original?*st->source:st->result);EndPaint(window,&ps);return 0;}
 if(message==WM_KEYDOWN||message==WM_KEYUP||message==WM_SIZE){InvalidateRect(window,nullptr,FALSE);return 0;}
 if(st&&message==WM_DESTROY){st->viewer=nullptr;return 0;}return DefWindowProcW(window,message,wp,lp);
}
void show_viewer(HWND d,State& st){
 if(!st.viewer){WNDCLASSW wc{};wc.hInstance=st.module;wc.lpfnWndProc=viewer;wc.lpszClassName=L"Digital8PreviewV2";wc.hCursor=LoadCursor(nullptr,IDC_ARROW);RegisterClassW(&wc);
  st.viewer=CreateWindowExW(WS_EX_TOOLWINDOW,wc.lpszClassName,L"Digital8 画像プレビュー — Spaceで元画像",WS_OVERLAPPEDWINDOW|WS_VISIBLE,CW_USEDEFAULT,CW_USEDEFAULT,950,700,d,nullptr,st.module,&st);}
 ShowWindow(st.viewer,SW_SHOW);InvalidateRect(st.viewer,nullptr,TRUE);
}
void changed(HWND d,State& st){if(!st.filling)SetDlgItemTextW(d,IDC_STATUS,L"設定を変更しました。「プレビュー更新」で反映します。");}
void draw(HWND d,State& st,const DRAWITEMSTRUCT& item){
 HDC dc=item.hDC;RECT r=item.rcItem;SetBkMode(dc,TRANSPARENT);SetTextColor(dc,foreground);
 if(item.CtlID==IDC_PALETTE_GRID){
  rectangle(dc,r,field);int cols=st.settings.colour_count<=16?st.settings.colour_count:st.settings.colour_count>256?48:25;int rows=(st.settings.colour_count+cols-1)/cols;
  for(int i=0;i<st.settings.colour_count;i++){RECT cell{r.left+(r.right-r.left)*(i%cols)/cols,r.top+(r.bottom-r.top)*(i/cols)/rows,r.left+(r.right-r.left)*(i%cols+1)/cols,r.top+(r.bottom-r.top)*(i/cols+1)/rows};auto c=st.settings.colours[i];rectangle(dc,cell,RGB(c.r,c.g,c.b));FrameRect(dc,&cell,GetStockBrush(BLACK_BRUSH));if(i==st.selected_colour){InflateRect(&cell,-1,-1);FrameRect(dc,&cell,GetStockBrush(WHITE_BRUSH));}}return;
 }
 if(item.CtlID==IDC_PALETTE){
  rectangle(dc,r,item.itemState&ODS_SELECTED?RGB(80,80,80):field);if(item.itemID==UINT(-1))return;
  int id=int(SendDlgItemMessageW(d,IDC_PALETTE,CB_GETITEMDATA,item.itemID,0));if(id<0||size_t(id)>=palettes().size())return;
  const auto& palette=palettes()[id];RECT text=r;text.left+=5;text.right-=112;DrawTextW(dc,wide(palette.label).c_str(),-1,&text,DT_LEFT|DT_VCENTER|DT_SINGLELINE|DT_END_ELLIPSIS);
  int count=std::min(10,int(palette.colours.size()));for(int i=0;i<count;i++){auto c=palette.colours[size_t(i)*palette.colours.size()/count];RECT cell{r.right-108+i*10,r.top+4,r.right-99+i*10,r.bottom-4};rectangle(dc,cell,RGB(c.r,c.g,c.b));}return;
 }
 bool tab=item.CtlID>=IDC_PAGE_COMMON&&item.CtlID<=IDC_PAGE_COLOUR;bool selected=tab&&int(item.CtlID)-IDC_PAGE_COMMON==st.page;
 rectangle(dc,r,selected?RGB(72,72,72):item.itemState&ODS_SELECTED?RGB(90,90,90):RGB(57,57,57));
 if(item.itemState&ODS_DISABLED)SetTextColor(dc,RGB(120,120,120));
 wchar_t label[100];GetWindowTextW(item.hwndItem,label,100);DrawTextW(dc,label,-1,&r,DT_CENTER|DT_VCENTER|DT_SINGLELINE);
 if(tab&&selected){RECT line=r;line.top=line.bottom-2;rectangle(dc,line,RGB(200,200,200));}
 else if(!tab)FrameRect(dc,&r,GetStockBrush(GRAY_BRUSH));
 if(item.itemState&ODS_FOCUS){InflateRect(&r,-3,-3);DrawFocusRect(dc,&r);}
}
INT_PTR CALLBACK dialog(HWND d,UINT message,WPARAM wp,LPARAM lp){
 auto* st=reinterpret_cast<State*>(GetWindowLongPtrW(d,DWLP_USER));
 if(message==WM_INITDIALOG){
  st=reinterpret_cast<State*>(lp);SetWindowLongPtrW(d,DWLP_USER,lp);BOOL dark=TRUE;DwmSetWindowAttribute(d,20,&dark,sizeof(dark));
  combo(d,IDC_MACHINE,{L"PC-8801",L"PC-9801",L"アニメ塗り"});combo(d,IDC_HALF,{L"市松",L"縦縞"});combo(d,IDC_QUARTER,{L"交互",L"片側"});combo(d,IDC_EIGHTH,{L"従来 4×2",L"交互 4×4"});combo(d,IDC_THREE112,{L"なし",L"縞 4×1",L"交互 4×2",L"斜め 4×4"});combo(d,IDC_THREE111,{L"なし",L"縞 3×1",L"斜め 3×3"});
  rows(d,*st);fill(d,*st);SetWindowTextW(d,L"Digital8 Native — 画像プレビュー");
  SetDlgItemTextW(d,IDC_STATUS,L"フィルタ単体のプレビューは別ウィンドウに表示します。");return TRUE;
 }
 if(!st)return FALSE;
 if(message==WM_CTLCOLORDLG)return reinterpret_cast<INT_PTR>(st->bg);
 if(message==WM_CTLCOLOREDIT||message==WM_CTLCOLORLISTBOX){SetTextColor(reinterpret_cast<HDC>(wp),foreground);SetBkColor(reinterpret_cast<HDC>(wp),field);return reinterpret_cast<INT_PTR>(st->edit_bg);}
 if(message==WM_CTLCOLORSTATIC||message==WM_CTLCOLORBTN){SetTextColor(reinterpret_cast<HDC>(wp),IsWindowEnabled(reinterpret_cast<HWND>(lp))?foreground:RGB(135,135,135));SetBkColor(reinterpret_cast<HDC>(wp),background);return reinterpret_cast<INT_PTR>(st->bg);}
 if(message==WM_MEASUREITEM){auto* item=reinterpret_cast<MEASUREITEMSTRUCT*>(lp);item->itemHeight=MulDiv(22,int(GetDpiForWindow(d)),96);return TRUE;}
 if(message==WM_DRAWITEM){draw(d,*st,*reinterpret_cast<DRAWITEMSTRUCT*>(lp));return TRUE;}
 if(message==preview_progress){wchar_t b[80];swprintf_s(b,L"プレビューを計算中… %d%%",int(wp));SetDlgItemTextW(d,IDC_STATUS,b);return TRUE;}
 if(message==preview_done){st->worker.join();st->busy=false;if(st->close_after){EndDialog(d,IDCANCEL);return TRUE;}if(st->error.empty()){show_viewer(d,*st);SetDlgItemTextW(d,IDC_STATUS,L"画像プレビューを更新しました。");}else MessageBoxW(d,wide(st->error.c_str()).c_str(),L"Digital8",MB_OK|MB_ICONERROR);page(d,*st);return TRUE;}
 if(message==WM_CLOSE||(message==WM_COMMAND&&LOWORD(wp)==IDCANCEL)){if(st->busy){st->close_after=true;st->cancel=true;SetDlgItemTextW(d,IDC_STATUS,L"キャンセル中…");}else EndDialog(d,IDCANCEL);return TRUE;}
 if(st->busy)return FALSE;
 try{
  if(message==WM_HSCROLL){for(auto& row:st->rows)if(reinterpret_cast<HWND>(lp)==row.slider){number(d,row.spec->id,row.spec->minimum+SendMessageW(row.slider,TBM_GETPOS,0,0)*row.spec->step);changed(d,*st);return TRUE;}}
  if(message==WM_NOTIFY){auto* header=reinterpret_cast<NMHDR*>(lp);
   if(header->code==UDN_DELTAPOS){for(auto& row:st->rows)if(header->hwndFrom==row.spin){auto* change=reinterpret_cast<NMUPDOWN*>(lp);double n=value(d,row.spec->id)+change->iDelta*row.spec->step;number(d,row.spec->id,std::clamp(n,row.spec->minimum,row.spec->maximum));sync_slider(d,row);SetWindowLongPtrW(d,DWLP_MSGRESULT,1);changed(d,*st);return TRUE;}}
   if(header->code==NM_CUSTOMDRAW){for(auto& row:st->rows)if(header->hwndFrom==row.slider){auto* cd=reinterpret_cast<NMCUSTOMDRAW*>(lp);if(cd->dwDrawStage==CDDS_PREPAINT){rectangle(cd->hdc,cd->rc,background);SetWindowLongPtrW(d,DWLP_MSGRESULT,CDRF_NOTIFYITEMDRAW);return TRUE;}if(cd->dwDrawStage==CDDS_ITEMPREPAINT){if(cd->dwItemSpec==TBCD_CHANNEL)rectangle(cd->hdc,cd->rc,RGB(95,95,95));else if(cd->dwItemSpec==TBCD_THUMB)rectangle(cd->hdc,cd->rc,IsWindowEnabled(row.slider)?RGB(205,205,205):RGB(100,100,100));SetWindowLongPtrW(d,DWLP_MSGRESULT,CDRF_SKIPDEFAULT);return TRUE;}}}
  }
  if(message!=WM_COMMAND)return FALSE;int id=LOWORD(wp),notification=HIWORD(wp);
  if(id>=IDC_PAGE_COMMON&&id<=IDC_PAGE_COLOUR){st->page=id-IDC_PAGE_COMMON;page(d,*st);InvalidateRect(d,nullptr,TRUE);return TRUE;}
  if(notification==EN_CHANGE&&!st->filling){for(auto& row:st->rows)if(row.spec->id==id)sync_slider(d,row);changed(d,*st);return TRUE;}
  if(id==IDC_MACHINE&&notification==CBN_SELCHANGE){st->settings=defaults(choice(d,IDC_MACHINE));st->selected_colour=0;fill(d,*st);changed(d,*st);return TRUE;}
  if(id==IDC_PALETTE&&notification==CBN_SELCHANGE){set_palette(st->settings,int(SendDlgItemMessageW(d,IDC_PALETTE,CB_GETITEMDATA,choice(d,IDC_PALETTE),0)));st->selected_colour=0;palette_info(d,*st);changed(d,*st);return TRUE;}
  if(id==IDC_PALETTE_GRID&&(notification==STN_CLICKED||notification==STN_DBLCLK)){POINT p{};GetCursorPos(&p);ScreenToClient(GetDlgItem(d,id),&p);RECT r{};GetClientRect(GetDlgItem(d,id),&r);int cols=st->settings.colour_count<=16?st->settings.colour_count:st->settings.colour_count>256?48:25,rows=(st->settings.colour_count+cols-1)/cols;st->selected_colour=std::clamp(int(p.y)*rows/std::max(1,int(r.bottom))*cols+int(p.x)*cols/std::max(1,int(r.right)),0,st->settings.colour_count-1);palette_info(d,*st);if(notification==STN_DBLCLK)SendMessageW(d,WM_COMMAND,IDC_EDIT_COLOUR,0);return TRUE;}
  if(id==IDC_EDIT_COLOUR){auto c=st->settings.colours[st->selected_colour];CHOOSECOLORW cc{};cc.lStructSize=sizeof(cc);cc.hwndOwner=d;cc.rgbResult=RGB(c.r,c.g,c.b);cc.lpCustColors=st->custom_colours;cc.Flags=CC_FULLOPEN|CC_RGBINIT;if(ChooseColorW(&cc)){st->settings.colours[st->selected_colour]={GetRValue(cc.rgbResult),GetGValue(cc.rgbResult),GetBValue(cc.rgbResult)};palette_info(d,*st);changed(d,*st);}return TRUE;}
  if(id==IDOK){st->settings=read(d,st->settings);EndDialog(d,IDOK);return TRUE;}
  if(id==IDC_PREVIEW){st->settings=read(d,st->settings);st->busy=true;st->cancel=false;st->error.clear();page(d,*st);SetDlgItemTextW(d,IDC_STATUS,L"プレビューを更新中…");UpdateWindow(d);
   Settings snapshot=st->settings;
   st->worker=std::thread([st,d,snapshot](){try{Hooks h;h.cancelled=[st](){return st->cancel.load();};h.progress=[d](int p){PostMessageW(d,preview_progress,p,0);};st->result=render(*st->source,snapshot,h);}catch(const std::exception& e){st->error=e.what();}catch(...){st->error="Preview failed";}PostMessageW(d,preview_done,0,0);});
   return TRUE;
  }
  if(notification==BN_CLICKED||notification==CBN_SELCHANGE)changed(d,*st);
 }catch(const std::exception& e){MessageBoxW(d,wide(e.what()).c_str(),L"Digital8 — 設定を確認",MB_OK|MB_ICONERROR);}
 return FALSE;
}
}
bool configure(Settings& settings,const Image& source,HWND owner){
 INITCOMMONCONTROLSEX controls{sizeof(controls),ICC_BAR_CLASSES|ICC_UPDOWN_CLASS};InitCommonControlsEx(&controls);
 State st;st.settings=settings;st.source=&source;
 GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,reinterpret_cast<LPCWSTR>(&configure),&st.module);
 INT_PTR result=DialogBoxParamW(st.module,MAKEINTRESOURCEW(IDD_SETTINGS),owner,dialog,reinterpret_cast<LPARAM>(&st));
 if(st.worker.joinable()){st.cancel=true;st.worker.join();}if(st.viewer)DestroyWindow(st.viewer);UnregisterClassW(L"Digital8PreviewV2",st.module);DeleteObject(st.bg);DeleteObject(st.edit_bg);
 if(result==-1)throw std::runtime_error("Cannot open Digital8 settings dialog");if(result==IDOK){settings=st.settings;return true;}return false;
}
}
