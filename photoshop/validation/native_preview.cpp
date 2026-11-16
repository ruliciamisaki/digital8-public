#include "../plugin/ui.h"
#include <filesystem>
#include <fstream>
#include <stdexcept>

int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int){try{
    wchar_t executable[32768]{};GetModuleFileNameW(nullptr,executable,32768);
    std::filesystem::path path=__argc==2?std::filesystem::path(__wargv[1]):std::filesystem::path(executable).parent_path().parent_path()/L"native-comparison/input.rgba";
    std::ifstream file(path,std::ios::binary);uint32_t header[3]{};file.read(reinterpret_cast<char*>(header),sizeof(header));
    if(!file||header[0]!=0x31493844||!header[1]||!header[2]||uint64_t(header[1])*header[2]>8000000)throw std::runtime_error("Invalid input image");
    digital8::Image image{int(header[1]),int(header[2]),std::vector<uint8_t>(size_t(header[1])*header[2]*4)};file.read(reinterpret_cast<char*>(image.rgba.data()),image.rgba.size());if(!file)throw std::runtime_error("Truncated input");
    auto settings=digital8::defaults();if(digital8::configure(settings,image,nullptr)){
        auto output=digital8::render(image,settings);std::ofstream raw(path.parent_path()/L"native_preview_output.rgba",std::ios::binary);raw.write(reinterpret_cast<const char*>(header),sizeof(header));raw.write(reinterpret_cast<const char*>(output.rgba.data()),output.rgba.size());
        std::ofstream saved(path.parent_path()/L"native_preview_settings.txt");saved<<digital8::encode(settings);
    }
    return 0;
}catch(const std::exception& e){MessageBoxA(nullptr,e.what(),"Digital8 Native preview",MB_OK|MB_ICONERROR);return 1;}}
