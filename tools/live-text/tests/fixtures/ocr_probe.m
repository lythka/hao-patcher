// Authored OCR fixture: draws two text lines and recognizes them with Vision (VNRecognizeTextRequest, accurate).
// Prints one recognized line per output line. Builds x86_64; on an Apple silicon host it runs through Rosetta.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreText/CoreText.h>
#import <Vision/Vision.h>

static void draw(CGContextRef context, NSString *line, CGFloat y) {
    CTFontRef font = CTFontCreateWithName(CFSTR("Helvetica"), 56, NULL);
    NSDictionary *attributes = @{(id)kCTFontAttributeName: (__bridge id)font,
                                 (id)kCTForegroundColorAttributeName: (__bridge id)CGColorGetConstantColor(kCGColorBlack)};
    CTLineRef text = CTLineCreateWithAttributedString(
        (__bridge CFAttributedStringRef)[[NSAttributedString alloc] initWithString:line attributes:attributes]);
    CGContextSetTextPosition(context, 60, y);
    CTLineDraw(text, context);
    CFRelease(text);
    CFRelease(font);
}

int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *first = argc > 1 ? @(argv[1]) : @"Live Text on Intel";
        NSString *second = argc > 2 ? @(argv[2]) : @"HD Graphics 4000";
        CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
        CGContextRef context = CGBitmapContextCreate(NULL, 960, 400, 8, 0, space,
                                                     kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little);
        CGContextSetFillColorWithColor(context, CGColorGetConstantColor(kCGColorWhite));
        CGContextFillRect(context, CGRectMake(0, 0, 960, 400));
        draw(context, first, 250);
        draw(context, second, 100);
        CGImageRef image = CGBitmapContextCreateImage(context);
        VNRecognizeTextRequest *request = [VNRecognizeTextRequest new];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        request.usesLanguageCorrection = NO;
        NSError *error = nil;
        VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
        if (![handler performRequests:@[request] error:&error]) {
            fprintf(stderr, "ocr_probe: %s\n", error.description.UTF8String);
            return 1;
        }
        for (VNRecognizedTextObservation *observation in request.results)
            printf("%s\n", [observation topCandidates:1].firstObject.string.UTF8String);
        CGImageRelease(image);
        CGContextRelease(context);
        CGColorSpaceRelease(space);
    }
    return 0;
}
