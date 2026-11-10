// A memo is a compiler reply, not a newly exercised compiler. Frame every input
// separately so targetData, ABI/options and complete request bytes cannot alias.
static NSData* rpDigest(NSArray<NSData*>* fields){
  CC_SHA256_CTX context; CC_SHA256_Init(&context);
  for(NSData* data in fields){
    uint64_t length=data.length; CC_SHA256_Update(&context,&length,sizeof length);
    const unsigned char* p=data.bytes;
    for(NSUInteger left=data.length;left;){ CC_LONG n=(CC_LONG)MIN(left,(NSUInteger)(1u<<30)); CC_SHA256_Update(&context,p,n);p+=n;left-=n; }
  }
  unsigned char hash[CC_SHA256_DIGEST_LENGTH];CC_SHA256_Final(hash,&context);
  return [NSData dataWithBytes:hash length:sizeof hash];
}
static NSMutableDictionary* rpServices(void){
  static NSMutableDictionary* states;static dispatch_once_t once;
  dispatch_once(&once,^{states=[[NSMutableDictionary alloc] init];});return states;
}
static NSString* rpServiceKey(void* service,unsigned slot){return [NSString stringWithFormat:@"%p:%u",service,slot];}
static void rpSetIdentity(void* service,unsigned slot,NSData* identity){
  NSMutableDictionary* states=rpServices();@synchronized(states){
    NSString* key=rpServiceKey(service,slot);if(identity)states[key]=identity;else [states removeObjectForKey:key];
  }
}
static void rpForgetService(void* service){
  NSMutableDictionary* states=rpServices();NSString* prefix=[NSString stringWithFormat:@"%p:",service];
  @synchronized(states){for(NSString* key in [states allKeys])if([key hasPrefix:prefix])[states removeObjectForKey:key];}
}
static NSData* rpServiceIdentity(const char* path,const void* target,size_t length){
  NSString* name=path?@(path):@"";
  NSData* plugin=[NSData dataWithContentsOfFile:name];
  // The interposer resolves System compiler plugin names through this path.
  if(!plugin) for(NSString* directory in [@(getenv("DYLD_LIBRARY_PATH")?:"") componentsSeparatedByString:@":"]){
    plugin=[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:name.lastPathComponent]];
    if(plugin)break;
  }
  if(!plugin)return nil; // Still compile; incomplete identity cannot reuse a reply.
  return rpDigest(@[[name dataUsingEncoding:NSUTF8StringEncoding],plugin,[NSData dataWithBytes:target length:length]]);
}
static NSString* rpMemoKey(void* service,unsigned type,unsigned llvm,const void* request,size_t length){
  const char* runtime=getenv("RP_RUNTIME_ID");if(!runtime||!*runtime)return nil;
  NSMutableDictionary* states=rpServices();NSData* state;
  @synchronized(states){state=[[states objectForKey:rpServiceKey(service,type)] retain];}
  if(!state)return nil;
  NSData* hash=rpDigest(@[[@"compiler-reply-v2" dataUsingEncoding:NSUTF8StringEncoding],
    [@(runtime) dataUsingEncoding:NSUTF8StringEncoding],state,
    [NSData dataWithBytes:&type length:sizeof type],[NSData dataWithBytes:&llvm length:sizeof llvm],
    [NSData dataWithBytes:request length:length]]);
  [state release];NSMutableString* key=[NSMutableString string];
  const unsigned char* bytes=hash.bytes;for(NSUInteger i=0;i<hash.length;i++)[key appendFormat:@"%02x",bytes[i]];
  return key;
}
