#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <linux/kcov.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>
static void fail(const char *s){perror(s);exit(1);}
int main(int argc,char **argv){
    if(argc!=2)return 2;
    char *end;unsigned long bus=strtoul(argv[1],&end,10);
    if(*end||!bus||bus>255)return 2;
    const unsigned long words=1UL<<20;
    int fd=open("/sys/kernel/debug/kcov",O_RDWR|O_CLOEXEC);
    if(fd<0)fail("open kcov");
    if(ioctl(fd,KCOV_INIT_TRACE,words))fail("KCOV_INIT_TRACE");
    unsigned long *area=mmap(NULL,words*sizeof(*area),PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(area==MAP_FAILED)fail("mmap");
    struct kcov_remote_arg *arg=calloc(1,sizeof(*arg)+sizeof(uint64_t));
    if(!arg)fail("calloc");
    arg->trace_mode=KCOV_TRACE_PC;arg->area_size=words;arg->num_handles=1;
    arg->handles[0]=kcov_remote_handle(KCOV_SUBSYSTEM_USB,bus);
    bool active=false;char command[32];setvbuf(stdout,NULL,_IOLBF,0);
    puts("{\"ready\":true}");
    while(fgets(command,sizeof(command),stdin)){
        if(!strcmp(command,"start\n")){
            if(active)return 2;
            __atomic_store_n(&area[0],0,__ATOMIC_RELAXED);
            if(ioctl(fd,KCOV_REMOTE_ENABLE,arg))fail("KCOV_REMOTE_ENABLE");
            active=true;puts("{\"started\":true}");
        }else if(!strcmp(command,"stop\n")){
            if(!active)return 2;
            if(ioctl(fd,KCOV_DISABLE,0))fail("KCOV_DISABLE");
            active=false;unsigned long n=__atomic_load_n(&area[0],__ATOMIC_ACQUIRE);
            bool full=n>=words-1;if(n>words-1)n=words-1;
            printf("{\"saturated\":%s,\"pcs\":[",full?"true":"false");
            for(unsigned long i=0;i<n;i++)printf("%s\"0x%lx\"",i?",":"",area[i+1]);
            puts("]}");
        }else return 2;
    }
    if(active&&ioctl(fd,KCOV_DISABLE,0))fail("KCOV_DISABLE");
    free(arg);munmap(area,words*sizeof(*area));close(fd);return 0;
}
