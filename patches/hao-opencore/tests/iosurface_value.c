#include <CoreFoundation/CoreFoundation.h>
#include <IOSurface/IOSurface.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    alarm(15);
    size_t n = argc > 1 ? strtoull(argv[1],0,0) : 65536;
    if (!n || n > 16*1024*1024) return 2;
    int w=16, h=16, bpe=4;
    CFNumberRef wn=CFNumberCreate(0,kCFNumberIntType,&w), hn=CFNumberCreate(0,kCFNumberIntType,&h), bn=CFNumberCreate(0,kCFNumberIntType,&bpe);
    const void *keys[]={kIOSurfaceWidth,kIOSurfaceHeight,kIOSurfaceBytesPerElement};
    const void *vals[]={wn,hn,bn};
    CFDictionaryRef props=CFDictionaryCreate(0,keys,vals,3,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
    IOSurfaceRef surface=IOSurfaceCreate(props);
    if (!surface) { puts("surface creation failed"); return 3; }
    CFMutableDataRef data=CFDataCreateMutable(0,n); CFDataSetLength(data,n);
    for (size_t i=0;i<n;i++) CFDataGetMutableBytePtr(data)[i]=(unsigned char)(i*31+17);
    printf("set size=%zu surface=%u\n",n,IOSurfaceGetID(surface)); fflush(stdout);
    IOSurfaceSetValue(surface,CFSTR("compatibility-probe"),data);
    CFTypeRef got=IOSurfaceCopyValue(surface,CFSTR("compatibility-probe"));
    int same=got&&CFEqual(got,data); printf("roundtrip=%d\n",same);fflush(stdout);
    if(got)CFRelease(got);CFRelease(data);CFRelease(surface);CFRelease(props);CFRelease(wn);CFRelease(hn);CFRelease(bn);
    return same?0:4;
}
