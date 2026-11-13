# Native camera observation

`live_camerastate.lua` is a read-only factory accepting `ffi`, `C`, `current` (the canonical public session callback), and `now` (the UI clock). `read()` returns `OBSERVED` or `UNAVAILABLE`; `dispose()` retires it. This standalone module does not register a route or issue native commands.

READ source analogues: `ui/core/lua/targetsystem.lua:73–77,110,1045–1048` declares and consumes `GetCameraRotation().roll`; `ui/addons/ego_detailmonitor/menu_followcamera.lua:8–15,219–224,257–264` reads the external reference and follow-camera base offset and converts that setting to slider percentages. `ui/addons/ego_detailmonitorhelper/helper.lua:43–47` defines `Coord3D` as three floats. Native struct names, offsets, alignment and float32 behavior are checked before reads.

The rotation is a native observation with units and reference frame left UNKNOWN. The follow-camera vector is an **offset setting**, not achieved world position. Neither proves target-relative camera distance, requested orbit achievement, visibility, image content or motion. A zero external reference is recorded explicitly; it is not an empty-world or pose success. Returned handles are observations, not automatically issued query IDs.

Six native reads bracket rotation/offset with external mode/reference checks. Public identity is checked before and after; UI-clock start/end must be finite and nondecreasing. These checks detect sampled changes; they do not prove engine-wide atomicity or exclude a change-and-return between reads. Native read failures yield no partial OBSERVED result. There are no setters, camera switches, filesystem actions or video controls.

Offline synthetic LuaJIT controls qualify protocol and ABI defenses only. Native runtime behavior remains untested. Still-image evidence cannot satisfy a clip requirement.

The fixed public `camera_state` read takes zero arguments. Its source is included
in the bundle identity, its lifetime follows native assembly disposal, and an
UNAVAILABLE payload is preserved under a successful transport response. This
integration passed 158 assembly/bundle/composition controls, zero skips,
resource ff2ef5820bcc; the unchanged standalone observer has 37 controls from
6d699f6d. Independent source and wiring reviews are clear. No runtime case closes
from these checks, and the nested external reference is not issued for actions.
