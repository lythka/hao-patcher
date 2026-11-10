#import <Foundation/Foundation.h>
#include "lower.h"
#include <iostream>
#include <iterator>

int main(int argc,const char **argv) { @autoreleasepool {
    try {
        std::string input((std::istreambuf_iterator<char>(std::cin)),std::istreambuf_iterator<char>());
        if(argc==2 && !strcmp(argv[1],"split")) {
            NSMutableArray *items=[NSMutableArray new];for(const auto &s:fbSplit(input))[items addObject:@(s.c_str())];
            std::cout<<[[[NSString alloc] initWithData:[NSJSONSerialization dataWithJSONObject:items options:0 error:NULL] encoding:NSUTF8StringEncoding] UTF8String];return 0;
        }
        if(argc==3 && !strcmp(argv[1],"version")) {
            auto v=fbAirVersion(input,argv[2]);std::cout<<"["<<v.first<<", "<<v.second<<"]";return 0;
        }
        auto result=fbLower(fbDrawInputs(input,false),argc==2 && !strcmp(argv[1],"multisample"));
        if(result.plans.empty())result.text=input;
        NSMutableArray *plans=[NSMutableArray new];
        for(const auto &p:result.plans) {
            NSMutableArray *bindings=[NSMutableArray new];
            for(const auto &b:p.bindings)[bindings addObject:@{@"attachment":@(b.attachment),@"texture_index":@(b.slot),@"type":@(b.type.c_str()),@"name":@(b.name.c_str())}];
            [plans addObject:@{@"function":@(p.function.c_str()),@"bindings":bindings,@"multisample":@(p.multisample),@"requires_primitive_order":@YES,@"reads_primitive_id":@NO}];
        }
        NSData *json=[NSJSONSerialization dataWithJSONObject:@[@(result.text.c_str()),plans] options:0 error:NULL];
        std::cout<<[[[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding] UTF8String];return 0;
    } catch(const std::exception &e) {std::cerr<<e.what()<<'\n';return 1;}
}}
