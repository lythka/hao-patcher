#define COHERENCE_TEST 1
#include "coherence_state.hpp"
#include "diagnostic_counts.hpp"
#include <cassert>
#include <cstdio>
struct Surface {static int live;Surface(){++live;}~Surface(){--live;}};int Surface::live=0;
using S=CoherenceState<Surface>;
static void *h(unsigned i){return reinterpret_cast<void*>(uintptr_t(i));}
static std::shared_ptr<Surface> surface(){return std::make_shared<Surface>();}
static void empty(S &s){assert((s.counts()==std::vector<size_t>{0,0,0,0}));assert(Surface::live==0);assert((s.origin_counts()==std::vector<size_t>{0,0,0}));}
int main(){
 // Retained native buffer/port/operation handles can be released while encoded
 // stream and asynchronous capture still use the surface. No early destruction.
 {S s;s.created_buffer(h(1),surface());s.retained_input(h(2),"image",h(3));s.bound(h(3),h(1));s.created_stream(h(4));s.encoded(h(4),h(2));auto flight=s.capture(h(4));assert(flight.size()==1);
  s.take_buffer(h(1));s.take_port(h(3));s.take_operation(h(2));assert(Surface::live==1);s.take_stream(h(4));assert(Surface::live==1);flight.clear();empty(s);}
 // Rebinding replaces only current input. A prior submitted tile retains its
 // original surface, and the next submission gets the new surface.
 {S s;s.created_stream(h(4));s.encoded(h(4),h(2));s.retained_input(h(2),"image",h(3));s.created_buffer(h(1),surface());s.bound(h(3),h(1));auto first=s.capture(h(4));s.take_buffer(h(1));s.created_buffer(h(5),surface());s.bound(h(3),h(5));auto second=s.capture(h(4));assert(first.size()==1&&second.size()==1&&first[0]!=second[0]&&Surface::live==2);
  s.take_port(h(3));s.take_operation(h(2));s.take_stream(h(4));s.take_buffer(h(5));assert(Surface::live==2);first.clear();assert(Surface::live==1);second.clear();empty(s);}
 // Handle-address reuse cannot restore old state over a new generation after
 // a failed native release. A failed ordinary release can restore the old one.
 {S s;s.created_buffer(h(1),surface());auto old=s.take_buffer(h(1));s.created_buffer(h(1),surface());s.restore_buffer(h(1),old);old.reset();assert(Surface::live==1);auto current=s.take_buffer(h(1));s.restore_buffer(h(1),current);current.reset();assert(Surface::live==1);s.take_buffer(h(1));empty(s);}
 // Unsupported replacement buffer clears the previous binding; output handles
 // do not acquire an input binding even when their transient address is reused.
 {S s;s.created_buffer(h(1),surface());s.retained_input(h(2),"image",h(3));s.bound(h(3),h(1));s.created_stream(h(4));s.encoded(h(4),h(2));assert(s.capture(h(4)).size()==1);s.bound(h(3),h(99));assert(s.capture(h(4)).empty());s.retained_output(h(3));assert(!s.bound(h(3),h(1)));s.take_buffer(h(1));s.take_operation(h(2));s.take_stream(h(4));empty(s);}
 // Repeated encoding/aliased input surfaces do not accumulate duplicate locks;
 // stream create resets only that stream while another encoded user remains.
 {S s;auto same=surface();s.created_buffer(h(1),same);s.created_buffer(h(5),same);same.reset();s.retained_input(h(2),"image",h(3));s.retained_input(h(2),"other",h(6));s.bound(h(3),h(1));s.bound(h(6),h(5));s.created_stream(h(4));s.encoded(h(4),h(2));s.encoded(h(4),h(2));s.created_stream(h(7));s.encoded(h(7),h(2));assert(s.capture(h(4)).size()==1);s.created_stream(h(4));assert(s.capture(h(4)).empty()&&s.capture(h(7)).size()==1);s.take_stream(h(4));s.take_stream(h(7));s.take_port(h(3));s.take_port(h(6));s.take_operation(h(2));s.take_buffer(h(1));s.take_buffer(h(5));empty(s);}
 // Thousands of complete lifetimes leave no global handle entries or surfaces.
 {S s;for(int i=0;i<10000;i++){s.created_buffer(h(1),surface());s.retained_input(h(2),"image",h(3));s.bound(h(3),h(1));s.created_stream(h(4));s.encoded(h(4),h(2));auto flight=s.capture(h(4));s.take_stream(h(4));s.take_operation(h(2));s.take_port(h(3));s.take_buffer(h(1));flight.clear();empty(s);}}
 // Detector provenance survives each earlier handle release. A surface created
 // before compilation remains observable, and other models/ports are untouched.
 {S s;s.created_buffer(h(1),surface());s.recorded_library(h(10),2);
  s.retained_function(h(10),h(11));s.take_library(h(10));
  s.created_options(h(11),h(12));s.take_function(h(11));
  s.created_operation(h(12),h(2));s.take_options(h(12));
  s.retained_input(h(2),"img_input",h(3));s.retained_input(h(2),"other",h(6));
  s.retained_input(h(20),"img_input",h(21));
  auto check=s.check_bind(h(3),h(1));assert(check.detector&&check.surface);
  assert(!s.check_bind(h(6),h(1)).detector&&!s.check_bind(h(21),h(1)).detector);
  assert(s.check_bind(h(3),h(99)).detector&&!s.check_bind(h(3),h(99)).surface);
  s.take_operation(h(2));assert(s.check_bind(h(3),h(1)).detector);
  s.take_buffer(h(1));check.surface.reset();s.take_port(h(3));s.take_port(h(6));s.take_port(h(21));s.take_operation(h(20));empty(s);}
 // Guard checks all dimensions and format, not a broad shape-only match. Padded
 // buffers remain native for recognizers and unrelated models, including a port
 // with the identical input name. Only the adapted detector asks for this check.
 {S s;S::Layout good{0x42475241,480,480,1920,921600};assert(S::valid_detector_layout(good));
  for(int field=0;field<5;++field){auto bad=good;if(field==0)bad.format=0x52474241;if(field==1)bad.width=479;if(field==2)bad.height=479;if(field==3)bad.row_bytes=2048;if(field==4)--bad.allocation;assert(!S::valid_detector_layout(bad));}
  s.recorded_library(h(10),1);s.retained_function(h(10),h(11));s.created_options(h(11),h(12));s.created_operation(h(12),h(2));s.retained_input(h(2),"img_input",h(3));assert(!s.check_bind(h(3),h(99)).detector);
  s.take_library(h(10));s.take_function(h(11));s.take_options(h(12));s.take_operation(h(2));s.take_port(h(3));empty(s);}
 // All provenance stages handle address reuse without stale detector admission.
 {S s;s.recorded_library(h(10),2);auto old=s.take_library(h(10));s.recorded_library(h(10),1);s.restore_library(h(10),old);s.retained_function(h(10),h(11));s.created_options(h(11),h(12));s.created_operation(h(12),h(2));s.retained_input(h(2),"img_input",h(3));assert(!s.check_bind(h(3),h(99)).detector);
  s.recorded_library(h(10),2);s.retained_function(h(10),h(11));auto f=s.take_function(h(11));s.retained_function(h(99),h(11));s.restore_function(h(11),f);
  s.created_options(h(11),h(12));s.created_operation(h(12),h(2));s.retained_input(h(2),"img_input",h(3));assert(!s.check_bind(h(3),h(99)).detector);
  s.retained_function(h(10),h(11));s.created_options(h(11),h(12));auto opts=s.take_options(h(12));s.created_options(h(99),h(12));s.restore_options(h(12),opts);s.created_operation(h(12),h(2));s.retained_input(h(2),"img_input",h(3));assert(!s.check_bind(h(3),h(99)).detector);
  s.take_function(h(11));s.take_library(h(10));s.take_options(h(12));s.take_operation(h(2));s.take_port(h(3));old.reset();f.reset();empty(s);}
 // A native retain/cache hit may return an identical opaque handle. Releasing
 // one observed alias must not erase provenance still owned by another alias.
 {S s;s.recorded_library(h(10),2);s.recorded_library(h(10),2);s.take_library(h(10));
  s.retained_function(h(10),h(11));s.retained_function(h(10),h(11));s.take_function(h(11));
  s.created_options(h(11),h(12));s.created_options(h(11),h(12));s.take_options(h(12));
  auto failed=s.take_options(h(12));s.restore_options(h(12),failed);failed.reset();
  s.created_operation(h(12),h(2));s.retained_input(h(2),"img_input",h(3));assert(s.check_bind(h(3),h(99)).detector);
  s.take_library(h(10));s.take_function(h(11));s.take_options(h(12));s.take_operation(h(2));s.take_port(h(3));empty(s);}
 // Diagnostics cannot grow with app lifetime: six categories, four records
 // each; cumulative bind counts still advance after stderr output is capped.
 {CoherenceDiagnosticCounts c;unsigned emitted=0;for(unsigned i=0;i<10000;++i)for(unsigned e=0;e<CoherenceDiagnosticCounts::EventCount;++e){auto event=static_cast<CoherenceDiagnosticCounts::Event>(e);auto sample=c.record(event);emitted+=sample.emit;assert(sample.count==i+1);}assert(emitted==24);auto last=c.record(CoherenceDiagnosticCounts::DetectorBindPass);assert(!last.emit&&last.passes==10001&&last.refusals==10000);}
 puts("PASS: detector provenance, full layout contract, mixed models, early release/reuse and retained aliases, asynchronous lifetime, rebinding, release failure/reuse, unsupported/output isolation, stream reuse/dedup and10000 cleanup cycles");
}
