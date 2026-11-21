#include <iostream>
#include <string>
#include "swift/Demangling/Demangle.h"
int main(int argc, char **argv) {
    if (argc != 1) return 2;
    std::string name;
    while (std::getline(std::cin, name))
        std::cout << swift::Demangle::demangleSymbolAsString(
            name, swift::Demangle::DemangleOptions::SimplifiedUIDemangleOptions()) << '\n';
    return std::cin.bad() || !std::cout ? 1 : 0;
}
