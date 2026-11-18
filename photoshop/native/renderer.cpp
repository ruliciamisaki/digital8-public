#include "renderer.h"
#include "digital8_core.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>
#include <map>
#include <numeric>
#include <stdexcept>
#include <unordered_map>

namespace digital8 {
namespace {
using Colour = std::array<float, 3>;
using Feature = std::array<float, 4>;
struct Recipe { int w, h; std::vector<uint16_t> tile; Colour target{}; double penalty = 0; };
float luma(Colour c) { return c[0]*.2126f+c[1]*.7152f+c[2]*.0722f; }
Colour colour(RGB c) { return {float(c.r),float(c.g),float(c.b)}; }
Feature feature(Colour c, bool weighted = true) {
    for (auto& v:c) { v/=255; v=v<=.04045f?v/12.92f:std::pow((v+.055f)/1.055f,2.4f); }
    float l=std::cbrt(.4122214708f*c[0]+.5363325363f*c[1]+.0514459929f*c[2]);
    float m=std::cbrt(.2119034982f*c[0]+.6806995451f*c[1]+.1073969566f*c[2]);
    float s=std::cbrt(.0883024619f*c[0]+.2817188376f*c[1]+.6299787005f*c[2]);
    float a=1.9779984951f*l-2.4285922050f*m+.4505937099f*s;
    float b=.0259040371f*l+.7827717662f*m-.8086757660f*s;
    float L=.2104542553f*l+.7936177850f*m-.0040720468f*s;
    return weighted?Feature{L,2*a,2*b,3*std::hypot(a,b)}:Feature{L,a,b,std::hypot(a,b)};
}
double cost(Feature a,Feature b) {
    float total=0; for(int c=0;c<4;c++){float d=a[c]-b[c];total+=d*d;}
    return double(total)+((a[3]>=.06f&&b[3]>=.06f&&a[1]*b[1]+a[2]*b[2]<0)?256:0);
}
void tick(const Hooks& h,int progress=-1) {
    if(h.cancelled&&h.cancelled()) throw std::runtime_error("cancelled");
    if(progress>=0&&h.progress)h.progress(progress);
}
uint8_t byte(float v) { return uint8_t(std::clamp(v,0.f,255.f)); }
int key(Colour c,int step){return (int(byte(c[0]))/step<<16)|(int(byte(c[1]))/step<<8)|(int(byte(c[2]))/step);}
struct Bin { double mass=0; std::array<double,3> sum{}; Colour mean{}; };

double lanczos(double x) {
    x=std::abs(x);if(x<1e-10)return 1;if(x>=3)return 0;
    constexpr double pi=3.14159265358979323846;
    return std::sin(pi*x)*std::sin(pi*x/3)/(pi*pi*x*x/3);
}
// Premultiplied-alpha separable Lanczos, with clipping only at output.
Image resize(const Image& in,int width,int height) {
    if(in.width==width&&in.height==height)return in;
    struct Tap { int index; double weight; };
    auto taps=[](int source,int dest){std::vector<std::vector<Tap>> all(dest);double scale=double(source)/dest,filter=std::max(1.,scale);
        for(int x=0;x<dest;x++){double center=(x+.5)*scale;int a=std::max(0,int(center-3*filter+.5)),b=std::min(source,int(center+3*filter+.5));double sum=0;
            for(int i=a;i<b;i++){double w=lanczos((i+.5-center)/filter);all[x].push_back({i,w});sum+=w;}
            for(auto& t:all[x])t.weight/=sum;}return all;};
    auto xt=taps(in.width,width),yt=taps(in.height,height);
    std::vector<std::array<double,4>> temp(size_t(width)*in.height);
    for(int y=0;y<in.height;y++)for(int x=0;x<width;x++)for(auto t:xt[x]){
        size_t p=(size_t(y)*in.width+t.index)*4;auto& v=temp[size_t(y)*width+x];double a=in.rgba[p+3]/255.;
        for(int c=0;c<3;c++)v[c]+=in.rgba[p+c]*a*t.weight;v[3]+=in.rgba[p+3]*t.weight;}
    Image out{width,height,std::vector<uint8_t>(size_t(width)*height*4)};
    for(int y=0;y<height;y++)for(int x=0;x<width;x++){
        std::array<double,4> v{};for(auto t:yt[y])for(int c=0;c<4;c++)v[c]+=temp[size_t(t.index)*width+x][c]*t.weight;
        size_t p=(size_t(y)*width+x)*4;out.rgba[p+3]=byte(float(std::round(v[3])));
        for(int c=0;c<3;c++)out.rgba[p+c]=v[3]>1e-6?byte(float(std::round(v[c]*255/v[3]))):0;}
    return out;
}
std::vector<uint8_t> morphology(const std::vector<uint8_t>& input,int w,int h,int radius,bool dilate) {
    // A rectangular any/all test separates into two sliding windows. Repeating
    // edge pixels does not change either test, so the windows may clip there.
    const uint8_t match=dilate?1:0, miss=1-match;
    std::vector<uint8_t> horizontal(input.size()),output(input.size());
    for(int y=0;y<h;y++){
        const int row=y*w;
        int count=0;
        for(int x=0;x<std::min(w,radius);x++)count+=input[row+x]==match;
        for(int x=0;x<w;x++){
            if(x+radius<w)count+=input[row+x+radius]==match;
            horizontal[row+x]=count?match:miss;
            if(x>=radius)count-=input[row+x-radius]==match;
        }
    }
    std::vector<int> counts(w);
    for(int y=0;y<std::min(h,radius);y++)
        for(int x=0;x<w;x++)counts[x]+=horizontal[y*w+x]==match;
    for(int y=0;y<h;y++){
        if(y+radius<h)
            for(int x=0;x<w;x++)counts[x]+=horizontal[(y+radius)*w+x]==match;
        for(int x=0;x<w;x++)output[y*w+x]=counts[x]?match:miss;
        if(y>=radius)
            for(int x=0;x<w;x++)counts[x]-=horizontal[(y-radius)*w+x]==match;
    }
    return output;
}
std::vector<Recipe> recipes(const Settings& s) {
    std::vector<Recipe> out;
    auto add=[&](std::vector<std::string> rows,int a,int b,int c,double extra){
        Recipe r{int(rows[0].size()),int(rows.size()),{}, {},extra};std::vector<int> used;
        for(auto row:rows)for(char ch:row){int id=ch=='A'?a:ch=='B'?b:c;r.tile.push_back(uint16_t(id));used.push_back(id);
            auto v=colour(s.colours[id]);for(int k=0;k<3;k++)r.target[k]+=v[k];}
        for(auto& v:r.target)v/=float(r.tile.size());
        double contrast=0;for(int i:used)for(int j:used){double d=0;auto x=colour(s.colours[i]),y=colour(s.colours[j]);for(int k=0;k<3;k++)d+=(x[k]-y[k])*(x[k]-y[k]);contrast=std::max(contrast,d);}
        r.penalty+=contrast*.002;out.push_back(std::move(r));};
    if(s.machine==2){for(int i=0;i<s.colour_count;i++)add({"A"},i,i,i,0);return out;}
    for(int a=0;a<s.colour_count;a++)for(int b=0;b<s.colour_count;b++){
        if(a==b){add({"A"},a,b,b,0);continue;}
        double pair_penalty=s.machine==0&&!observed_pc88(a,b)?240.:0.;
        if(s.machine==0&&a!=0&&a!=7&&b!=0&&b!=7&&(a^b)==7)pair_penalty+=1800;
        if(luma(colour(s.colours[a]))>=luma(colour(s.colours[b]))){
            add({"AB"},a,b,b,pair_penalty+(s.half==1?0:500));add({"AB","BA"},a,b,b,pair_penalty+(s.half==0?0:500));}
        add({"AB","AA"},a,b,b,pair_penalty+(s.quarter==1?0:500));add({"AABA","BAAA"},a,b,b,pair_penalty+(s.quarter==0?0:500));
        add({"AAAB","AAAA"},a,b,b,pair_penalty+(s.eighth==0?0:500));
    }
    // Optional patterns follow the same logical rows as the Python converter.
    for(int a=0;a<s.colour_count;a++)for(int b=0;b<s.colour_count;b++)if(a!=b)
        add({"BAAA","AAAA","AABA","AAAA"},a,b,b,(s.eighth==1?0:500)+(s.machine==0&&!observed_pc88(a,b)?240:0));
    auto order=[&](int a,int b){float x=luma(colour(s.colours[a])),y=luma(colour(s.colours[b]));return x!=y?x>y:a<b;};
    if(s.three112)for(int c=0;c<s.colour_count;c++)for(int a=0;a<s.colour_count;a++)for(int b=a+1;b<s.colour_count;b++)if(a!=c&&b!=c){
        int x=a,y=b;if(!order(x,y))std::swap(x,y);
        auto rows=s.three112==1?std::vector<std::string>{"ACBC"}:s.three112==2?std::vector<std::string>{"ACBC","BCAC"}:std::vector<std::string>{"ACBC","CBCA","BCAC","CACB"};
        add(rows,x,y,c,120);}
    if(s.three111)for(int a=0;a<s.colour_count;a++)for(int b=a+1;b<s.colour_count;b++)for(int c=b+1;c<s.colour_count;c++){
        std::array<int,3> ids{a,b,c};std::sort(ids.begin(),ids.end(),order);
        add(s.three111==1?std::vector<std::string>{"CAB"}:std::vector<std::string>{"CAB","ABC","BCA"},ids[0],ids[1],ids[2],120);}
    return out;
}
}

// Stateless native entry point used by the integration harness. No Photoshop
// callbacks, Python runtime, allocations or C++ objects cross this C boundary.
extern "C" __declspec(dllexport) int D8RenderRGBA(const char* snapshot,unsigned int length,
    const unsigned char* input,int width,int height,unsigned char* output,size_t output_bytes){
    try{if(!snapshot||!input||!output||width<=0||height<=0||uint64_t(width)*height>8000000||output_bytes!=size_t(width)*height*4)return 1;
        auto settings=digital8::decode(std::string(snapshot,length));digital8::Image source{width,height,std::vector<uint8_t>(input,input+output_bytes)};
        auto result=digital8::render(source,settings);std::memcpy(output,result.rgba.data(),output_bytes);return 0;
    }catch(...){return 1;}
}

Settings defaults(int machine) {
    Settings s{};s.machine=machine;s.threshold=machine==0?72:58;s.tall_dots=machine==0;
    for(size_t i=0;i<palettes().size();i++)if(palettes()[i].machine==machine){set_palette(s,int(i));break;}
    return s;
}
void set_palette(Settings& s,int index) {
    if(index<0||size_t(index)>=palettes().size()||palettes()[index].machine!=s.machine)throw std::runtime_error("Invalid palette");
    s.palette_index=index;s.colour_count=int(palettes()[index].colours.size());
    std::fill(std::begin(s.colours),std::end(s.colours),RGB{});
    std::copy(palettes()[index].colours.begin(),palettes()[index].colours.end(),s.colours);
}
void validate(const Settings& s) {
    auto range=[](double x,double a,double b){return std::isfinite(x)&&x>=a&&x<=b;};
    if(s.magic!=settings_magic||s.version!=1||s.machine<0||s.machine>2||s.colour_count<1||s.colour_count>max_palette
        ||(s.machine==0&&s.colour_count!=8)||(s.machine==1&&(s.colour_count<14||s.colour_count>16))
        ||!range(s.pixel_size,1,64)||!range(s.threshold,0,120)||!range(s.radius,0,8)||!range(s.min_area,0,4096)
        ||!range(s.max_colours,0,256)||!range(s.groups,2,64)||!range(s.alpha_mode,0,1)||!range(s.alpha_threshold,0,255)
        ||!range(s.tall_dots,0,1)||!range(s.trace,0,1)||!range(s.white_transparent,0,1)
        ||!range(s.half,0,1)||!range(s.quarter,0,1)||!range(s.eighth,0,1)||!range(s.three112,0,3)||!range(s.three111,0,2)
        ||!range(s.tolerance,0,128)||!range(s.edge,0,1)||!range(s.growth,0,16)||!range(s.trace_strength,.1,.95)
        ||!range(s.brightness,0,10)||!range(s.saturation,0,10)||!range(s.contrast,0,10)||!range(s.red,0,10)||!range(s.green,0,10)||!range(s.blue,0,10))
        throw std::runtime_error("Unsupported or invalid Digital8 settings");
}
std::string encode(const Settings& s) {
    validate(s);const auto* p=reinterpret_cast<const uint8_t*>(&s);const char* digits="0123456789abcdef";
    std::string out="D8N1:";for(size_t i=0;i<sizeof(s);i++){out+=digits[p[i]>>4];out+=digits[p[i]&15];}return out;
}
Settings decode(const std::string& text) {
    if(text.size()!=5+sizeof(Settings)*2||text.substr(0,5)!="D8N1:")throw std::runtime_error("Unsupported Digital8 settings version");
    Settings s{};auto* p=reinterpret_cast<uint8_t*>(&s);auto hex=[](char c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;throw std::runtime_error("Corrupt Digital8 settings");};
    for(size_t i=0;i<sizeof(s);i++)p[i]=uint8_t(hex(text[5+i*2])*16+hex(text[6+i*2]));validate(s);return s;
}

Image render(const Image& input,const Settings& s,const Hooks& hooks,FillMap* fills) {
    validate(s);
    if(input.width<=0||input.height<=0||uint64_t(input.width)*input.height>8000000
        ||input.rgba.size()!=size_t(input.width)*input.height*4)throw std::runtime_error("Digital8 supports up to 8 million RGB pixels in this version");
    tick(hooks,0);
    // Alpha is a separate output channel; colour processing cannot fill gaps.
    auto alpha=[&](uint8_t value){return s.alpha_mode?uint8_t(value>=s.alpha_threshold?255:0):value;};
    Image prepared;
    const Image* source=&input;
    if(s.alpha_mode){
        prepared=input;
        for(size_t i=3;i<prepared.rgba.size();i+=4)prepared.rgba[i]=alpha(prepared.rgba[i]);
        source=&prepared;
    }
    const int logical_h=s.tall_dots?(input.height+1)/2:input.height;
    if(logical_h!=input.height){
        prepared=resize(*source,input.width,logical_h);
        source=&prepared;
    }
    const int grid_width=std::max(1,input.width/s.pixel_size),grid_height=std::max(1,logical_h/s.pixel_size);
    if(grid_width!=source->width||grid_height!=source->height){
        prepared=resize(*source,grid_width,grid_height);
        source=&prepared;
    }
    const Image& grid=*source;
    int w=grid.width,h=grid.height,n=w*h;
    std::vector<Colour> pixels(n);std::vector<float> coverage(n);std::vector<uint8_t> ink(n),visible(n),valid(n);
    for(int y=0;y<h;y++)for(int x=0;x<w;x++){
        int p=y*w+x;Colour c{float(grid.rgba[p*4]),float(grid.rgba[p*4+1]),float(grid.rgba[p*4+2])};
        float maximum=*std::max_element(c.begin(),c.end()),L=luma(c);
        ink[p]=maximum<=s.threshold||(L<=s.threshold*.65f&&maximum<=s.threshold*1.5f);
        if(!ink[p]&&s.edge>0&&L<170){
            float neighbours=0;
            for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++){
                int q=(std::clamp(y+dy,0,h-1)*w+std::clamp(x+dx,0,w-1))*4;
                neighbours+=grid.rgba[q]*.2126f+grid.rgba[q+1]*.7152f+grid.rgba[q+2]*.0722f;
            }
            ink[p]=(neighbours-L)/8-L>=72-36*s.edge;
        }
        coverage[p]=grid.rgba[p*4+3]/255.f;visible[p]=coverage[p]>0;
        c[0]*=s.red;c[1]*=s.green;c[2]*=s.blue;L=luma(c);
        for(auto& v:c)v=byte(((L+(v-L)*s.saturation-127.5f)*s.contrast+127.5f)*s.brightness);
        pixels[p]=c;
    }
    auto rs=recipes(s);std::vector<Colour> colours;std::vector<int> recipe_ids;
    std::map<Colour,int> position;
    for(size_t i=0;i<rs.size();i++){
        auto found=position.find(rs[i].target);
        if(found==position.end()){position[rs[i].target]=int(colours.size());colours.push_back(rs[i].target);recipe_ids.push_back(int(i));}
        else if(rs[i].penalty<rs[recipe_ids[found->second]].penalty)recipe_ids[found->second]=int(i);
    }
    int k=int(colours.size()),darkest=0;for(int i=1;i<k;i++)if(luma(colours[i])<luma(colours[darkest]))darkest=i;
    int step=std::clamp(int(std::ceil(s.tolerance/16)),1,8);
    std::unordered_map<int,Bin> bins;bool has_ink=false;
    for(int p=0;p<n;p++){has_ink|=ink[p]&&visible[p];if(visible[p]&&!ink[p]){auto& bin=bins[key(pixels[p],step)];bin.mass+=coverage[p];for(int c=0;c<3;c++)bin.sum[c]+=pixels[p][c]*coverage[p];}}
    // Fast lookups per pixel, with the original RGB order for the floating-point sums.
    std::vector<int> bin_order;
    bin_order.reserve(bins.size());
    for(auto& pair:bins){
        auto& bin=pair.second;
        for(int c=0;c<3;c++)bin.mean[c]=float(bin.sum[c]/bin.mass);
        bin_order.push_back(pair.first);
    }
    std::sort(bin_order.begin(),bin_order.end());
    std::vector<Feature> features;for(auto c:colours)features.push_back(feature(c));
    tick(hooks,10);
    std::vector<int> selected;
    int maximum=s.machine==2?s.max_colours:s.groups;
    if(maximum==0||maximum>=k){selected.resize(k);std::iota(selected.begin(),selected.end(),0);}
    else{
        std::vector<double> histogram(k);
        for(int id:bin_order){const auto& bin=bins.at(id);for(int pass=0;pass<(s.machine==2?2:1);pass++){
            Colour c=bin.mean;if(pass)for(auto& v:c)v*=.55f;auto f=feature(c);int best=0;
            double best_cost=cost(f,features[0]);
            for(int i=1;i<k;i++){
                double candidate=cost(f,features[i]);
                if(candidate<best_cost){best=i;best_cost=candidate;}
            }
            histogram[best]+=bin.mass*(pass?.12:1.);}}
        // Only occupied histogram bins contribute. Store the costs in
        // candidate order so the selection pass reads each row sequentially.
        std::vector<int> occupied;
        std::vector<double> weights;
        for(int i=0;i<k;i++)if(histogram[i]>0){occupied.push_back(i);weights.push_back(histogram[i]);}
        const size_t width=occupied.size();
        std::vector<double> distances(size_t(k)*width);
        for(int j=0;j<k;j++){
            tick(hooks);
            for(size_t i=0;i<width;i++)distances[size_t(j)*width+i]=cost(features[occupied[i]],features[j]);
        }
        int first=darkest;
        if(!has_ink&&!bins.empty()){
            double best=std::numeric_limits<double>::infinity();
            for(int j=0;j<k;j++){
                double score=0;
                for(size_t i=0;i<width;i++)score+=weights[i]*distances[size_t(j)*width+i];
                if(score<best){best=score;first=j;}
            }
        }
        selected.push_back(first);
        std::vector<uint8_t> included(k);
        included[first]=1;
        std::vector<double> current(width);
        if(width>0)std::copy_n(distances.data()+size_t(first)*width,width,current.begin());
        for(int iteration=1;iteration<maximum&&width>0;iteration++){
            tick(hooks);
            double total=0;
            for(size_t i=0;i<width;i++)total+=weights[i]*current[i];
            double best=std::numeric_limits<double>::infinity();
            int next=-1;
            for(int j=0;j<k;j++){
                if(included[j])continue;
                double score=0;
                for(size_t i=0;i<width;i++)score+=weights[i]*std::min(current[i],distances[size_t(j)*width+i]);
                if(score<best){best=score;next=j;}
            }
            if(next<0||total-best<=1e-12)break;
            selected.push_back(next);
            included[next]=1;
            for(size_t i=0;i<width;i++)current[i]=std::min(current[i],distances[size_t(next)*width+i]);
        }
    }
    if(s.tolerance>0&&selected.size()>1&&!bins.empty()){
        std::vector<double> usage(selected.size());
        for(int id:bin_order){
            const auto& bin=bins.at(id);
            const auto f=feature(bin.mean);
            size_t best=0;
            double best_cost=cost(f,features[selected[0]]);
            for(size_t i=1;i<selected.size();i++){
                const double candidate=cost(f,features[selected[i]]);
                if(candidate<best_cost){best=i;best_cost=candidate;}
            }
            usage[best]+=bin.mass;
        }
        std::vector<int> order(selected.size());std::iota(order.begin(),order.end(),0);std::stable_sort(order.begin(),order.end(),[&](int a,int b){return usage[a]>usage[b];});
        if(has_ink){auto it=std::find(selected.begin(),selected.end(),darkest);if(it!=selected.end()){int pos=int(it-selected.begin());order.erase(std::find(order.begin(),order.end(),pos));order.insert(order.begin(),pos);}}
        std::vector<int> kept;std::vector<bool> covered(selected.size());for(int pos:order){if(covered[pos])continue;kept.push_back(selected[pos]);for(size_t j=0;j<selected.size();j++){float d=0;for(int c=0;c<3;c++)d=std::max(d,std::abs(colours[selected[j]][c]-colours[selected[pos]][c]));if(d<=s.tolerance/8)covered[j]=true;}}selected=kept;
    }
    if(int(s.growth)>0)ink=morphology(ink,w,h,int(s.growth),true);
    for(int p=0;p<n;p++){ink[p]&=visible[p];valid[p]=visible[p]&&!ink[p];}
    tick(hooks,30);
    // Diffusion uses the original image as its edge guide on every pass.
    using WeightedColour=std::array<float,4>; // RGB sums followed by accumulated alpha.
    std::vector<WeightedColour> total(n);
    for(int p=0;p<n;p++){
        const float mass=valid[p]?coverage[p]:0;
        total[p]={pixels[p][0]*mass,pixels[p][1]*mass,pixels[p][2]*mass,mass};
    }
    if(s.tolerance>0&&s.radius>0){
        // The guide contains integer channel values. Reuse the edge weight for
        // equal channel differences rather than calling exp for each edge.
        std::array<float,256> weights{};
        for(int d=0;d<=s.tolerance;d++){
            const float ratio=d/std::max(s.tolerance,1.f);
            weights[d]=std::exp(-2*ratio*ratio);
        }
        std::vector<float> down(n),right(n);
        auto edge_weight=[&](int p,int q){
            if(!valid[q])return 0.f;
            float difference=0;
            for(int c=0;c<3;c++)difference=std::max(difference,std::abs(pixels[p][c]-pixels[q][c]));
            return weights[int(difference)];
        };
        for(int y=0;y<h;y++)for(int x=0;x<w;x++){
            const int p=y*w+x;
            if(!valid[p])continue;
            if(y+1<h)down[p]=edge_weight(p,p+w);
            if(x+1<w)right[p]=edge_weight(p,p+1);
        }
        std::vector<WeightedColour> next(n);
        for(int iteration=0;iteration<s.radius*2;iteration++){
            tick(hooks);
            for(int y=0;y<h;y++){
                const int row=y*w, above=y>0?row-w:row, below=y+1<h?row+w:row;
                for(int x=0;x<w;x++){
                    const int p=row+x, left=x>0?p-1:p, after=x+1<w?p+1:p;
                    const float up_weight=y>0?down[above+x]:0;
                    const float left_weight=x>0?right[left]:0;
                    // Gather in the same order as the old vertical then
                    // horizontal scatter, with a single write to the new grid.
                    for(int c=0;c<4;c++){
                        float value=total[p][c];
                        value+=total[above+x][c]*up_weight;
                        value+=total[below+x][c]*down[p];
                        value+=total[left][c]*left_weight;
                        value+=total[after][c]*right[p];
                        next[p][c]=value/5;
                    }
                }
            }
            total.swap(next);
        }
    }
    std::unordered_map<int,uint16_t> mapped;
    mapped.reserve(bins.size());
    std::vector<uint16_t> indices(n,uint16_t(darkest));
    for(int p=0;p<n;p++)if(valid[p]){
        if((p&8191)==0)tick(hooks);Colour mean=pixels[p];if(total[p][3]>0)for(int c=0;c<3;c++)mean[c]=total[p][c]/total[p][3];int id=key(mean,step);
        auto it=mapped.find(id);if(it==mapped.end()){
            auto bin=bins.find(id);if(bin!=bins.end())mean=bin->second.mean;else mean={float(std::min(255.,((id>>16)&255)*step+(step-1)/2.)),float(std::min(255.,((id>>8)&255)*step+(step-1)/2.)),float(std::min(255.,(id&255)*step+(step-1)/2.))};
            auto f=feature(mean);int best=selected[0];
            double best_cost=cost(f,features[best]);
            for(size_t i=1;i<selected.size();i++){
                const double candidate=cost(f,features[selected[i]]);
                if(candidate<best_cost){best=selected[i];best_cost=candidate;}
            }
            it=mapped.emplace(id,uint16_t(best)).first;
        }indices[p]=it->second;
    }
    tick(hooks,60);
    if(s.min_area>1){
        std::vector<uint8_t> compatible(size_t(k)*k,1);for(int a:selected)for(int b:selected)compatible[size_t(a)*k+b]=cost(features[a],features[b])<256;
        std::vector<double> scratch((d8_cleanup_scratch_bytes(n)+7)/8);std::vector<uint16_t> cleaned(n);
        auto status=d8_clean_colour_cells(indices.data(),pixels[0].data(),valid.data(),w,h,s.tolerance,s.min_area,compatible.data(),k,scratch.data(),scratch.size()*8,cleaned.data());
        if(status)throw std::runtime_error("Native cleanup failed");indices=std::move(cleaned);
    }
    // Render tile pixels now; trace and fractional ink use virtual fill colours.
    std::vector<uint16_t> output_ids(n);for(int y=0;y<h;y++)for(int x=0;x<w;x++){
        int p=y*w+x;auto& r=rs[recipe_ids[indices[p]]];output_ids[p]=r.tile[(y%r.h)*r.w+(x%r.w)];}
    int black=0;for(int i=1;i<s.colour_count;i++)if(luma(colour(s.colours[i]))<luma(colour(s.colours[black])))black=i;
    for(int p=0;p<n;p++)if(ink[p])output_ids[p]=uint16_t(black);
    std::vector<int> permitted;if(s.machine==2)permitted=selected;else{permitted.resize(s.colour_count);std::iota(permitted.begin(),permitted.end(),0);}
    std::vector<Feature> labs;for(int i=0;i<s.colour_count;i++)labs.push_back(feature(colour(s.colours[i]),false));
    auto plain_distance=[](Feature a,Feature b){float d=0;for(int c=0;c<3;c++)d+=(a[c]-b[c])*(a[c]-b[c]);return d;};
    if(s.trace){
        int max_width=3+2*int(s.growth),radius=(max_width+1)/2;
        auto broad=morphology(morphology(ink,w,h,radius,false),w,h,radius,true);std::vector<uint8_t> eligible(n),known(n),traversable(n);
        const int rays[4][2]={{1,0},{0,1},{1,1},{1,-1}};
        for(int y=0;y<h;y++)for(int x=0;x<w;x++){int p=y*w+x;if(!ink[p]||!visible[p]||broad[p])continue;
            for(auto& ray:rays){int sum=0;for(int sign:{-1,1}){int distance=100;for(int d=1;d<=std::min(8,max_width+1);d++){int yy=y+ray[0]*sign*d,xx=x+ray[1]*sign*d;if(xx<0||xx>=w||yy<0||yy>=h||!visible[yy*w+xx])break;if(!ink[yy*w+xx]){distance=d;break;}}sum+=distance;}if(sum<=max_width+1)eligible[p]=1;}}
        std::vector<int> mapping(k,-1),nearest(indices.begin(),indices.end());
        for(int ref=0;ref<k;ref++){
            auto src=feature(colours[ref],false);Colour desired=colours[ref];for(auto& v:desired)v*=s.trace_strength;auto target=feature(desired,false);float best=std::numeric_limits<float>::infinity();
            for(int i:permitted){auto candidate=colour(s.colours[i]);if(luma(candidate)>=std::min(luma(colours[ref])-1,luma(colours[ref])*.92f))continue;
                if(src[3]>.025f){float cosine=(labs[i][1]*src[1]+labs[i][2]*src[2])/std::max(labs[i][3]*src[3],1e-8f);if(labs[i][3]<=.012f||cosine<.8f)continue;}else if(labs[i][3]>.035f)continue;
                float score=plain_distance(labs[i],target);if(score<best){best=score;mapping[ref]=i;}}
        }
        for(int p=0;p<n;p++){known[p]=valid[p];traversable[p]=visible[p]&&(!ink[p]||eligible[p]);}
        const int dirs[4][2]={{-1,0},{1,0},{0,-1},{0,1}};
        for(int iteration=0;iteration<8;iteration++){tick(hooks);auto next=nearest;auto next_known=known;
            for(int y=0;y<h;y++)for(int x=0;x<w;x++){int p=y*w+x;if(known[p]||!traversable[p])continue;float best=std::numeric_limits<float>::infinity();int id=-1;
                for(auto& d:dirs){int yy=y+d[0],xx=x+d[1];if(yy<0||yy>=h||xx<0||xx>=w)continue;int q=yy*w+xx;if(known[q]&&luma(colours[nearest[q]])<best){id=nearest[q];best=luma(colours[id]);}}
                if(id>=0){next[p]=id;next_known[p]=1;if(eligible[p]&&mapping[id]>=0)output_ids[p]=uint16_t(mapping[id]);}}
            nearest=std::move(next);known=std::move(next_known);
        }
    }
    float fraction=s.growth-std::floor(s.growth);
    if(fraction>1e-8){auto original=output_ids;for(int y=0;y<h;y++)for(int x=0;x<w;x++){
        int p=y*w+x;if(ink[p]||!visible[p])continue;int dark=-1;bool cardinal=false;float least=std::numeric_limits<float>::infinity();
        for(int dy=-1;dy<=1;dy++)for(int dx=-1;dx<=1;dx++){if(!dy&&!dx)continue;int yy=y+dy,xx=x+dx;if(yy<0||yy>=h||xx<0||xx>=w)continue;int q=yy*w+xx;if(!ink[q])continue;cardinal|=!dy||!dx;float L=luma(colour(s.colours[original[q]]));if(L<least){dark=original[q];least=L;}}
        if(dark<0)continue;float a=cardinal?fraction:fraction*fraction;auto c=colour(s.colours[original[p]]),d=colour(s.colours[dark]);for(int i=0;i<3;i++)c[i]=c[i]*(1-a)+d[i]*a;auto f=feature(c,false);float best=std::numeric_limits<float>::infinity();
        for(int id:permitted){float cosine=(f[1]*labs[id][1]+f[2]*labs[id][2])/std::max(f[3]*labs[id][3],1e-8f);bool hue=f[3]<=.025f?labs[id][3]<=.035f:cosine>=.8f||labs[id][3]<.02f;
            if(!hue||luma(colour(s.colours[id]))>luma(colour(s.colours[original[p]]))+1)continue;float score=plain_distance(f,labs[id]);if(score<best){best=score;output_ids[p]=uint16_t(id);}}
    }
    if(fills)for(int p=0;p<n;++p)if(output_ids[p]!=original[p])ink[p]=2;
    }
    tick(hooks,90);
    Image out{input.width,input.height,std::vector<uint8_t>(input.rgba.size())};
    if(fills){
        fills->styles.clear();
        fills->styles.reserve(recipe_ids.size());
        for(int id:recipe_ids){
            const auto& recipe=rs[id];
            const auto first=s.colours[recipe.tile.front()];
            const bool mixed=std::any_of(recipe.tile.begin(),recipe.tile.end(),[&](uint16_t index){
                const auto dot=s.colours[index];
                return dot.r!=first.r || dot.g!=first.g || dot.b!=first.b;
            });
            const auto& c=recipe.target;
            fills->styles.push_back({{byte(c[0]+.5f),byte(c[1]+.5f),byte(c[2]+.5f)},uint8_t(mixed)});
        }
        fills->ids.resize(size_t(input.width)*input.height);
    }
    for(int y=0;y<input.height;y++)for(int x=0;x<input.width;x++){
        int ly=s.tall_dots?y/2:y,gx=std::min(w-1,int((x+.5)*w/input.width)),gy=std::min(h-1,int((ly+.5)*h/logical_h));
        const int g=gy*w+gx;
        auto c=s.colours[output_ids[g]];size_t p=size_t(y)*input.width+x;out.rgba[p*4]=c.r;out.rgba[p*4+1]=c.g;out.rgba[p*4+2]=c.b;
        out.rgba[p*4+3]=(s.white_transparent&&c.r==255&&c.g==255&&c.b==255)?0:alpha(input.rgba[p*4+3]);
        if(fills)fills->ids[p]=ink[g] ? line_fill_id : indices[g];
    }
    tick(hooks,100);return out;
}
}
