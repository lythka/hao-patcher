// Specialized AIR -> explicit framebuffer texture inputs. No export-name rules.
#include "lower.h"
#include <algorithm>
#include <map>
#include <regex>
#include <set>
#include <stdexcept>

using S = std::string;
using V = std::vector<S>;
static S trim(S s) {
    auto a=s.find_first_not_of(" \t\r\n"), b=s.find_last_not_of(" \t\r\n");
    return a==S::npos ? S() : s.substr(a,b-a+1);
}
static S join(const V &v, const S &separator=", ") {
    S out; for(const auto &s:v) { if(!out.empty()) out+=separator; out+=s; } return out;
}
static void need(bool ok,const S &why) { if(!ok) throw std::runtime_error(why); }
static bool has(const V &v,const S &s) { return std::find(v.begin(),v.end(),s)!=v.end(); }
static size_t at(const V &v,const S &s) {
    auto i=std::find(v.begin(),v.end(),s); need(i!=v.end(),"Missing AIR metadata "+s); return i-v.begin();
}
static S escape(const S &s) { return std::regex_replace(s,std::regex(R"([.^$|()\[\]{}*+?\\])"),R"(\$&)"); }
static unsigned integer(const S &s) {
    need(std::regex_match(s,std::regex("i32 [0-9]+")),"Specialize resource indices before lowering: "+s);
    return std::stoul(s.substr(4));
}
// Metadata lines start a line; find "\n" + prefix, or the prefix at offset 0. A linear search keeps this fast on
// large lowered modules.
static size_t lineStarting(const S &text,const S &prefix) {
    if(text.compare(0,prefix.size(),prefix)==0) return 0;
    auto i=text.find("\n"+prefix); return i==S::npos ? S::npos : i+1;
}
static unsigned digits(const S &text,size_t &i,const S &why) {
    size_t start=i; while(i<text.size() && text[i]>='0' && text[i]<='9') ++i;
    need(i>start && i-start<10,why); return std::stoul(text.substr(start,i-start));
}
std::pair<unsigned,unsigned> fbAirVersion(const S &text,const S &key) {
    S head="!"+key+" = !{!"; size_t i=lineStarting(text,head); need(i!=S::npos,"Missing AIR version");
    i+=head.size(); S ref=std::to_string(digits(text,i,"Missing AIR version")); need(i<text.size() && text[i]=='}',"Missing AIR version");
    S node="!"+ref+" = !{"; size_t j=lineStarting(text,node); need(j!=S::npos,"Invalid AIR version"); j+=node.size();
    S metal="!\"Metal\", "; if(text.compare(j,metal.size(),metal)==0) j+=metal.size();
    need(text.compare(j,4,"i32 ")==0,"Invalid AIR version"); j+=4; unsigned major=digits(text,j,"Invalid AIR version");
    need(text.compare(j,6,", i32 ")==0,"Invalid AIR version"); j+=6; unsigned minor=digits(text,j,"Invalid AIR version");
    return {major,minor};
}
V fbSplit(const S &text) {
    V out; S stack; bool quote=false,slash=false; size_t start=0;
    const S opens="([{<",closes=")]}>";
    for(size_t i=0;i<text.size();++i) {
        char c=text[i];
        if(quote) { if(slash)slash=false;else if(c=='\\')slash=true;else if(c=='"')quote=false; }
        else if(c=='"')quote=true;
        else if(opens.find(c)!=S::npos)stack+=c;
        else if(closes.find(c)!=S::npos) {
            need(!stack.empty() && opens.find(stack.back())==closes.find(c),"Unbalanced LLVM list");stack.pop_back();
        } else if(c==',' && stack.empty()) { out.push_back(trim(text.substr(start,i-start)));start=i+1; }
    }
    need(!quote && stack.empty(),"Unterminated LLVM list: "+text);
    S tail=trim(text.substr(start));if(!tail.empty())out.push_back(tail);return out;
}
FBLowered fbLower(const S &source,bool ms) {
    FBLowered out{source,{}}; std::map<S,S> nodes; unsigned next=0;
    V lines; size_t start=0;
    while(start<source.size()) { size_t end=source.find('\n',start);if(end==S::npos)end=source.size();lines.push_back(source.substr(start,end-start));start=end+1; }
    std::regex nodeRE(R"(^!(\d+) = (.*)$)"); std::smatch m; S fragments;
    for(const auto &line:lines) {
        if(std::regex_match(line,m,nodeRE)) {
            // Distinct alias/debug nodes also consume the module's ID space.
            next=std::max(next,unsigned(std::stoul(m[1]))+1);
            S value=m[2];if(value.rfind("!{",0)==0)nodes["!"+m[1].str()]=value.substr(2,value.size()-3);
        }
        if(line.rfind("!air.fragment = !{",0)==0)fragments=line.substr(18,line.size()-19);
    }
    if(fragments.empty())return out;
    V additions,wrappers;std::set<S> declarations;
    auto node=[&](const S &body){S ref="!"+std::to_string(next++);additions.push_back(ref+" = !{"+body+"}");return ref;};
    for(const S &ref:fbSplit(fragments)) {
        V parts=fbSplit(nodes.at(ref));need(parts.size()>=3,"Invalid fragment metadata");
        // The function type can contain nested aggregates and quoted type names.
        size_t star=parts[0].rfind(")* @");need(star!=S::npos,"Unsupported AIR fragment function type");
        size_t paren=parts[0].find(" (");need(paren!=S::npos,"Unsupported AIR fragment result");
        S result=parts[0].substr(0,paren),name=parts[0].substr(star+3);
        V types=fbSplit(parts[0].substr(paren+2,star-paren-2));
        std::map<unsigned,V> metadata;bool colors=false;
        for(const auto &r:fbSplit(nodes.at(parts[2]))) {
            V md=fbSplit(nodes.at(r));metadata[integer(md.at(0))]=md;colors|=has(md,"!\"air.render_target\"");
        }
        if(!colors)continue;
        need(metadata.size()==types.size(),"Fragment metadata does not cover each parameter: "+name);
        std::set<unsigned> occupied;
        for(const auto &item:metadata) {
            const V &md=item.second;
            need(!has(md,"!\"air.primitive_id\"") && !has(md,"!\"air.render_target_array_index\""),"Primitive IDs require draw-input lowering; layered inputs require an extended encoder ABI");
            need(!has(md,"!\"air.function_constant\""),"Specialize function constants before lowering "+name);
            if(has(md,"!\"air.texture\"")) {
                size_t j=at(md,"!\"air.location_index\"")+1;unsigned begin=integer(md.at(j)),count=integer(md.at(j+1));
                need(begin<128 && count<=128-begin,"Texture range outside Metal slots");
                for(unsigned i=begin;i<begin+count;++i)occupied.insert(i);
            }
        }
        V newTypes,newMD,callArgs(types.size());S position,sample;
        for(unsigned i=0;i<types.size();++i) {
            const V &md=metadata.at(i);if(has(md,"!\"air.render_target\""))continue;
            unsigned n=newTypes.size();newTypes.push_back(types[i]);V copy=md;copy[0]="i32 "+std::to_string(n);
            newMD.push_back(node(join(copy)));S arg="%fb_arg"+std::to_string(n);callArgs[i]=types[i]+" "+arg;
            if(has(md,"!\"air.position\"")) {need(types[i]=="<4 x float>","Unexpected position type");position=arg;}
            if(has(md,"!\"air.sample_id\"")) {need(types[i]=="i32","Unexpected sample ID type");sample=arg;}
        }
        auto builtin=[&](const S &type,const S &md) {
            unsigned n=newTypes.size();newTypes.push_back(type);newMD.push_back(node("i32 "+std::to_string(n)+", "+md));return "%fb_arg"+std::to_string(n);
        };
        if(position.empty())position=builtin("<4 x float>","!\"air.position\", !\"air.center\", !\"air.no_perspective\", !\"air.arg_type_name\", !\"float4\", !\"air.arg_name\", !\"__fbcompat_fb_position\"");
        if(ms && sample.empty())sample=builtin("i32","!\"air.sample_id\", !\"air.arg_type_name\", !\"uint\", !\"air.arg_name\", !\"__fbcompat_fb_sample\"");
        declarations.insert("declare <2 x i32> @air.convert.u.v2i32.f.v2f32(<2 x float>)");
        V instructions={"  %fb_xy = shufflevector <4 x float> "+position+", <4 x float> undef, <2 x i32> <i32 0, i32 1>",
                        "  %fb_coord = call <2 x i32> @air.convert.u.v2i32.f.v2f32(<2 x float> %fb_xy)"};
        S kind=ms?"_texture_2d_ms_t":"_texture_2d_t",pointer="%struct."+kind+" addrspace(1)*";
        S declaration="%struct."+kind+" = type opaque";
        if(source.find(declaration)==S::npos)declarations.insert(declaration);
        FBPlan plan{name,{},ms};
        for(const auto &item:metadata) {
            unsigned i=item.first;const V &md=item.second;if(!has(md,"!\"air.render_target\""))continue;
            unsigned attachment=integer(md.at(at(md,"!\"air.render_target\"")+1));need(attachment<8,"Color attachment outside Metal range");
            const S &type=types[i];std::smatch shape;
            need(std::regex_match(type,shape,std::regex(R"(<([2-4]) x (half|float|i32)>|(half|float|i32))")),"Unsupported framebuffer input type: "+type);
            unsigned width=shape[1].matched?std::stoul(shape[1]):1;S scalar=shape[2].matched?shape[2].str():shape[3].str();
            S airType=md.at(at(md,"!\"air.arg_type_name\"")+1);airType=airType.substr(2,airType.size()-3);
            bool sign=scalar=="i32" && airType[0]!='u';
            S suffix=scalar=="half"?"f16":scalar=="float"?"f32":sign?"i32":"u32";
            S msl=scalar=="i32"?(sign?"int":"uint"):scalar;
            int slot=127;while(slot>=0 && occupied.count(slot))--slot;need(slot>=0,"No texture slot available for framebuffer input");occupied.insert(slot);
            unsigned n=newTypes.size();newTypes.push_back(pointer);S label="__fbcompat_fb_attachment_"+std::to_string(attachment);
            newMD.push_back(node("i32 "+std::to_string(n)+", !\"air.texture\", !\"air.location_index\", i32 "+std::to_string(slot)+", i32 1, !\"air.read\", !\"air.arg_type_name\", !\""+(ms?"texture2d_ms":"texture2d")+"<"+msl+", read>\", !\"air.arg_name\", !\""+label+"\""));
            S id=std::to_string(i),v4="<4 x "+scalar+">",aggregate="{ "+v4+", i8 }";
            S intrinsic="@air.read_texture_2d"+S(ms?"_ms":"")+".v4"+suffix;
            declarations.insert("declare "+aggregate+" "+intrinsic+"("+pointer+", <2 x i32>, i32, i32)");
            instructions.push_back("  %fb_read"+id+" = call "+aggregate+" "+intrinsic+"("+pointer+" %fb_arg"+std::to_string(n)+", <2 x i32> %fb_coord, i32 "+(ms?sample:"0")+", i32 1)");
            instructions.push_back("  %fb_v4_"+id+" = extractvalue "+aggregate+" %fb_read"+id+", 0");S value="%fb_v4_"+id;
            if(width==1) {instructions.push_back("  %fb_value"+id+" = extractelement "+v4+" "+value+", i32 0");value="%fb_value"+id;}
            else if(width!=4) {
                V mask;for(unsigned k=0;k<width;++k)mask.push_back("i32 "+std::to_string(k));
                instructions.push_back("  %fb_value"+id+" = shufflevector "+v4+" "+value+", "+v4+" undef, <"+std::to_string(width)+" x i32> <"+join(mask)+">");value="%fb_value"+id;
            }
            callArgs[i]=type+" "+value;plan.bindings.push_back({attachment,unsigned(slot),airType,label});
        }
        S inner="@__fbcompat_fb_body_"+std::to_string(out.plans.size());need(source.find(inner)==S::npos,"Framebuffer wrapper name already exists");
        bool found=false;
        std::regex header("^define (.*?)"+escape(name)+R"(\((.*)\)([^\n]*) \{$)");
        for(auto &line:lines)if(std::regex_match(line,m,header)) {
            need(m[1].str()==result+" ","Unsupported fragment linkage/return attributes");
            line="define internal "+m[1].str()+inner+"("+m[2].str()+")"+m[3].str()+" alwaysinline {";found=true;break;
        }
        need(found,"Missing fragment definition "+name);
        instructions.push_back("  %fb_result = call "+result+" "+inner+"("+join(callArgs)+")");instructions.push_back("  ret "+result+" %fb_result");
        V args;for(unsigned i=0;i<newTypes.size();++i)args.push_back(newTypes[i]+" %fb_arg"+std::to_string(i));
        wrappers.push_back("define "+result+" "+name+"("+join(args)+") {\n"+join(instructions,"\n")+"\n}");
        parts[0]=result+" ("+join(newTypes)+")* "+name;parts[2]=node(join(newMD));
        for(auto &line:lines)if(line.rfind(ref+" = !{",0)==0){line=ref+" = !{"+join(parts)+"}";break;}
        out.plans.push_back(plan);
    }
    if(out.plans.empty())return out;
    for(const auto &d:declarations) {
        bool exists=false;
        if(d.rfind("declare ",0)==0) {
            auto a=d.find('@'),b=d.find('(',a);S symbol=d.substr(a,b-a);
            for(const auto &line:lines)if(line.rfind("declare ",0)==0 && line.find(symbol+"(")!=S::npos) {
                need(line.rfind(d.substr(0,a)+symbol+"(",0)==0,"Texture intrinsic return ABI differs: "+symbol);exists=true;break;
            }
        }
        if(!exists)lines.push_back(d);
    }
    lines.insert(lines.end(),wrappers.begin(),wrappers.end());lines.insert(lines.end(),additions.begin(),additions.end());out.text=join(lines,"\n")+"\n";return out;
}

S fbDrawInputs(const S &source,bool vertex) {
    S root=vertex?"!air.vertex = !{":"!air.fragment = !{";
    std::map<S,S> nodes;V lines;unsigned next=0;S entries;
    std::regex nodeRE(R"(^!(\d+) = (.*)$)");std::smatch m;
    size_t begin=0;
    while(begin<source.size()) {
        size_t end=source.find('\n',begin);if(end==S::npos)end=source.size();S line=source.substr(begin,end-begin);begin=end+1;lines.push_back(line);
        if(std::regex_match(line,m,nodeRE)) {
            next=std::max(next,unsigned(std::stoul(m[1]))+1);S value=m[2];
            if(value.rfind("!{",0)==0)nodes["!"+m[1].str()]=value.substr(2,value.size()-3);
        }
        if(line.rfind(root,0)==0)entries=line.substr(root.size(),line.size()-root.size()-1);
    }
    if(entries.empty())return source;
    V appended;unsigned count=0;
    auto node=[&](const S &body){S ref="!"+std::to_string(next++);appended.push_back(ref+" = !{"+body+"}");return ref;};
    for(const auto &ref:fbSplit(entries)) {
        V parts=fbSplit(nodes.at(ref));size_t paren=parts[0].find(" ("),star=parts[0].rfind(")* @");
        need(paren!=S::npos && star!=S::npos,"Unsupported draw input function type");
        S result=parts[0].substr(0,paren),name=parts[0].substr(star+3);V types=fbSplit(parts[0].substr(paren+2,star-paren-2));
        std::map<unsigned,V> metadata;std::map<unsigned,unsigned> fields;std::set<unsigned> occupied;
        for(const auto &r:fbSplit(nodes.at(parts.at(2)))) {
            V md=fbSplit(nodes.at(r));unsigned i=integer(md[0]);metadata[i]=md;
            if(vertex && has(md,"!\"air.base_vertex\""))fields[i]=0;
            if(vertex && has(md,"!\"air.base_instance\""))fields[i]=1;
            if(!vertex && has(md,"!\"air.primitive_id\""))fields[i]=2;
        }
        if(fields.empty())continue;
        for(const auto &item:metadata) {
            const V &md=item.second;
            if(has(md,"!\"air.buffer\"")) {
                size_t j=at(md,"!\"air.location_index\"")+1;unsigned first=integer(md.at(j)),n=integer(md.at(j+1));
                need(first<31 && n<=31-first,"Buffer range outside Metal slots");for(unsigned k=first;k<first+n;++k)occupied.insert(k);
            }
        }
        int slot=30;while(slot>=0 && occupied.count(slot))--slot;need(slot>=0,"No buffer slot available for draw inputs");
        V newTypes,newMD,callArgs(types.size()),instructions;
        for(unsigned i=0;i<types.size();++i) {
            V md=metadata.at(i);
            if(fields.count(i)) {need(types[i]=="i32" || types[i]=="i16","Unsupported draw input type");continue;}
            unsigned n=newTypes.size();newTypes.push_back(types[i]);md[0]="i32 "+std::to_string(n);newMD.push_back(node(join(md)));callArgs[i]=types[i]+" %draw_arg"+std::to_string(n);
        }
        unsigned buffer=newTypes.size();newTypes.push_back("i32 addrspace(2)*");S param="%draw_arg"+std::to_string(buffer);
        newMD.push_back(node("i32 "+std::to_string(buffer)+", !\"air.buffer\", !\"air.buffer_size\", i32 16, !\"air.location_index\", i32 "+std::to_string(slot)+", i32 1, !\"air.read\", !\"air.address_space\", i32 2, !\"air.arg_type_size\", i32 16, !\"air.arg_type_align_size\", i32 16, !\"air.arg_type_name\", !\"uint4\", !\"air.arg_name\", !\"__fbcompat_fb_draw\""));
        for(const auto &field:fields) {
            S i=std::to_string(field.first),value="%draw_value"+i;
            instructions.push_back("  %draw_pointer"+i+" = getelementptr inbounds i32, i32 addrspace(2)* "+param+", i32 "+std::to_string(field.second));
            instructions.push_back("  "+value+" = load i32, i32 addrspace(2)* %draw_pointer"+i+", align 4");
            if(types[field.first]=="i16") {instructions.push_back("  %draw_short"+i+" = trunc i32 "+value+" to i16");value="%draw_short"+i;}
            callArgs[field.first]=types[field.first]+" "+value;
        }
        S inner="@__fbcompat_draw_body_"+std::to_string(count++);need(source.find(inner)==S::npos,"Draw wrapper name already exists");bool found=false;
        std::regex header("^define (.*?)"+escape(name)+R"(\((.*)\)([^\n]*) \{$)");
        for(auto &line:lines)if(std::regex_match(line,m,header)) {
            need(m[1].str()==result+" ","Unsupported draw function linkage");line="define internal "+m[1].str()+inner+"("+m[2].str()+")"+m[3].str()+" alwaysinline {";found=true;break;
        }
        need(found,"Missing draw function definition");
        instructions.push_back("  %draw_result = call "+result+" "+inner+"("+join(callArgs)+")");instructions.push_back("  ret "+result+" %draw_result");
        V args;for(unsigned i=0;i<newTypes.size();++i)args.push_back(newTypes[i]+" %draw_arg"+std::to_string(i));
        appended.push_back("define "+result+" "+name+"("+join(args)+") {\n"+join(instructions,"\n")+"\n}");
        parts[0]=result+" ("+join(newTypes)+")* "+name;parts[2]=node(join(newMD));
        for(auto &line:lines)if(line.rfind(ref+" = !{",0)==0){line=ref+" = !{"+join(parts)+"}";break;}
    }
    if(!count)return source;
    lines.insert(lines.end(),appended.begin(),appended.end());return join(lines,"\n")+"\n";
}
