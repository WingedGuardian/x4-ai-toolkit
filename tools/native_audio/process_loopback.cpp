// Bounded Windows process-tree loopback. No launch, device change, or output file.
// Native contract: Microsoft Windows-classic-samples ApplicationLoopback.
// Only --synthetic-fixture/--protocol may be used by offline qualification.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <wrl.h>
#include <wrl/implements.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <cerrno>
#include <string>
#include <vector>
#include <stdexcept>
#include <algorithm>
#ifndef X4_AUDIO_SOURCE_SHA256
#error Build must bind X4_AUDIO_SOURCE_SHA256 to these exact source bytes.
#endif
using Microsoft::WRL::ComPtr;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;
using Microsoft::WRL::ClassicCom;
using Microsoft::WRL::FtmBase;
constexpr uint32_t Rate=44100, Align=4, MaxPackets=8192;
struct Handle {
    HANDLE h=nullptr;
    explicit Handle(HANDLE v=nullptr):h(v){}
    ~Handle(){if(h && h!=INVALID_HANDLE_VALUE)CloseHandle(h);}
    Handle(const Handle&)=delete; Handle& operator=(const Handle&)=delete;
};
void require(bool yes,const char* why){if(!yes)throw std::runtime_error(why);}
void hr(HRESULT result,const char* why){require(SUCCEEDED(result),why);}
void output(const void* data,size_t count){
    const auto* p=static_cast<const uint8_t*>(data);
    while(count){
        DWORD n=0; const DWORD want=static_cast<DWORD>(std::min<size_t>(count,65536));
        require(WriteFile(GetStdHandle(STD_OUTPUT_HANDLE),p,want,&n,nullptr)&&n>0,"stdout failed");
        p+=n;count-=n;
    }
}
void u32(uint32_t v){uint8_t b[4];for(int i=0;i<4;++i)b[i]=uint8_t(v>>(i*8));output(b,4);}
void u64(uint64_t v){uint8_t b[8];for(int i=0;i<8;++i)b[i]=uint8_t(v>>(i*8));output(b,8);}
uint64_t ticks(){LARGE_INTEGER n{};require(QueryPerformanceCounter(&n)&&n.QuadPart>=0,"QPC unavailable");return uint64_t(n.QuadPart);}
uint64_t number(const wchar_t* s){
    require(s && *s && *s!=L'-',"invalid integer");
    errno=0;wchar_t* end=nullptr;const auto n=wcstoull(s,&end,10);
    require(!errno && end && !*end && std::to_wstring(n)==s,"noncanonical integer");return n;
}
void process(HANDLE h,uint64_t created,const std::wstring& image){
    require(WaitForSingleObject(h,0)==WAIT_TIMEOUT,"target not running");
    FILETIME c{},e{},k{},u{};
    require(GetProcessTimes(h,&c,&e,&k,&u),"target time unavailable");
    const uint64_t observed=(uint64_t(c.dwHighDateTime)<<32)|c.dwLowDateTime;
    std::vector<wchar_t> path(32768);DWORD n=static_cast<DWORD>(path.size());
    require(QueryFullProcessImageNameW(h,0,path.data(),&n),"target image unavailable");
    require(observed==created && !_wcsicmp(std::wstring(path.data(),n).c_str(),image.c_str()),"target incarnation differs");
}
void header(bool synthetic,uint32_t pid,uint64_t created,uint32_t ms,uint64_t freq,uint64_t begin){
    constexpr char source[]=X4_AUDIO_SOURCE_SHA256;
    static_assert(sizeof(source)==65,"source digest length");
    output("X4AUD001",8);output(source,64);
    u32(synthetic?1:0);u32(pid);u64(created);u32(Rate);u32(2);u32(16);u32(ms);u64(freq);u64(begin);
}
void packet(uint32_t frames,uint32_t flags,uint64_t position,uint64_t qpc,const BYTE* data){
    require(frames>0 && frames<=Rate*2 && (flags&~7u)==0,"packet shape unavailable");
    output("P",1);u32(frames);u32(flags);u64(position);u64(qpc);
    if(flags&AUDCLNT_BUFFERFLAGS_SILENT){
        uint8_t zero[4096]{};size_t remaining=size_t(frames)*Align;
        while(remaining){auto n=std::min(remaining,sizeof(zero));output(zero,n);remaining-=n;}
    }else{require(data!=nullptr,"nonsilent packet missing");output(data,size_t(frames)*Align);}
}
void footer(uint32_t packets,uint64_t frames,uint64_t ended){
    output("E",1);u32(packets);u64(frames);u64(ended);
}
class Activation final:public RuntimeClass<RuntimeClassFlags<ClassicCom>,IActivateAudioInterfaceCompletionHandler,FtmBase>{
public:
    Handle ready{CreateEventW(nullptr,TRUE,FALSE,nullptr)};
    ComPtr<IAudioClient> client;HRESULT result=E_PENDING;
    STDMETHOD(ActivateCompleted)(IActivateAudioInterfaceAsyncOperation* operation) override{
        ComPtr<IUnknown> unknown;HRESULT activation=E_FAIL;
        result=operation->GetActivateResult(&activation,&unknown);
        if(SUCCEEDED(result))result=activation;
        if(SUCCEEDED(result))result=unknown.As(&client);
        SetEvent(ready.h);return S_OK;
    }
};
int capture(uint32_t pid,uint64_t created,const std::wstring& image,uint32_t ms){
    Handle target{OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,pid)};
    require(target.h!=nullptr,"target cannot be opened");process(target.h,created,image);
    hr(CoInitializeEx(nullptr,COINIT_MULTITHREADED),"COM unavailable");
    auto handler=Microsoft::WRL::Make<Activation>();
    require(handler && handler->ready.h,"activation event unavailable");
    AUDIOCLIENT_ACTIVATION_PARAMS params{};
    params.ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    params.ProcessLoopbackParams.TargetProcessId=pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT variant{};variant.vt=VT_BLOB;
    variant.blob.cbSize=sizeof(params);variant.blob.pBlobData=reinterpret_cast<BYTE*>(&params);
    ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
    hr(ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,__uuidof(IAudioClient),
       &variant,handler.Get(),&operation),"activation request failed");
    const auto activationStart=GetTickCount64();
    for(;;){
        process(target.h,created,image);
        const DWORD wait=WaitForSingleObject(handler->ready.h,100);
        if(wait==WAIT_OBJECT_0)break;
        require(wait==WAIT_TIMEOUT && GetTickCount64()-activationStart<=10000,"activation timeout/failed");
    }
    hr(handler->result,"activation completion failed");
    auto client=handler->client;require(client!=nullptr,"missing audio client");
    WAVEFORMATEX format{};format.wFormatTag=WAVE_FORMAT_PCM;format.nChannels=2;format.nSamplesPerSec=Rate;
    format.wBitsPerSample=16;format.nBlockAlign=Align;format.nAvgBytesPerSec=Rate*Align;
    hr(client->Initialize(AUDCLNT_SHAREMODE_SHARED,AUDCLNT_STREAMFLAGS_LOOPBACK|
       AUDCLNT_STREAMFLAGS_EVENTCALLBACK|AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,0,0,&format,nullptr),"audio initialize failed");
    Handle samples{CreateEventW(nullptr,FALSE,FALSE,nullptr)};require(samples.h,"sample event unavailable");
    hr(client->SetEventHandle(samples.h),"sample event bind failed");
    ComPtr<IAudioCaptureClient> reader;hr(client->GetService(IID_PPV_ARGS(&reader)),"capture service unavailable");
    UINT32 buffer=0;hr(client->GetBufferSize(&buffer),"buffer size unavailable");
    require(buffer>0 && buffer<=Rate*2,"native buffer bound exceeded");
    LARGE_INTEGER frequency{};require(QueryPerformanceFrequency(&frequency)&&frequency.QuadPart>0,"QPC frequency unavailable");
    process(target.h,created,image);const auto began=ticks();hr(client->Start(),"audio start failed");
    header(false,pid,created,ms,frequency.QuadPart,began);
    uint32_t packets=0;uint64_t frames=0;
    for(;;){
        process(target.h,created,image);
        const auto now=ticks();require(now>=began,"QPC backward");
        if((now-began)*1000/uint64_t(frequency.QuadPart)>=ms)break;
        DWORD wait=WaitForSingleObject(samples.h,100);
        require(wait==WAIT_OBJECT_0 || wait==WAIT_TIMEOUT,"sample wait failed");
        UINT32 available=0;hr(reader->GetNextPacketSize(&available),"packet count failed");
        while(available){
            process(target.h,created,image);
            require(++packets<=MaxPackets && available<=buffer,"packet capacity exceeded");
            BYTE* data=nullptr;UINT32 n=0;DWORD flags=0;UINT64 position=0,qpc=0;
            hr(reader->GetBuffer(&data,&n,&flags,&position,&qpc),"packet read failed");
            try{
                require(n<=buffer && n==available && frames+n<=uint64_t(Rate)*(ms/1000+2),"packet/frame capacity exceeded");
                packet(n,flags,position,qpc,data);frames+=n;
            }catch(...){reader->ReleaseBuffer(n);throw;}
            hr(reader->ReleaseBuffer(n),"packet release failed");
            hr(reader->GetNextPacketSize(&available),"packet recount failed");
            require(ticks()-began<=uint64_t(frequency.QuadPart)*(ms/1000+2),"capture drain deadline");
        }
    }
    hr(client->Stop(),"audio stop failed");process(target.h,created,image);
    footer(packets,frames,ticks());return 0;
}
int wmain(int argc,wchar_t** argv){
    try{
        if(argc==2 && std::wstring(argv[1])==L"--protocol"){output("X4AUD001\n",9);return 0;}
        if(argc==2 && std::wstring(argv[1])==L"--synthetic-fixture"){
            // No COM/process/device API. Tests exercise this exact writer.
            header(true,123,456,1000,10000000,100);
            BYTE samples[8]={0,0,1,0,255,127,0,128};
            packet(2,0,0,100,samples);packet(3,AUDCLNT_BUFFERFLAGS_SILENT|AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY,2,200,nullptr);
            footer(2,5,10000100);return 0;
        }
        require(argc==6 && std::wstring(argv[1])==L"--capture-pcm","explicit capture arguments required");
        const auto pid=number(argv[2]),created=number(argv[3]),ms=number(argv[5]);
        require(pid>0&&pid<=0xffffffffULL&&created>0&&ms>=1000&&ms<=30000&&ms%1000==0,"capture arguments out of bounds");
        require(wcslen(argv[4])>0&&wcslen(argv[4])<32768,"bounded exact target image required");
        return capture(static_cast<uint32_t>(pid),created,argv[4],static_cast<uint32_t>(ms));
    }catch(const std::exception& e){fprintf(stderr,"UNAVAILABLE: %s\n",e.what());return 2;}
}
