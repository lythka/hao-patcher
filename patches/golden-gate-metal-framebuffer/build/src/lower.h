#pragma once
#include <string>
#include <utility>
#include <vector>

struct FBBinding { unsigned attachment, slot; std::string type, name; };
struct FBPlan { std::string function; std::vector<FBBinding> bindings; bool multisample; };
struct FBLowered { std::string text; std::vector<FBPlan> plans; };
std::vector<std::string> fbSplit(const std::string &text);
FBLowered fbLower(const std::string &text, bool multisample);
std::string fbDrawInputs(const std::string &text, bool vertex);
// Named metadata KEY ("air.version", "air.language_version") -> (major, minor). Linear in the text.
std::pair<unsigned, unsigned> fbAirVersion(const std::string &text, const std::string &key);
