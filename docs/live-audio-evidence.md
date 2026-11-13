# Bounded process-tree audio evidence

This optional Windows host capability captures PCM from the connected game's process and its descendants. It never launches X4, changes an audio device or volume, plays sound, sends input, or grants recording authorization. Calling code must hold separately approved recording scope. No native recording has been run during development.

The supported native mechanism is Microsoft's [Application Loopback sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) and [process-loopback activation contract](https://learn.microsoft.com/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params). It requires Windows build 20348 or newer. INCLUDE_TARGET_PROCESS_TREE includes descendants: this is neither an exact single-process-only stream nor a system-mix attribution guess. It does not identify a particular voice, weapon, music track, or requested sound.

`connect_audio(client, profile, journal, helper=..., helper_sha256=...)` accepts an existing production AutomationClient/BoundPipe/SessionLink/ProfilePeer. The ordinary journal directory must already exist outside the personal profile tree. A bound native_save_path read verifies the explicit profile route before an exclusive owner journal is created. Linked/reparse routes, changed sources/binary, wrong profile, and existing owner journals refuse. The supplied native helper binary must have a separately qualified SHA256; it is not downloaded, compiled or installed by construction.

`prepare(label, duration=5)` returns an opaque same-owner ticket; the label is a declaration, never proof of the sound subject. Duration is an integral 1–30 seconds. `execute(ticket)` consumes and durably journals that ticket before starting one hidden, shell-free helper child. There is no automatic retry or reopen. Partial output and failed reservations remain quarantined. The helper checks PID, process creation FILETIME, executable image and liveness before activation and while draining packets. The host repeatedly uses the existing bound ping read, which validates the native stamp/build/age and pipe peer. These checks are sampled, not atomic. A load, UI generation change, missing native response, process replacement or observed identity drift aborts; only the owned child may be killed.

The native implementation uses ActivateAudioInterfaceAsync with VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK and the exact process activation structure. It requests the sample's shared-mode, event-driven, automatically converted PCM16 stereo at 44.1 kHz. GetNextPacketSize/GetBuffer/ReleaseBuffer run in one thread. A ten-second activation deadline, capture duration, packet/frame limits and the host timeout bound the operation. The helper writes framed bytes only to stdout and errors to stderr; it never opens an output file. This intentionally replaces the upstream sample's overwrite-capable file writer and unbounded activation wait.

Every packet retains frame count, native flags, device position, 100 ns QPC timestamp and PCM hash. SILENT packets are represented by zero samples as the API specifies, with the SILENT flag retained separately. DATA_DISCONTINUITY and TIMESTAMP_ERROR are retained, never silently repaired or counted as uninterrupted audio. Stream begin/end QPC ticks have their own frequency; native packet timestamps, UI/MD/envelope clocks and source events are not numerically joined. [GetBuffer documentation](https://learn.microsoft.com/windows/win32/api/audioclient/nf-audioclient-iaudiocaptureclient-getbuffer) defines these fields.

`native_timestamp_error_flag_absent` records only the absence of the native TIMESTAMP_ERROR flag. It does not qualify timestamp accuracy or alignment; `native_clock_alignment_qualified` remains false.

A strictly parsed terminal record proves only that this bounded helper window closed and its packet/frame denominators agree. Zero packets means NO_PACKETS_OBSERVED, not proven silence. PCM with zero-valued samples, SILENT-marked packets, nonzero samples, discontinuities and missing packets remain distinguishable. A window need not contain the requested duration's worth of packets. The native transport and recording route still require an approved runtime qualification.

The host exclusively creates a raw `.x4pcm` artifact and a PCM WAV assembled from received packets. WAV concatenation deliberately does not fill gaps or preserve the original timing between packets; retain the raw artifact and receipt for timing/flag analysis. No automatic playback or case verdict occurs. `receipt(ticket)` rehashes artifacts, binary and source dependencies and returns a copied historical record; it does not perform a fresh native read. `export(ticket)` marks it EXPORTED_UNAUTHENTICATED. Local opaque handles are trusted-process provenance, not security against arbitrary same-user code.

Bounds: 16 reservations, 16 MiB raw cap per reservation, 256 MiB conservative retained raw reservation budget, 8,192 packets, at most 44,100 × (duration + 2) frames, 2 MiB/2,048 native identity samples, 64 KiB child stderr. Derived WAV and JSON evidence consume additional bounded space (less than 16 MiB each per reservation); the stated raw budget is not a whole-directory quota. Capture timeout is duration + 15 seconds. Existing evidence is never automatically removed.

## Build and offline qualification

### Started-window orchestration (offline candidate)

`begin(ticket, minimum_remaining=1.0)` consumes the same reservation as `execute`,
starts one owned recorder worker, and returns only after validating its complete
120-byte native start header. The native helper emits that header after
IAudioClient.Start returns. The worker only drains bytes; all client, BoundPipe,
ProfilePeer and native ping checks run on the calling thread. The caller can then
prepare and execute a separately authorized, fixed playback request through that
same AutomationClient. No callback, arbitrary evaluation or playback dispatch is
accepted by the recorder. Preparing the playback after the header avoids spending
its SDK ticket lifetime waiting for audio activation.

`status(ticket)` reports RECORDING or AWAITING_FINISH without restarting or
settling anything. `finish(ticket)` waits for the existing child and requires its
terminal record, the same start-header hash, unchanged journals/source/identity,
and the ordinary complete artifact checks. `cancel(ticket)` cancels only that
owned recording and quarantines its reservation; it never stops independent
game playback. It is cleanup, not a fresh identity or audio-completion proof.
Any separately started looping playback still requires its own qualified stop.

The minimum remaining window is duration minus elapsed host monotonic time since
worker start, conservatively including activation and pipe buffering. It is an
observation, never a future reservation or a clock alignment. A partial or invalid
header, terminal worker, late header, expired publication window, source/identity
drift or worker failure refuses and retains the consumed reservation/artifacts.
The pipe reader can buffer 64 KiB before delivering the header, so a short capture
can finish before readiness and must refuse. Use an explicitly costed duration;
do not infer that all named triggers fit in the unchanged 16-record budget.

Synthetic tests exercise split-header delivery and real SDK/router/client calls
between begin and finish, cancellation, deadline/refusal and tampered settlement.
They do not establish native loopback readiness latency, audible playback, named
attribution, or alignment between QPC, host, MD and UI clocks. The final successor
run passed all 74 controls with zero skips, including the separately hash-verified
compiled helper's synthetic writer and actual bounded subprocess pipe draining
into the started-header sink. Independent production source review is clear. Earlier
synchronous results remain historical; this adds no native audio qualification.

`tools/native_audio/build.py` requires explicit MSVC compiler, VC include/lib paths, Windows SDK root/version and an existing empty output directory. It invokes only the compiler/linker; it never invokes capture. The source hash is embedded at compile time and checked in every stream header. Its receipt pins named compiler binaries and three audio API headers, not complete transitive SDK provenance. No executable or Microsoft source/data is committed.

Tests exercise the actual Lua bound router, BoundPipe, ReadEvidence and host wrapper with explicitly SYNTHETIC packets and process fixtures. Set X4_TEST_AUDIO_HELPER and X4_TEST_AUDIO_HELPER_SHA256 to a qualified compiled helper to exercise its `--synthetic-fixture` writer through the same packet parser; this mode does not initialize COM or touch a process/audio endpoint. Without that explicit dependency, the control reports a skip. `--protocol` also performs no audio operation. `--capture-pcm` must never be used in offline tests.

The first qualification compiled the helper against the Windows SDK, then passed 49 controls with zero skips, including the actual compiled synthetic writer. That establishes offline structure/codec/refusal behavior, not actual loopback sampling or named-sound attribution. Native runtime, audibility at speakers, continuous audio, and audio-case success remain unqualified.

## Native X4 speech events are a different evidence class

READ in the owned game reference: common.xsd event_speak_finished documents interruption status 0/1/2, including a request that never started; page/line precede text-reference resolution. The named native npc_factionrepresentative.xml LineSpoken listener uses an actor-bound event to advance a ceremony. That working example proves a lifecycle listener, not audible samples. UI PlaySound calls likewise request playback. No speech lifecycle observer is installed by this unit.

A bounded effective-corpus discovery attempt reported STALE after local content changed; its historical hit count is not current coverage or an absence proof. Consequently this design makes no engine-wide claim that no other audio observation API exists. The process-loopback choice rests on the documented Windows mechanism and the measured installed FFmpeg device list, whose four inputs lacked process-loopback capture. Neither source validates a particular in-game sound.
