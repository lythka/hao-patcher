# Userspace repair review: 26B131

This review separates observed binary compatibility from hardware and application
acceptance. Matching code patterns establish that a recipe applies; they do not
by themselves prove that a repaired path is exercised on the laptop.

| Repair | Current evidence | Decision |
|---|---|---|
| EndpointSecurity selectors | All 31 selector operands retain the old client ABI; the selected Tahoe provider retains its different ABI. | Keep the remapping. |
| Trackpad | All six sites retain the packet/variant behavior that excludes the Tahoe report path. | Keep; verify gestures on hardware. |
| CoreRoutine console lookup | The measured caller and guard still match; the repair initializes the UID when no console user is available. | Keep pending a bounded pre-login comparison; pattern matching alone is insufficient to remove it. |
| CoreSpotlight | Native CoreSpotlight still lacks the getter used by SpotlightDaemon; the supporting ivar remains. | Keep the getter adapter. |
| Display state | Native encoding still omits the presence byte consumed by the client. | Keep. |
| WindowServer | The incompatible legacy display-vtable path remains; inversion slot/flag moved in this release. | Keep the updated layout. Desktop inversion/Siri behavior still needs hardware validation. |
| Wi-Fi | The selected OCLP wrapper still lacks Apple80211Get20MhzChannelIndexBitmap and requires the code/data export distinctions. | Keep the adapter. |
| MPSMatrix | The legacy GPU family needs the fallback initializer; the current cache matches the recipe. | Keep pending graphics acceptance. |
| Audio | The no-console branch still reaches the server-died path. | Keep; a pre-login comparison could establish whether current system behavior makes it redundant. |
| Metal libraries/runtime/framebuffer | The legacy HD 4000 compiler and driver still serve newer framework clients. | Keep. Investigate pruning selector coverage used only by debug/replay paths. |
| Code identity | Translated programs gain a native executable slice while TCC/BTM need their original identity. | Keep; boot success alone cannot establish correct attribution. |
| Hello shaders | Only needed when retaining the Hello animation on the legacy GPU. | Optional feature. Blur-cache and Continue-button warmup are separate performance candidates. |
| hybridsearchd suppression | Historical non-Apple-silicon exit loop; current target behavior has not been compared. | Open removal candidate, requiring a bounded target run. |

No runtime repair has been removed based solely on this review. Targeted negative
controls are still needed for the candidates above.

Eight Tahoe framework roots were removed from extraction after positive evidence
that Golden Gate supplies their Intel code. The resulting selected native and
extension manifests match the prior selection. The minimal extraction also omits
the unused Haswell DriverKit cache. The archived donor payload fell from
1,942,322,740 to 1,476,002,843 bytes (about 24%). These are extraction savings,
not removal of an active compatibility repair.

## Initial hardware boot: Setup Assistant hang

The first 26B131 image reached Setup Assistant on the MBP9,2, then stopped
responding. Its main thread waited in `CPHasActivationRecord` through
ApplePushService's synchronous XPC call. `apsd` restarted over 100 times;
the kernel rejected its profile with an operation-count mismatch, 204 versus
200. The earlier CPU saturation subsided to about 85% idle while this wait
persisted. Reaching SSH therefore did not establish a usable installation.

The existing operation adapter refused filter 110 in the target profile.
That node is an isolated root of `asr-parser-enter`, an operation absent from
the Tahoe provider. Kernel-foundation r41 removes such roots only after proving
that no retained operation or node references them; profiles with auxiliary
root tables remain unsupported by this pruning step. Shared unsupported filters
still fail. This preserves the existing donor policy rather than inventing a
decision for a filter the donor cannot implement.

The laptop's compiled `apsd` profile reproduces the refusal in the old adapter.
The corrected profile retains identical graphs for all 200 remaining operations
and identical data. In separate short-lived probes on the same laptop, the
original profiles for `apsd`, `nfcd`, and `seld` return `EINVAL`; all three
converted profiles apply successfully. No system profile was replaced by those
probes. Host tests pass, including retained-decision equivalence and refusal
without partial writes. A new full-image boot is still required to establish
that Setup Assistant progresses.

## Runtime contracts

The initial 26B131 payload uses hao-runtime commit 128fcc0 with 1,895 regenerated
interface units and 50,655 records. It lacks the async provider and Optional<Data>
setter adapter. Both are being prepared as versioned release inputs. Their
research generation and native ARM comparisons belong to the hao release process;
end users consume versioned packages. An end-user image builder must not require
the private research repository or rerun contract discovery.
