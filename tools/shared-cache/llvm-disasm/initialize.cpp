// Initialize only the MC components needed by LLVMCreateDisasm.
#include <mutex>
extern "C" {
void LLVMInitializeX86TargetInfo();
void LLVMInitializeX86TargetMC();
void LLVMInitializeX86Disassembler();
void lto_initialize_disassembler() {
    static std::once_flag once;
    std::call_once(once, [] {
        LLVMInitializeX86TargetInfo();
        LLVMInitializeX86TargetMC();
        LLVMInitializeX86Disassembler();
    });
}
}
