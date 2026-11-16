#include <windows.h>
#include "PIDefines.h"
#include "PIFilter.h"
#include "PIAbout.h"
#include "PIActions.h"
#include "ui.h"
#include <algorithm>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>

namespace {
constexpr DescriptorKeyID settings_key='d8st';
struct HostError { int16 code; };
struct Session { bool show_dialog=false,success=false;digital8::Settings settings; };
void check(OSErr error){if(error)throw HostError{int16(error)};}
void clear_rects(FilterRecord& f){f.inRect={};f.outRect={};f.maskRect={};if(f.bigDocumentData){f.bigDocumentData->inRect32={};f.bigDocumentData->outRect32={};f.bigDocumentData->maskRect32={};}}
VRect rectangle(int top,int left,int bottom,int right){VRect r{};r.top=top;r.left=left;r.bottom=bottom;r.right=right;return r;}
VRect area(FilterRecord& f){if(f.bigDocumentData){f.bigDocumentData->PluginUsing32BitCoordinates=1;return f.bigDocumentData->filterRect32;}return rectangle(f.filterRect.top,f.filterRect.left,f.filterRect.bottom,f.filterRect.right);}
void request(FilterRecord& f,VRect rect,bool output){
    clear_rects(f);if(f.bigDocumentData){if(output)f.bigDocumentData->outRect32=rect;else f.bigDocumentData->inRect32=rect;}
    else {if(rect.top<-32768||rect.left<-32768||rect.bottom>32767||rect.right>32767)throw HostError{filterBadParameters};Rect r{};r.top=int16(rect.top);r.left=int16(rect.left);r.bottom=int16(rect.bottom);r.right=int16(rect.right);if(output)f.outRect=r;else f.inRect=r;}
}
void require_handles(FilterRecord& f){auto* h=f.handleProcs;if(!h||!h->newProc||!h->disposeProc||!h->lockProc||!h->unlockProc||!h->getSizeProc)throw HostError{filterBadParameters};}
std::string text(FilterRecord& f,Handle h){
    if(!h)return {};require_handles(f);int32 n=f.handleProcs->getSizeProc(h);if(n<=0||n>32768)throw std::runtime_error("Invalid Digital8 settings length");
    auto* p=f.handleProcs->lockProc(h,false);if(!p)throw std::bad_alloc();std::string out;
    try{out.assign(p,size_t(n));}catch(...){f.handleProcs->unlockProc(h);throw;}f.handleProcs->unlockProc(h);return out;
}
Handle handle(FilterRecord& f,const std::string& str){
    require_handles(f);Handle h=f.handleProcs->newProc(int32(str.size()));if(!h)throw std::bad_alloc();auto* p=f.handleProcs->lockProc(h,false);
    if(!p){f.handleProcs->disposeProc(h);throw std::bad_alloc();}std::memcpy(p,str.data(),str.size());f.handleProcs->unlockProc(h);return h;
}
bool read_descriptor(FilterRecord& f,digital8::Settings& settings,bool& show){
    auto* d=f.descriptorParameters;if(!d||!d->descriptor)return false;
    auto* r=d->readDescriptorProcs;if(!r||!r->openReadDescriptorProc||!r->closeReadDescriptorProc||!r->getKeyProc||!r->getTextProc)throw HostError{filterBadParameters};
    DescriptorKeyIDArray keys={settings_key,0};PIReadDescriptor token=r->openReadDescriptorProc(d->descriptor,keys);if(!token)throw HostError{filterBadParameters};
    DescriptorKeyID key=0;DescriptorTypeID type=0;int32 flags=0;bool found=false;std::string saved;OSErr error=noErr;
    try{while(r->getKeyProc(token,&key,&type,&flags))if(key==settings_key){Handle h=nullptr;check(r->getTextProc(token,&h));try{saved=text(f,h);}catch(...){if(h)f.handleProcs->disposeProc(h);throw;}if(h)f.handleProcs->disposeProc(h);found=true;}}
    catch(...){r->closeReadDescriptorProc(token);throw;}
    error=r->closeReadDescriptorProc(token);check(error);
    if(!found)throw std::runtime_error("This action has no Digital8 settings snapshot");
    settings=digital8::decode(saved);show=d->playInfo==plugInDialogDisplay;return true;
}
void write_descriptor(FilterRecord& f,const digital8::Settings& settings){
    auto* d=f.descriptorParameters;if(!d)return;auto* w=d->writeDescriptorProcs;
    if(!w||!w->openWriteDescriptorProc||!w->putTextProc||!w->closeWriteDescriptorProc)throw HostError{filterBadParameters};
    Handle value=handle(f,digital8::encode(settings));PIWriteDescriptor token=w->openWriteDescriptorProc();
    if(!token){f.handleProcs->disposeProc(value);throw HostError{filterBadParameters};}
    OSErr put=w->putTextProc(token,settings_key,value);f.handleProcs->disposeProc(value);
    PIDescriptorHandle saved=nullptr;OSErr close=w->closeWriteDescriptorProc(token,&saved);
    if(put||close){if(saved)f.handleProcs->disposeProc(saved);check(put?put:close);}
    if(d->descriptor)f.handleProcs->disposeProc(d->descriptor);d->descriptor=saved;d->recordInfo=plugInDialogOptional;
}
void abort_if_needed(FilterRecord& f){if(f.abortProc&&f.abortProc())throw HostError{-128};}
digital8::Image read_image(FilterRecord& f,VRect rect){
    int64_t width=int64_t(rect.right)-rect.left,height=int64_t(rect.bottom)-rect.top;
    if(width<=0||height<=0||width*height>8000000)throw std::runtime_error("Digital8 Native 0.1 supports up to 8 million pixels per filter region");
    digital8::Image image{int(width),int(height),std::vector<uint8_t>(size_t(width*height)*4,255)};
    int planes=f.inTransparencyMask?4:3;
    for(int c=0;c<planes;c++)for(int y=rect.top;y<rect.bottom;){
        abort_if_needed(f);int end=int(std::min(int64_t(rect.bottom),int64_t(y)+128));request(f,rectangle(y,rect.left,end,rect.right),false);f.inLoPlane=f.inHiPlane=int16(c);check(f.advanceState());
        if(!f.inData)throw HostError{filterBadParameters};int stride=f.inColumnBytes?f.inColumnBytes:1;
        for(int row=y;row<end;row++){auto* source=static_cast<const uint8_t*>(f.inData)+ptrdiff_t(row-y)*f.inRowBytes;
            for(int x=0;x<width;x++)image.rgba[(size_t(row-rect.top)*size_t(width)+x)*4+c]=source[ptrdiff_t(x)*stride];}
        y=end;
    }
    clear_rects(f);return image;
}
void write_image(FilterRecord& f,VRect rect,const digital8::Image& image){
    int planes=f.outTransparencyMask?4:3;
    for(int c=0;c<planes;c++)for(int y=rect.top;y<rect.bottom;){
        abort_if_needed(f);int end=int(std::min(int64_t(rect.bottom),int64_t(y)+128));request(f,rectangle(y,rect.left,end,rect.right),true);f.outLoPlane=f.outHiPlane=int16(c);check(f.advanceState());
        if(!f.outData)throw HostError{filterBadParameters};int stride=f.outColumnBytes?f.outColumnBytes:1;
        for(int row=y;row<end;row++){auto* dest=static_cast<uint8_t*>(f.outData)+ptrdiff_t(row-y)*f.outRowBytes;
            for(int x=0;x<image.width;x++)dest[ptrdiff_t(x)*stride]=image.rgba[(size_t(row-rect.top)*image.width+x)*4+c];}
        y=end;
    }
    clear_rects(f);check(f.advanceState());
}
void start(FilterRecord& f,Session& state){
    if(f.depth!=8||f.imageMode!=plugInModeRGBColor||f.planes<3||(f.inLayerPlanes&&f.inLayerPlanes!=3)||(f.outLayerPlanes&&f.outLayerPlanes!=3))throw HostError{filterBadMode};
    if(!f.advanceState)throw HostError{filterBadParameters};require_handles(f);VRect rect=area(f);
    state.settings=digital8::defaults();bool show=state.show_dialog;
    if(f.parameters)state.settings=digital8::decode(text(f,f.parameters));else show=true;
    read_descriptor(f,state.settings,show);f.autoMask=true;f.inputRate=f.maskRate=1<<16;
    auto image=read_image(f,rect);HWND owner=f.platformData?reinterpret_cast<HWND>(static_cast<PlatformData*>(f.platformData)->hwnd):nullptr;
    if(show&&!digital8::configure(state.settings,image,owner))throw HostError{-128};
    if(!f.outTransparencyMask&&(state.settings.alpha_mode||state.settings.white_transparent))throw std::runtime_error("Transparency changes require an unlocked RGB layer with editable transparency");
    digital8::Hooks hooks;hooks.cancelled=[&f](){return f.abortProc&&f.abortProc();};hooks.progress=[&f](int percent){if(f.progressProc)f.progressProc(percent,100);};
    auto result=digital8::render(image,state.settings,hooks);abort_if_needed(f);
    // Allocate the new snapshot before writing any pixels, so low-memory
    // failures cannot leave a partially updated image or settings handle.
    Handle saved=handle(f,digital8::encode(state.settings));
    try{write_image(f,rect,result);}catch(...){f.handleProcs->disposeProc(saved);throw;}
    if(f.parameters)f.handleProcs->disposeProc(f.parameters);f.parameters=saved;state.success=true;
}
void report(FilterRecord& f,const char* message,int16& result){
    result=errReportString;if(!f.errorString){result=filterBadParameters;return;}
    size_t n=std::min(size_t(255),std::strlen(message));(*f.errorString)[0]=uint8(n);std::memcpy(&(*f.errorString)[1],message,n);
}
}

// Instance state is owned by Photoshop's opaque data slot, never shared across
// documents, preview calls or simultaneous smart-filter reevaluations.
extern "C" __declspec(dllexport) void MACPASCAL PluginMain(const int16 selector,FilterRecordPtr record,intptr_t* data,int16* result){
    if(!result)return;*result=noErr;
    if(selector==filterSelectorAbout){MessageBoxW(nullptr,L"Digital8 Native 0.2.1\nPC-8801 / PC-9801 / アニメ塗り",L"Digital8",MB_OK);return;}
    if(!record||!data){*result=filterBadParameters;return;}auto& f=*record;
    try{
        if(selector==filterSelectorFinish){std::unique_ptr<Session> st(reinterpret_cast<Session*>(*data));*data=0;if(st&&st->success)write_descriptor(f,st->settings);clear_rects(f);return;}
        if(selector==filterSelectorContinue){clear_rects(f);return;}
        if(selector!=filterSelectorParameters&&selector!=filterSelectorPrepare&&selector!=filterSelectorStart)return;
        if(!*data)*data=reinterpret_cast<intptr_t>(new Session{});auto& state=*reinterpret_cast<Session*>(*data);
        if(selector==filterSelectorParameters){state.show_dialog=true;return;}
        if(selector==filterSelectorPrepare){auto r=area(f);int64_t n=std::max(int64_t(0),int64_t(r.right)-r.left)*std::max(int64_t(0),int64_t(r.bottom)-r.top);f.bufferSpace=0;f.bufferSpace64=std::min(int64_t(1600000000),n*160+64000000);return;}
        start(f,state);
    }catch(const HostError& e){*result=e.code;}catch(const std::bad_alloc&){*result=-108;}catch(const std::exception& e){if(std::string(e.what())=="cancelled")*result=-128;else report(f,e.what(),*result);}catch(...){report(f,"Unexpected Digital8 Native error",*result);}
    if(*result!=noErr){clear_rects(f);delete reinterpret_cast<Session*>(*data);*data=0;}
}
