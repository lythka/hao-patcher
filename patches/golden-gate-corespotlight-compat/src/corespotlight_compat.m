// corespotlight_compat: the x86_64 CoreSpotlight method Darwin 27.0's own native SpotlightDaemon calls.
//
// 26A428's x86_64 (RosettaOS) CoreSpotlight has CSSearchQueryContext's _compiledRankingFactsConfiguration ivar and
// -rankingFactsConfiguration, but not the -compiledRankingFactsConfiguration getter the arm64e build has.
// SpotlightDaemon's -[SPCoreSpotlightTask _makeSIQueryWithQueryString:queryContext:] sends it for every query, so
// corespotlightd raised "unrecognized selector" and (libdispatch's callout unwind info being unusable) crashed.
//
// The getter mirrors the arm64e implementation: a ranking configuration is compiled (-compile) when there is one,
// otherwise the ivar is returned. x86_64's _CSRankingModelConfiguration has no -compile, so a configuration yields
// nil there (the default ranking). The method is added only when the class lacks it: if Apple ships it, this
// library does nothing.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <os/log.h>

static id compiled_ranking_facts(id self, SEL _cmd) {
    (void)_cmd;
    id configuration = nil;
    if ([self respondsToSelector:@selector(rankingFactsConfiguration)])
        configuration = [self performSelector:@selector(rankingFactsConfiguration)];
    if (configuration)
        return [configuration respondsToSelector:@selector(compile)] ? [configuration performSelector:@selector(compile)] : nil;
    Ivar ivar = class_getInstanceVariable(object_getClass(self), "_compiledRankingFactsConfiguration");
    return ivar ? object_getIvar(self, ivar) : nil;
}

static void install(Class cls) {
    if (!cls || class_getInstanceMethod(cls, @selector(compiledRankingFactsConfiguration))) return;
    if (class_addMethod(cls, @selector(compiledRankingFactsConfiguration), (IMP)compiled_ranking_facts, "@16@0:8"))
        os_log(OS_LOG_DEFAULT, "corespotlight-compat: added -[CSSearchQueryContext compiledRankingFactsConfiguration]");
}

// Linked against CoreSpotlight, so its classes are mapped before this initializer runs.
__attribute__((constructor)) static void start(void) {
    install(objc_lookUpClass("CSSearchQueryContext"));
}
