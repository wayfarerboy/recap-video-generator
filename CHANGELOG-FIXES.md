# Fix log: kdenlive project generation errors

## Errors reported by Kdenlive
1. "Removed clip 04_MetroNova_Loop.wav with no video found in video track V1 00:00:00:00 (chain48)"
2. "V2 00:00:18:00 Timeline clip (chain6) without bin reference found and removed."

## Attempts

### Fix 1 (2026-07-14): Duplicate `kdenlive:id=6`
- **Change**: Master tractor `kdenlive:id` from hardcoded `"6"` → `str(chain_id + 1)` = 50
- **Test**: `TestUniqueIds.test_all_kdenlive_ids_unique`
- **Result**: ⚠️ Necessary but not sufficient

### Fix 2 (2026-07-14): Tractor ordering — qtblend on music tractor
- **Change**: tractor0=music(hide=video), tractor1=video(hide=audio), tractor2=empty(hide=audio)
- **Result**: ⚠️ Video tracks now correct. New errors:
  - "no audio track" in timeline
  - "Incorrect/Invalid composition transition" on all 3 transitions
  - chain48 still "no video found in video track playlist0"

### Fix 3 (2026-07-14): Missing tractor + transition properties
- **Findings**: Working file has properties on tractors and transitions that generated file lacks
- **tractor0 missing**: `kdenlive:audio_track=1` (CRITICAL — without this Kdenlive doesn't render audio track)
- **transitions missing**: `compositing=0`, `distort=0`, `rotate_center=0`, `kdenlive_id`, `internal_added=237` (on qtblend); `kdenlive_id`, `internal_added=237`, `accepts_blanks=1` (on mix)
- **tractor1 missing**: `kdenlive:trackheight`, `kdenlive:timeline_active`, `kdenlive:collapsed=0`
- **tractor2 missing**: `kdenlive:trackheight`, `kdenlive:timeline_active`
- **Change**: Add these properties
- **Result**: TBD
