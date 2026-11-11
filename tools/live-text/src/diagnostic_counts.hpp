#pragma once
#include <atomic>
#include <cstdint>
// Metadata-only counters. Callers opt in before touching this state; at most
// four messages per event category can be emitted in a process lifetime.
class CoherenceDiagnosticCounts {
public:
 enum Event {RecognizerLibrary,DetectorLibrary,RecognizerOperation,DetectorOperation,DetectorBindPass,DetectorBindRefusal,EventCount};
 struct Sample {bool emit;uint64_t count,passes,refusals;};
 Sample record(Event event){auto n=counts[event].fetch_add(1,std::memory_order_relaxed)+1;return {n<=4,n,counts[DetectorBindPass].load(std::memory_order_relaxed),counts[DetectorBindRefusal].load(std::memory_order_relaxed)};}
private:
 std::atomic<uint64_t> counts[EventCount]{};
};
