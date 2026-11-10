/*
 * btpower (local.btpower, once at boot): turn the Bluetooth controller on in recoveryOS.
 * Recovery leaves it off and has no Bluetooth settings; the stack itself works (BlueToolFixup is
 * active, bluetoothd drives the BCM20702). With the controller on, pairing works the native Intel way: plug the Magic
 * Keyboard / Mouse / Trackpad in with its cable once, bluetoothd pairs it. Setting the power state needs the Bluetooth
 * TCC allowance (ad-hoc entitlement com.apple.private.tcc.allow kTCCServiceBluetoothAlways; amfi=0x80).
 * Waits up to 60 s for bluetoothd and the controller.
 */
#include <stdio.h>
#include <unistd.h>
extern int IOBluetoothPreferenceGetControllerPowerState(void);
extern void IOBluetoothPreferenceSetControllerPowerState(int);
int main(void) {
    for (int i = 0; i < 120; i++) {
        if (IOBluetoothPreferenceGetControllerPowerState()) { printf("btpower: controller on\n"); return 0; }
        IOBluetoothPreferenceSetControllerPowerState(1);
        usleep(500000);
    }
    printf("btpower: controller did not power on\n");
    return 1;
}
