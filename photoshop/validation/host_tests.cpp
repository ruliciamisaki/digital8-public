#include <windows.h>
#include "PIDefines.h"
#include "PIFilter.h"
#include "PIActions.h"
#include "../native/renderer.h"
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

using Main=void(MACPASCAL*)(int16,FilterRecordPtr,intptr_t*,int16*);
using Render=int(*)(const char*,unsigned int,const unsigned char*,int,int,unsigned char*,size_t);
namespace {
void expect(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
std::map<Handle,int32> allocations;
Handle MACPASCAL allocate(int32 n){auto h=reinterpret_cast<Handle>(new char*);*h=new char[size_t(n)]{};allocations[h]=n;return h;}
void MACPASCAL dispose(Handle h){if(!h)return;expect(allocations.count(h)!=0,"Double free or foreign handle");delete[] *h;delete h;allocations.erase(h);}
int32 MACPASCAL size(Handle h){return allocations.at(h);}
Ptr MACPASCAL lock(Handle h,Boolean){return *h;}
void MACPASCAL unlock(Handle){}
Handle make(const std::string& text){Handle h=allocate(int32(text.size()));std::memcpy(*h,text.data(),text.size());return h;}
std::string string(Handle h){return std::string(*h,size_t(size(h)));}
struct Reader { std::string text,fill_path;std::set<DescriptorKeyID> required;int key=0; };
struct Writer { std::string text; };
PIReadDescriptor MACPASCAL open_read(PIDescriptorHandle h,DescriptorKeyIDArray keys){
    const auto value=string(h);
    const auto newline=value.find('\n');
    auto* reader=new Reader{value.substr(0,newline),newline==std::string::npos?"":value.substr(newline+1)};
    for(;keys && *keys;++keys)reader->required.insert(*keys);
    return reinterpret_cast<PIReadDescriptor>(reader);
}
OSErr MACPASCAL close_read(PIReadDescriptor t){
    auto* reader=reinterpret_cast<Reader*>(t);
    const OSErr result=reader->required.empty()?noErr:errMissingParameter;
    delete reader;
    return result;
}
Boolean MACPASCAL get_key(PIReadDescriptor t,DescriptorKeyID* key,DescriptorTypeID* type,int32* flags){
    auto& r=*reinterpret_cast<Reader*>(t);
    if(r.key==2 || (r.key==1 && r.fill_path.empty()))return false;
    *key=r.key++==0?'d8st':'d8fm';*type='TEXT';*flags=0;
    r.required.erase(*key);
    return true;
}
OSErr MACPASCAL get_text(PIReadDescriptor t,Handle* h){const auto& r=*reinterpret_cast<Reader*>(t);*h=make(r.key==1?r.text:r.fill_path);return 0;}
PIWriteDescriptor MACPASCAL open_write(){return reinterpret_cast<PIWriteDescriptor>(new Writer{});}
OSErr MACPASCAL put_text(PIWriteDescriptor t,DescriptorKeyID key,Handle h){expect(key=='d8st',"Wrong descriptor key");reinterpret_cast<Writer*>(t)->text=string(h);return 0;}
OSErr MACPASCAL close_write(PIWriteDescriptor t,PIDescriptorHandle* h){auto* w=reinterpret_cast<Writer*>(t);*h=make(w->text);delete w;return 0;}
struct Host;
Host* active=nullptr;
OSErr MACPASCAL advance();
Boolean MACPASCAL abort_host();
struct Host {
    FilterRecord f{};BigDocumentStruct big{};HandleProcs handles{};ReadDescriptorProcs reads{};WriteDescriptorProcs writes{};PIDescriptorParameters descriptors{};Str255 error{};
    digital8::Image input,output;std::vector<uint8_t> buffer;VRect pending{};int plane=-1;bool negative=false,cancel=false;int calls=0;
    Host(const digital8::Image& image,bool reverse=false):input(image),output(image),negative(reverse){
        handles.handleProcsVersion=1;handles.newProc=allocate;handles.disposeProc=dispose;handles.getSizeProc=size;handles.lockProc=lock;handles.unlockProc=unlock;
        reads.openReadDescriptorProc=open_read;reads.closeReadDescriptorProc=close_read;reads.getKeyProc=get_key;reads.getTextProc=get_text;
        writes.openWriteDescriptorProc=open_write;writes.closeWriteDescriptorProc=close_write;writes.putTextProc=put_text;
        descriptors.readDescriptorProcs=&reads;descriptors.writeDescriptorProcs=&writes;descriptors.playInfo=plugInDialogSilent;
        f.handleProcs=&handles;f.descriptorParameters=&descriptors;f.errorString=&error;f.bigDocumentData=&big;f.depth=8;f.imageMode=plugInModeRGBColor;f.planes=4;f.inLayerPlanes=f.outLayerPlanes=3;f.inTransparencyMask=f.outTransparencyMask=1;f.advanceState=advance;f.abortProc=abort_host;
        big.filterRect32.top=11;big.filterRect32.left=7;big.filterRect32.bottom=11+image.height;big.filterRect32.right=7+image.width;big.imageSize32.v=image.height+11;big.imageSize32.h=image.width+7;
    }
    ~Host(){if(f.parameters)dispose(f.parameters);if(descriptors.descriptor)dispose(descriptors.descriptor);}
    void flush(){if(plane<0)return;int w=pending.right-pending.left,h=pending.bottom-pending.top;size_t pitch=size_t(w*2+13);
        for(int y=0;y<h;y++)for(int x=0;x<w;x++){size_t index=(size_t(pending.top-11+y)*input.width+pending.left-7+x)*4+plane;output.rgba[index]=buffer[size_t(negative?h-1-y:y)*pitch+x*2];}plane=-1;}
};
Boolean MACPASCAL abort_host(){return active->cancel;}
OSErr MACPASCAL advance(){auto& host=*active;host.flush();host.calls++;auto& f=host.f;VRect r=host.big.inRect32;bool output=false;
    if(r.bottom==r.top){r=host.big.outRect32;output=true;}if(r.bottom==r.top)return 0;
    expect(r.left>=7&&r.top>=11&&r.right<=7+host.input.width&&r.bottom<=11+host.input.height,"Filter request escaped ROI");
    int w=r.right-r.left,h=r.bottom-r.top,pitch=w*2+13;host.buffer.assign(size_t(pitch)*h,0xA5);
    if(output){expect(f.outLoPlane==f.outHiPlane,"Expected planar output");host.pending=r;host.plane=f.outLoPlane;f.outData=host.buffer.data()+(host.negative?size_t(h-1)*pitch:0);f.outColumnBytes=2;f.outRowBytes=host.negative?-pitch:pitch;}
    else{expect(f.inLoPlane==f.inHiPlane,"Expected planar input");int c=f.inLoPlane;
        for(int y=0;y<h;y++)for(int x=0;x<w;x++)host.buffer[size_t(host.negative?h-1-y:y)*pitch+x*2]=host.input.rgba[(size_t(r.top-11+y)*host.input.width+r.left-7+x)*4+c];
        f.inData=host.buffer.data()+(host.negative?size_t(h-1)*pitch:0);f.inColumnBytes=2;f.inRowBytes=host.negative?-pitch:pitch;}
    return 0;
}
void call(Main main,Host& h,int selector,intptr_t& data,int16& result){active=&h;main(int16(selector),&h.f,&data,&result);}
digital8::Image image(){digital8::Image out{31,137,std::vector<uint8_t>(31*137*4)};for(int y=0;y<out.height;y++)for(int x=0;x<out.width;x++){size_t p=(size_t(y)*out.width+x)*4;out.rgba[p]=uint8_t(90+x*4);out.rgba[p+1]=uint8_t(80+y);out.rgba[p+2]=uint8_t(100+x*3);out.rgba[p+3]=x%5?uint8_t(40+y):0;}return out;}
int check_fill_map(Main main,Render render,const std::filesystem::path& folder){
    const auto path=folder/L"digital8-fill-123abcdef.tmp";
    struct Cleanup {
        std::filesystem::path path;
        ~Cleanup(){std::error_code ignored;std::filesystem::remove(path,ignored);}
    };
    expect(!std::filesystem::exists(path),"The mock-host fixture already exists");
    Cleanup cleanup{path};
    std::string url;
    constexpr char hex[]="0123456789ABCDEF";
    for(unsigned char value:path.u8string()){
        url+='%';url+=hex[value>>4];url+=hex[value&15];
    }
    auto input=image();
    auto settings=digital8::defaults(0);
    settings.tall_dots=1;settings.pixel_size=2;settings.growth=.5f;settings.trace=1;
    const auto snapshot=digital8::encode(settings);
    for(const auto& marker:{std::string("D8RQ:123abcdef"),std::string("unrelated data")}){
        std::ofstream(path,std::ios::binary)<<marker;
        Host host(input);
        host.descriptors.descriptor=make(snapshot+"\n"+url);
        intptr_t data=0;int16 result=0;
        call(main,host,filterSelectorStart,data,result);
        std::ifstream stream(path,std::ios::binary);
        std::string bytes{std::istreambuf_iterator<char>(stream),{}};
        if(marker=="unrelated data"){
            expect(result==errReportString && bytes==marker && host.output.rgba==input.rgba,
                   "An invalid fill map request changed the file or pixels");
            continue;
        }
        expect(result==0 && bytes.size()>=24 && bytes.compare(0,4,"D8F1")==0,"No fill map was written");
        int32_t header[5];
        std::memcpy(header,bytes.data()+4,sizeof(header));
        digital8::FillMap fills;
        const auto same=digital8::render(input,settings,{},&fills);
        auto plain=input;
        expect(render(snapshot.data(),unsigned(snapshot.size()),input.rgba.data(),input.width,input.height,
                      plain.rgba.data(),plain.rgba.size())==0,"Render failed");
        expect(same.rgba==plain.rgba && host.output.rgba==plain.rgba,"Request changed rendered RGBA");
        expect(header[0]==7 && header[1]==11 && header[2]==input.width && header[3]==input.height &&
               header[4]==int32_t(fills.styles.size()),"Wrong fill map ROI or style count");
        const auto style_bytes=fills.styles.size()*sizeof(digital8::FillStyle);
        expect(bytes.size()==24+style_bytes+fills.ids.size()*2 &&
               std::memcmp(bytes.data()+24,fills.styles.data(),style_bytes)==0 &&
               std::memcmp(bytes.data()+24+style_bytes,fills.ids.data(),fills.ids.size()*2)==0,
               "Fill map differs from the renderer ownership");
        call(main,host,filterSelectorFinish,data,result);
        expect(result==0 && string(host.descriptors.descriptor)==snapshot,"Temporary path leaked into snapshot");
    }
    auto checker=digital8::defaults(0);
    checker.threshold=0;checker.edge=0;checker.tall_dots=0;
    for(int i=0;i<checker.colour_count;++i)checker.colours[i]=i%2?
        digital8::RGB{0,0,255}:digital8::RGB{255,255,0};
    digital8::Image grey{17,13,std::vector<uint8_t>(17*13*4,128)};
    digital8::FillMap fills;
    digital8::render(grey,checker,{},&fills);
    const auto& style=fills.styles.at(fills.ids.at(0));
    expect(style.mixed && style.colour.r==128 && style.colour.g==128 && style.colour.b==128,
           "Yellow/blue checker did not produce #808080");
    return 3;
}
}
int main(int argc,char** argv){try{
    if(argc!=2)throw std::runtime_error("Pass the absolute Digital8.8bf path");HMODULE dll=LoadLibraryA(argv[1]);expect(dll!=nullptr,"Cannot load Digital8.8bf");auto main=reinterpret_cast<Main>(GetProcAddress(dll,"PluginMain"));expect(main!=nullptr,"PluginMain missing");
    auto render=reinterpret_cast<Render>(GetProcAddress(dll,"D8RenderRGBA"));expect(render!=nullptr,"Native render entry missing");
    auto input=image();int tests=0;
    for(int machine=0;machine<3;machine++)for(bool negative:{false,true}){
        auto s=digital8::defaults(machine);s.tall_dots=0;s.growth=.5f;s.trace=1;
        Host h(input,negative);h.f.parameters=make(digital8::encode(s));intptr_t data=0;int16 result=0;
        call(main,h,filterSelectorPrepare,data,result);expect(result==0&&data,"Prepare failed");
        call(main,h,filterSelectorStart,data,result);expect(result==0,"Start failed");
        auto expected=input;auto snapshot=digital8::encode(s);expect(render(snapshot.data(),unsigned(snapshot.size()),input.rgba.data(),input.width,input.height,expected.rgba.data(),expected.rgba.size())==0,"DLL renderer failed");
        if(h.output.rgba!=expected.rgba){for(size_t i=0;i<expected.rgba.size();i++)if(h.output.rgba[i]!=expected.rgba[i]){std::cerr<<"machine="<<machine<<" negative="<<negative<<" index="<<i<<" actual="<<int(h.output.rgba[i])<<" expected="<<int(expected.rgba[i])<<" input="<<int(input.rgba[i])<<'\n';break;}throw std::runtime_error("Host pixel/alpha/stride mismatch");}
        expect(h.f.autoMask,"Host selection autoMask disabled");expect(h.calls>8,"Tiled input/output was not exercised");
        call(main,h,filterSelectorFinish,data,result);expect(result==0&&data==0,"Finish failed");expect(h.descriptors.descriptor!=nullptr,"No action descriptor was saved");expect(digital8::encode(digital8::decode(string(h.descriptors.descriptor)))==digital8::encode(s),"Descriptor snapshot changed");
        auto changed=input;for(size_t p=0;p<changed.rgba.size();p+=4)changed.rgba[p]=uint8_t(changed.rgba[p]/2);
        Host replay(changed,negative);replay.descriptors.descriptor=make(string(h.descriptors.descriptor));
        call(main,replay,filterSelectorPrepare,data,result);call(main,replay,filterSelectorStart,data,result);expect(result==0,"Silent descriptor replay failed");auto expected_replay=changed;expect(render(snapshot.data(),unsigned(snapshot.size()),changed.rgba.data(),changed.width,changed.height,expected_replay.rgba.data(),expected_replay.rgba.size())==0,"DLL replay renderer failed");expect(replay.output.rgba==expected_replay.rgba,"Reevaluation did not use new input/saved settings");call(main,replay,filterSelectorFinish,data,result);expect(result==0,"Replay finish failed");tests+=2;
    }
    {Host h(input);auto settings=digital8::encode(digital8::defaults());h.f.parameters=make(settings);h.cancel=true;intptr_t data=0;int16 result=0;call(main,h,filterSelectorStart,data,result);expect(result==-128&&data==0,"Cancellation was not returned");expect(h.output.rgba==input.rgba&&string(h.f.parameters)==settings,"Cancellation changed image/settings");tests++;}
    {Host h(input);h.f.parameters=make(digital8::encode(digital8::defaults()));h.f.depth=16;intptr_t data=0;int16 result=0;call(main,h,filterSelectorStart,data,result);expect(result==filterBadMode&&data==0,"16-bit data was not rejected");expect(h.output.rgba==input.rgba,"Unsupported mode changed pixels");tests++;}
    {Host h(input);h.f.parameters=make("broken");intptr_t data=0;int16 result=0;call(main,h,filterSelectorStart,data,result);expect(result==errReportString&&data==0,"Corrupt settings were not rejected");expect(h.output.rgba==input.rgba,"Corrupt settings changed pixels");tests++;}
    tests+=check_fill_map(main,render,std::filesystem::absolute(argv[1]).parent_path());
    expect(allocations.empty(),"Host handles leaked");FreeLibrary(dll);std::cout<<tests<<" host integration checks passed (strides, offset ROI, alpha, snapshots, replay, cancellation, invalid inputs, fill map).\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
