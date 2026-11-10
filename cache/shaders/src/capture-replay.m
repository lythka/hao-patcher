// Included by replay.m: reproduce captured API descriptors, including linked functions.
// Use the loaded Metal runtime's classes, never the host SDK's linked Metal framework.
static NSString *captureRoot;
static NSMutableDictionary *captureObjects;
static id captureObject(id value);
typedef struct { uint32_t field0; NSUInteger field8,field16; } ReplayFilterParameter;
typedef struct { bool flag; NSUInteger count; const ReplayFilterParameter *parameters; } ReplayFilterInfo;
static id captureStitchingNode(NSDictionary *graph, NSUInteger index, NSMutableDictionary *nodes, NSMutableSet *active) {
  id cached=nodes[@(index)]; if (cached) return cached;
  if (index>=[graph[@"records"] count] || [active containsObject:@(index)])
    [NSException raise:@"Capture" format:@"invalid stitching graph node %lu",(unsigned long)index];
  [active addObject:@(index)];
  NSDictionary *record=graph[@"records"][index]; id result=nil;
  if ([record[@"node"] isEqual:@"input"]) {
    result=MSG(id(*)(id,SEL,NSUInteger),[NSClassFromString(@"MTLFunctionStitchingInputNode") alloc],
               sel_registerName("initWithArgumentIndex:"),[record[@"argumentIndex"] unsignedIntegerValue]);
  } else if ([record[@"node"] isEqual:@"early-return"]) {
    if (!record[@"condition"]) [NSException raise:@"Capture" format:@"missing stitching early-return condition"];
    id condition=captureStitchingNode(graph,[record[@"condition"] unsignedIntegerValue],nodes,active);
    NSMutableArray *dependencies=[NSMutableArray array];
    for (NSNumber *ref in record[@"controlDependencies"])
      [dependencies addObject:captureStitchingNode(graph,ref.unsignedIntegerValue,nodes,active)];
    result=MSG(id(*)(id,SEL,id,id),[NSClassFromString(@"MTLFunctionStitchingEarlyReturnNode") alloc],
               sel_registerName("initWithCondition:controlDependencies:"),condition,dependencies);
  } else if ([record[@"node"] isEqual:@"function"]) {
    NSMutableArray *arguments=[NSMutableArray array], *dependencies=[NSMutableArray array];
    for (NSNumber *ref in record[@"arguments"])
      [arguments addObject:captureStitchingNode(graph,ref.unsignedIntegerValue,nodes,active)];
    for (NSNumber *ref in record[@"controlDependencies"])
      [dependencies addObject:captureStitchingNode(graph,ref.unsignedIntegerValue,nodes,active)];
    NSString *cls=record[@"nodeClass"]?:@"MTLFunctionStitchingFunctionNode";
    if ([cls isEqual:@"MTLFunctionStitchingFunctionNodeSPI"] && record[@"isEarlyReturn"])
      result=MSG(id(*)(id,SEL,id,id,id,BOOL),[NSClassFromString(cls) alloc],
                 sel_registerName("initWithName:arguments:controlDependencies:isEarlyReturn:"),record[@"name"],arguments,dependencies,[record[@"isEarlyReturn"] boolValue]);
    else if ([cls isEqual:@"MTLFunctionStitchingFunctionNode"])
      result=MSG(id(*)(id,SEL,id,id,id),[NSClassFromString(cls) alloc],
                 sel_registerName("initWithName:arguments:controlDependencies:"),record[@"name"],arguments,dependencies);
    else [NSException raise:@"Capture" format:@"unsupported or incomplete stitching function subclass %@",cls];
  } else if ([record[@"node"] isEqual:@"buffer-address"]) {
    result=MSG(id(*)(id,SEL,NSUInteger,NSUInteger,BOOL),[NSClassFromString(@"MTLFunctionStitchingInputBufferAddress") alloc],
               sel_registerName("initWithBindIndex:byteOffset:dereference:"),[record[@"bindIndex"] unsignedIntegerValue],
               [record[@"byteOffset"] unsignedIntegerValue],[record[@"dereference"] boolValue]);
  } else if ([record[@"node"] isEqual:@"texture"] || [record[@"node"] isEqual:@"sampler"]) {
    NSString *cls=[record[@"node"] isEqual:@"texture"]?@"MTLFunctionStitchingInputTexture":@"MTLFunctionStitchingInputSampler";
    result=MSG(id(*)(id,SEL,NSUInteger),[NSClassFromString(cls) alloc],sel_registerName("initWithBindIndex:"),
               [record[@"bindIndex"] unsignedIntegerValue]);
  } else if ([record[@"node"] isEqual:@"thread-position-in-grid"]) {
    result=[NSClassFromString(@"MTLFunctionStitchingBuiltinThreadPositionInGrid") new];
  }
  if (!result) [NSException raise:@"Capture" format:@"stitching node construction failed"];
  nodes[@(index)]=result; [active removeObject:@(index)];
  return result;
}
static id captureStitchingGraph(NSDictionary *graph) {
  NSMutableDictionary *nodes=[NSMutableDictionary dictionary]; NSMutableSet *active=[NSMutableSet set];
  NSMutableArray *functions=[NSMutableArray array], *attributes=[NSMutableArray array];
  for (NSNumber *ref in graph[@"nodes"])
    [functions addObject:captureStitchingNode(graph,ref.unsignedIntegerValue,nodes,active)];
  id output=graph[@"outputNode"]==[NSNull null]?nil:
    captureStitchingNode(graph,[graph[@"outputNode"] unsignedIntegerValue],nodes,active);
  for (NSString *name in graph[@"attributes"]) {
    if (![name isEqual:@"always-inline"]) [NSException raise:@"Capture" format:@"unsupported stitching attribute %@",name];
    id attribute=[NSClassFromString(@"MTLFunctionStitchingAttributeAlwaysInline") new];
    if (!attribute) [NSException raise:@"Capture" format:@"runtime lacks stitching attribute %@",name];
    [attributes addObject:attribute];
  }
  NSString *cls=graph[@"graphClass"]?:@"MTLFunctionStitchingGraph";
  if (![@[@"MTLFunctionStitchingGraph",@"MTLFunctionStitchingGraphSPI"] containsObject:cls])
    [NSException raise:@"Capture" format:@"unsupported stitching graph subclass %@",cls];
  id result=MSG(id(*)(id,SEL,id,id,id,id),[NSClassFromString(cls) alloc],
    sel_registerName("initWithFunctionName:nodes:outputNode:attributes:"),graph[@"functionName"],functions,output,attributes);
  if (!result) [NSException raise:@"Capture" format:@"runtime lacks stitching graph subclass %@",cls];
  return result;
}
static void captureFields(id target, NSDictionary *fields) {
  for (NSString *key in fields) {
    id value=fields[key];
    if (value==[NSNull null]) continue;
    if ([key isEqual:@"constantValues"]) {
      id constants=[[NSClassFromString(@"MTLFunctionConstantValues") alloc] init];
      for (NSString *slot in value) {
        NSDictionary *record=value[slot]; NSData *bytes=captureObject(record[@"bytes"]);
        if (!bytes.length) [NSException raise:@"Capture" format:@"constant bytes unavailable"];
        NSUInteger type=[record[@"type"] unsignedIntegerValue];
        if ([slot hasPrefix:@"index:"])
          MSG(void(*)(id,SEL,const void*,NSUInteger,NSUInteger),constants,sel_registerName("setConstantValue:type:atIndex:"),bytes.bytes,type,[[slot substringFromIndex:6] integerValue]);
        else if ([slot hasPrefix:@"name:"])
          MSG(void(*)(id,SEL,const void*,NSUInteger,id),constants,sel_registerName("setConstantValue:type:withName:"),bytes.bytes,type,[slot substringFromIndex:5]);
        else [NSException raise:@"Capture" format:@"unknown constant slot %@",slot];
      }
      [target setValue:constants forKey:key];
    } else if ([@[@"colorAttachments",@"vertexBuffers",@"fragmentBuffers",@"buffers",@"attributes",@"layouts"] containsObject:key]) {
      id array=[target valueForKey:key];
      for (NSUInteger i=0;i<[value count];i++) captureFields([array objectAtIndexedSubscript:i],value[i]);
    } else if ([@[@"vertexLinkedFunctions",@"fragmentLinkedFunctions",@"linkedFunctions",@"vertexDescriptor",@"stageInputDescriptor"] containsObject:key]) {
      NSString *cls=[key isEqual:@"vertexDescriptor"]?@"MTLVertexDescriptor":
                    [key isEqual:@"stageInputDescriptor"]?@"MTLStageInputOutputDescriptor":@"MTLLinkedFunctions";
      id object=[[NSClassFromString(cls) alloc] init];
      if (!object) [NSException raise:@"Capture" format:@"runtime lacks %@",cls];
      captureFields(object,value); [target setValue:object forKey:key];
    } else [target setValue:captureObject(value) forKey:key];
  }
}
static id captureObject(id value) {
  if (value==[NSNull null]) return nil;
  if ([value isKindOfClass:[NSArray class]]) {
    NSMutableArray *result=[NSMutableArray array];
    for (id item in value) [result addObject:captureObject(item)];
    return result;
  }
  if (![value isKindOfClass:[NSDictionary class]]) return value;
  if (value[@"blob"]) {
    NSData *data=[NSData dataWithContentsOfFile:[captureRoot stringByAppendingPathComponent:value[@"blob"]]];
    if (!data) [NSException raise:@"Capture" format:@"missing captured payload %@",value[@"blob"]];
    return data;
  }
  NSString *kind=value[@"kind"];
  if (!kind) {
    NSMutableDictionary *result=[NSMutableDictionary dictionary];
    for (NSString *key in value) result[key]=captureObject(value[key])?:[NSNull null];
    return result;
  }
  id cached=captureObjects[value]; if (cached) return cached;
  id result=nil; NSError *error=nil;
  if ([kind isEqual:@"library"]) {
    if (value[@"image_filter_functions"]) {
      NSArray *functions=captureObject(value[@"image_filter_functions"]), *records=value[@"function_info"];
      if (functions.count!=records.count) [NSException raise:@"Capture" format:@"image-filter info count differs"];
      NSMutableData *storage=[NSMutableData dataWithLength:records.count*sizeof(ReplayFilterInfo)];
      ReplayFilterInfo *info=storage.mutableBytes;
      NSMutableArray *parameterStorage=[NSMutableArray array];
      for (NSUInteger i=0;i<records.count;i++) {
        NSDictionary *row=records[i]; NSArray *parameters=row[@"parameters"];
        NSMutableData *bytes=[NSMutableData dataWithLength:parameters.count*sizeof(ReplayFilterParameter)];
        [parameterStorage addObject:bytes]; ReplayFilterParameter *p=bytes.mutableBytes;
        for (NSUInteger j=0;j<parameters.count;j++) {
          p[j].field0=[parameters[j][@"field0"] unsignedIntValue];
          p[j].field8=[parameters[j][@"field8"] unsignedIntegerValue];
          p[j].field16=[parameters[j][@"field16"] unsignedIntegerValue];
        }
        info[i]=(ReplayFilterInfo){[row[@"flag"] boolValue],parameters.count,bytes.bytes};
      }
      result=MSG(id(*)(id,SEL,id,const ReplayFilterInfo*,NSError**),gDev,
        sel_registerName("newLibraryWithImageFilterFunctionsSPI:imageFilterFunctionInfo:error:"),functions,info,&error);
    } else if (value[@"descriptor"]) {
      id descriptor=[NSClassFromString(@"MTLStitchedLibraryDescriptor") new];
      if (!descriptor) [NSException raise:@"Capture" format:@"runtime lacks MTLStitchedLibraryDescriptor"];
      captureFields(descriptor,value[@"descriptor"]);
      result=MSG(id(*)(id,SEL,id,NSError**),gDev,sel_registerName("newLibraryWithDescriptor:error:"),descriptor,&error);
    } else if (value[@"source"]) {
      NSString *source=[[NSString alloc] initWithData:captureObject(value[@"source"]) encoding:NSUTF8StringEncoding];
      id options=nil;
      if ([value[@"options"] isKindOfClass:[NSDictionary class]]) {
        options=[[NSClassFromString(@"MTLCompileOptions") alloc] init];
        captureFields(options,value[@"options"]);
      }
      result=MSG(id(*)(id,SEL,id,id,NSError**),gDev,sel_registerName("newLibraryWithSource:options:error:"),source,options,&error);
    } else {
      NSData *data=captureObject(value[@"data"]);
      dispatch_data_t bytes=dispatch_data_create(data.bytes,data.length,dispatch_get_main_queue(),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
      result=MSG(id(*)(id,SEL,id,NSError**),gDev,sel_registerName("newLibraryWithData:error:"),bytes,&error);
    }
  } else if ([kind isEqual:@"stitching-graph"]) result=captureStitchingGraph(value);
  else if ([kind isEqual:@"function"]) {
    id library=captureObject(value[@"library"]); NSDictionary *fields=value[@"descriptor"];
    if ([value[@"descriptor_api"] boolValue] || fields[@"constantValues"]) {
      id descriptor=[[NSClassFromString(@"MTLFunctionDescriptor") alloc] init];
      captureFields(descriptor,fields);
      result=MSG(id(*)(id,SEL,id,NSError**),library,sel_registerName("newFunctionWithDescriptor:error:"),descriptor,&error);
    } else result=MSG(id(*)(id,SEL,id),library,sel_registerName("newFunctionWithName:"),fields[@"name"]);
  } else [NSException raise:@"Capture" format:@"unknown recipe kind %@",kind];
  if (!result) [NSException raise:@"Capture" format:@"%@ failed: %@",kind,error];
  captureObjects[value]=result;
  return result;
}
static BOOL capturePipeline(NSString *path, NSError **error) {
  NSData *data=[NSData dataWithContentsOfFile:path];
  if (!data) [NSException raise:@"Capture" format:@"missing recipe %@",path];
  NSDictionary *recipe=[NSJSONSerialization JSONObjectWithData:data options:0 error:error];
  if (!recipe) return NO;
  captureRoot=[path stringByDeletingLastPathComponent];
  captureObjects=[NSMutableDictionary dictionary];
  NSString *kind=recipe[@"kind"];
  BOOL render=[kind isEqual:@"render"];
  if (!render && ![kind isEqual:@"compute"]) [NSException raise:@"Capture" format:@"unknown pipeline kind %@",kind];
  id descriptor=[[NSClassFromString(render?@"MTLRenderPipelineDescriptor":@"MTLComputePipelineDescriptor") alloc] init];
  captureFields(descriptor,recipe[@"descriptor"]);
  SEL selector=sel_registerName(render?"newRenderPipelineStateWithDescriptor:options:reflection:error:":"newComputePipelineStateWithDescriptor:options:reflection:error:");
  id pipeline=MSG(id(*)(id,SEL,id,NSUInteger,id*,NSError**),gDev,selector,descriptor,[recipe[@"options"] unsignedIntegerValue],NULL,error);
  captureObjects=nil;
  return pipeline!=nil;
}
