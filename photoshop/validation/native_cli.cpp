#include "../native/renderer.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <cstddef>
#include <iomanip>

// Development-only export. Runtime UXP needs only the checked-in JSON and 8bf.
void export_panel(const char* path){
    std::ofstream out(path);if(!out)throw std::runtime_error("Cannot export panel data");
    out<<"{\"size\":"<<sizeof(digital8::Settings)<<",\"paletteOffset\":"<<offsetof(digital8::Settings,colours)<<",\"fields\":{";
    bool first=true;
#define FIELD(name,type) if(!first)out<<',';first=false;out<<"\"" #name "\":{\"offset\":"<<offsetof(digital8::Settings,name)<<",\"type\":\"" type "\"}";
    FIELD(machine,"int") FIELD(palette_index,"int") FIELD(colour_count,"int")
    FIELD(pixel_size,"int") FIELD(tall_dots,"int") FIELD(threshold,"int") FIELD(radius,"int") FIELD(min_area,"int")
    FIELD(max_colours,"int") FIELD(groups,"int") FIELD(alpha_mode,"int") FIELD(alpha_threshold,"int")
    FIELD(half,"int") FIELD(quarter,"int") FIELD(eighth,"int") FIELD(three112,"int") FIELD(three111,"int")
    FIELD(trace,"int") FIELD(white_transparent,"int") FIELD(tolerance,"float") FIELD(edge,"float") FIELD(growth,"float")
    FIELD(trace_strength,"float") FIELD(brightness,"float") FIELD(saturation,"float") FIELD(contrast,"float")
    FIELD(red,"float") FIELD(green,"float") FIELD(blue,"float")
#undef FIELD
    out<<"},\"defaults\":[";
    for(int i=0;i<3;i++){if(i)out<<',';out<<std::quoted(digital8::encode(digital8::defaults(i)));}
    out<<"],\"palettes\":[";first=true;
    for(const auto& p:digital8::palettes()){
        if(!first)out<<',';first=false;out<<"{\"machine\":"<<p.machine<<",\"id\":"<<std::quoted(p.id)<<",\"label\":"<<std::quoted(p.label)<<",\"colours\":[";
        bool cfirst=true;for(auto c:p.colours){if(!cfirst)out<<',';cfirst=false;out<<'['<<int(c.r)<<','<<int(c.g)<<','<<int(c.b)<<']';}out<<"]}";
    }
    out<<"]}\n";
}

int main(int argc,char** argv){try{
    if(argc==3&&std::string(argv[1])=="--export-panel"){export_panel(argv[2]);return 0;}
    if(argc<4)throw std::runtime_error("Usage: native_cli input.rgba output.rgba machine [--field value ...]");
    digital8::Settings s=digital8::defaults(std::stoi(argv[3]));std::string save;
    for(int i=4;i<argc;i+=2){if(i+1>=argc)throw std::runtime_error("Missing argument value");std::string key=argv[i],arg=argv[i+1];
        if(key=="--settings"){std::ifstream in(arg);std::string text((std::istreambuf_iterator<char>(in)),{});s=digital8::decode(text);continue;}
        if(key=="--save-settings"){save=arg;continue;}
        if(key=="--palette"){bool found=false;for(size_t j=0;j<digital8::palettes().size();j++)if(digital8::palettes()[j].machine==s.machine&&arg==digital8::palettes()[j].id){digital8::set_palette(s,int(j));found=true;break;}if(!found)throw std::runtime_error("Unknown palette");continue;}
        float v=std::stof(arg);bool found=false;
        for(auto pair:std::initializer_list<std::pair<const char*,int32_t*>>{{"pixel",&s.pixel_size},{"tall",&s.tall_dots},{"threshold",&s.threshold},{"radius",&s.radius},{"area",&s.min_area},{"colours",&s.max_colours},{"groups",&s.groups},{"alpha",&s.alpha_mode},{"alpha-threshold",&s.alpha_threshold},{"trace",&s.trace},{"white",&s.white_transparent},{"half",&s.half},{"quarter",&s.quarter},{"eighth",&s.eighth},{"three112",&s.three112},{"three111",&s.three111}})
            if(key==std::string("--")+pair.first){*pair.second=int(v);found=true;}
        for(auto pair:std::initializer_list<std::pair<const char*,float*>>{{"tolerance",&s.tolerance},{"edge",&s.edge},{"growth",&s.growth},{"trace-strength",&s.trace_strength},{"brightness",&s.brightness},{"saturation",&s.saturation},{"contrast",&s.contrast},{"red",&s.red},{"green",&s.green},{"blue",&s.blue}})
            if(key==std::string("--")+pair.first){*pair.second=v;found=true;}
        if(!found)throw std::runtime_error("Unknown option: "+key);
    }
    digital8::validate(s);digital8::Image image;std::ifstream input(argv[1],std::ios::binary);uint32_t header[3]{};input.read(reinterpret_cast<char*>(header),sizeof(header));
    if(!input||header[0]!=0x31493844||!header[1]||!header[2]||uint64_t(header[1])*header[2]>8000000)throw std::runtime_error("Invalid RGBA input");
    image.width=int(header[1]);image.height=int(header[2]);image.rgba.resize(size_t(image.width)*image.height*4);input.read(reinterpret_cast<char*>(image.rgba.data()),image.rgba.size());if(!input)throw std::runtime_error("Truncated input");
    auto out=digital8::render(image,s);std::ofstream output(argv[2],std::ios::binary);output.write(reinterpret_cast<const char*>(header),sizeof(header));output.write(reinterpret_cast<const char*>(out.rgba.data()),out.rgba.size());if(!output)throw std::runtime_error("Cannot write output");
    if(!save.empty()){std::ofstream text(save);text<<digital8::encode(s);if(!text)throw std::runtime_error("Cannot save settings");}
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
