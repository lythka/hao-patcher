// Exercise the shared exchange IR on 32 CPU threads. The barrier shim models a
// full workgroup fence; GPU scheduling/performance still need hardware tests.
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
#include <stdatomic.h>
typedef float V4 __attribute__((ext_vector_type(4)));
extern V4 exchange_shuffle(V4,uint16_t,uint16_t) __asm__("_logical32_shuffle_v4f32");
extern uint32_t exchange_int(uint32_t,uint16_t,uint16_t) __asm__("_logical32_broadcast_i32");
extern float exchange_float(float,uint16_t,uint16_t) __asm__("_logical32_broadcast_f32");
extern uint64_t exchange_ballot(_Bool,uint16_t) __asm__("_logical32_ballot");
extern float exchange_max(float,uint16_t) __asm__("_logical32_max");
void barrier(int,int) __asm__("_air.wg.barrier");
float maximum(float,float) __asm__("_air.fast_fmax.f32");
static pthread_mutex_t mutex=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static unsigned arrived,generation;
static _Atomic unsigned failures,comparisons;
void barrier(int flags,int scope){
 if(flags!=2 || scope!=1)atomic_fetch_add(&failures,1);
 pthread_mutex_lock(&mutex);unsigned mine=generation;
 if(++arrived==32){arrived=0;generation++;pthread_cond_broadcast(&condition);}
 else while(generation==mine)pthread_cond_wait(&condition,&mutex);
 pthread_mutex_unlock(&mutex);
}
float maximum(float a,float b){return fmaxf(a,b);}
static uint32_t bits(unsigned iteration,unsigned lane){return (iteration*0x9e3779b9U)^(lane*0x1234567U);}
static float value(unsigned iteration,unsigned lane){return -1000.0f-(float)((lane+iteration)%32);}
static void check(int equal){atomic_fetch_add(&comparisons,1);if(!equal)atomic_fetch_add(&failures,1);}
static void *worker(void *arg){
 uint16_t lane=(uintptr_t)arg;
 for(unsigned i=0;i<64;i++){
  V4 v={(float)i,(float)lane,(float)(i+lane),-(float)lane};
  V4 shuffled=exchange_shuffle(v,1,lane);unsigned next=(lane+1)&31;
  check(shuffled[0]==(float)i);check(shuffled[1]==(float)next);
  check(shuffled[2]==(float)(i+next));check(shuffled[3]==-(float)next);
  unsigned source=i&31;
  check(exchange_int(bits(i,lane),source,lane)==bits(i,source));
  check(exchange_float(value(i,lane),source,lane)==value(i,source));
  uint64_t expected=0;for(unsigned j=0;j<32;j++)if((bits(i,j)&7)==3)expected|=1ULL<<j;
  check(exchange_ballot((bits(i,lane)&7)==3,lane)==expected);
  check(exchange_max(value(i,lane),lane)==-1000.0f);
 }
 return NULL;
}
int main(void){
 pthread_t threads[32];
 for(uintptr_t i=0;i<32;i++)if(pthread_create(&threads[i],NULL,worker,(void*)i))return 2;
 for(unsigned i=0;i<32;i++)pthread_join(threads[i],NULL);
 printf("%u exchange comparisons, %u failures\n",comparisons,failures);
 return failures!=0;
}
