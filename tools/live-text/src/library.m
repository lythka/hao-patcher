// Observe devices the caller creates; never request a GPU during startup.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#ifndef LIVE_TEXT_LIBRARY_TEST
#import <Metal/Metal.h>
#endif

typedef id __attribute__((ns_returns_retained)) (*LoadLibrary)(id,SEL,NSString *,NSError **);
typedef struct Entry { Class cls; IMP original; struct Entry *next; } Entry;
static Entry *entries;
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
@protocol HAOLegacyDeviceName
- (NSString *)name;
@end
static BOOL eligible(id device) {
    if(!device||![device respondsToSelector:@selector(name)])return NO;
    NSString *name=[(id<HAOLegacyDeviceName>)device name];
    return [name isKindOfClass:[NSString class]]&&[name containsString:@"Intel HD Graphics 4000"];
}

static IMP original_for(Class cls) {
    IMP result=NULL;
    pthread_mutex_lock(&mutex);
    for(Class current=cls;current&&!result;current=class_getSuperclass(current))
        for(Entry *e=entries;e;e=e->next)if(e->cls==current){result=e->original;break;}
    pthread_mutex_unlock(&mutex);
    return result;
}
static id __attribute__((ns_returns_retained)) load_file(id object,SEL selector,NSString *path,NSError **error) {
    LoadLibrary original=(LoadLibrary)original_for(object_getClass(object));
    NSString *selected=path;
    const char *configured=getenv("VISION_MPSNDARRAY_LIBRARY");
    if(eligible(object)&&configured&&configured[0]=='/'&&
       [path containsString:@"/MPSNDArray.framework/"]&&
       [path.lastPathComponent isEqualToString:@"default.metallib"])
        selected=[NSString stringWithUTF8String:configured]?:path;
    // The original API reports an unreadable candidate through NSError. Do not
    // terminate unrelated services because a diagnostic resource is missing.
    return original(object,selector,selected,error);
}
static BOOL signature(Method method) {
    if(!method||method_getNumberOfArguments(method)!=4)return NO;
    char *result=method_copyReturnType(method);
    BOOL ok=result&&!strcmp(result,"@");free(result);
    const char *types[]={"@",":","@","^@"};
    for(unsigned i=0;ok&&i<4;i++){char *type=method_copyArgumentType(method,i);ok=type&&!strcmp(type,types[i]);free(type);}
    return ok;
}
static void observe(id device) {
    const char *configured=getenv("VISION_MPSNDARRAY_LIBRARY");
    if(!eligible(device)||!configured||configured[0]!='/')return;
    Class cls=object_getClass(device);SEL selector=sel_registerName("newLibraryWithFile:error:");
    pthread_mutex_lock(&mutex);
    Method method=class_getInstanceMethod(cls,selector);
    if(!signature(method)){pthread_mutex_unlock(&mutex);return;}
    IMP previous=method_getImplementation(method);
    if(previous==(IMP)load_file){pthread_mutex_unlock(&mutex);return;}
    Entry *entry=calloc(1,sizeof(*entry));
    if(!entry){pthread_mutex_unlock(&mutex);return;}
    entry->cls=cls;entry->original=previous;entry->next=entries;entries=entry;
    if(!class_addMethod(cls,selector,(IMP)load_file,method_getTypeEncoding(method)))
        method_setImplementation(class_getInstanceMethod(cls,selector),(IMP)load_file);
    pthread_mutex_unlock(&mutex);
}

#ifdef LIVE_TEXT_LIBRARY_TEST
void LiveTextLibraryTestObserve(id device){observe(device);}
#else
static id<MTLDevice> __attribute__((ns_returns_retained)) create_default(void) {
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();observe(device);return device;
}
static NSArray<id<MTLDevice>> * __attribute__((ns_returns_retained)) copy_devices(void) {
    NSArray *devices=MTLCopyAllDevices();for(id device in devices)observe(device);return devices;
}
static NSArray<id<MTLDevice>> * __attribute__((ns_returns_retained)) copy_observed(id<NSObject> __strong *observer,MTLDeviceNotificationHandler handler) {
    MTLDeviceNotificationHandler wrapped=handler?^(id<MTLDevice> device,MTLDeviceNotificationName name){observe(device);handler(device,name);}:nil;
    NSArray *devices=MTLCopyAllDevicesWithObserver(observer,wrapped);for(id device in devices)observe(device);return devices;
}
#define HOOK(replacement,original) __attribute__((used,section("__DATA,__interpose"))) static const struct {const void *r,*o;} hook_##original={(void*)replacement,(void*)original};
HOOK(create_default,MTLCreateSystemDefaultDevice)
HOOK(copy_devices,MTLCopyAllDevices)
HOOK(copy_observed,MTLCopyAllDevicesWithObserver)
#endif
